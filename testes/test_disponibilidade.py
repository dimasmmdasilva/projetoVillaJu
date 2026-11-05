"""Consulta de disponibilidade por período.

A pergunta "quais chalés estão livres de 10 a 15 de dezembro" não é
respondida pela trigger: ela só reage a uma gravação. Esta consulta
repete a MESMA comparação da trigger, letra por letra, para responder por
leitura.

O calendário da massa de dados, que os testes usam como cenário:

    Embaré     15 a 20/12/2026  confirmada
    Embaré     20 a 27/12/2026  confirmada   (encostada na anterior)
    Camburi    10 a 18/12/2026  confirmada   (cruza a virada de temporada)
    Asturias   05 a 12/01/2027  cancelada
    Asturias   05 a 12/01/2027  confirmada   (mesmo período da cancelada)
    Boracéia   10 a 15/02/2027  pendente
    Dunas      04 a 11/07/2026  concluída
"""

import datetime as dt
import re

import pytest

from app import create_app
from app.config import Configuracao
from app.dados import conexao, reserva_repo
from app.dados.reserva_repo import ErroDeReserva

pytestmark = pytest.mark.banco

ADMIN = ("admin", "Villa@Admin2026")
RECEPCAO = ("recepcao", "Villa@Recep2026")
HOSPEDE = ("joao", "Villa@Hospede2026")

TOTAL_DE_CHALES = 5

# Período sem reserva nenhuma, em qualquer chalé.
LIVRE = ("2027-07-01", "2027-07-05")

# Boracéia está reservada de 10 a 15/02/2027, e é a única nesse mês.
BORACEIA_ENTRADA = dt.date(2027, 2, 10)
BORACEIA_SAIDA = dt.date(2027, 2, 15)


@pytest.fixture(scope="module")
def aplicacao(conexao_banco):
    from conftest import (
        SCRIPT_DADOS,
        SCRIPT_SCHEMA,
        SCRIPT_TRIGGERS,
        aplicar_script,
        limpar_banco,
    )

    cursor = conexao_banco.cursor()
    limpar_banco(cursor)
    aplicar_script(cursor, SCRIPT_SCHEMA)
    aplicar_script(cursor, SCRIPT_TRIGGERS)
    aplicar_script(cursor, SCRIPT_DADOS)
    cursor.close()

    app = create_app(Configuracao(modo_teste=True))
    yield app
    conexao.encerrar_pool()


@pytest.fixture
def cliente(aplicacao):
    def entrar(credenciais=ADMIN):
        sessao = aplicacao.test_client()
        login, senha = credenciais
        sessao.post("/login", data={"login": login, "senha": senha})
        return sessao

    return entrar


def _nomes_livres(resposta) -> list:
    corpo = resposta.data.decode()
    bloco = corpo.split("Livres no período")[1].split("Ocupados")[0]
    return re.findall(r"<tr>\s*<td>([^<]+)</td>", bloco)


def _aviso(resposta) -> str:
    achados = re.findall(
        r'<p class="aviso aviso-erro">(.*?)</p>', resposta.data.decode(), re.S
    )
    return achados[0].strip() if achados else ""


# ---------------------------------------------------------------------------
# Os três casos obrigatórios da Etapa 3
# ---------------------------------------------------------------------------

def test_periodo_sem_reserva_retorna_os_cinco_chales(aplicacao):
    """Caso obrigatório 1."""
    livres = reserva_repo.chales_disponiveis(*LIVRE)
    assert len(livres) == TOTAL_DE_CHALES


def test_periodo_com_um_chale_ocupado_retorna_quatro(aplicacao):
    """Caso obrigatório 2.

    O cenário precisa ser isolado: em dezembro há dois chalés reservados
    ao mesmo tempo, e usar esse mês daria quatro livres por motivo
    errado. Fevereiro de 2027 tem apenas a reserva do Boracéia.
    """
    livres = reserva_repo.chales_disponiveis("2027-02-11", "2027-02-13")
    nomes = {chale["nome"] for chale in livres}

    assert len(livres) == TOTAL_DE_CHALES - 1
    assert "Boracéia" not in nomes


def test_periodo_que_cruza_parcialmente_nao_mostra_o_chale(aplicacao):
    """Caso obrigatório 3.

    A consulta vai de 14 a 18/02; a reserva do Boracéia vai de 10 a 15.
    A sobreposição é de um único dia, e basta para o chalé sumir da lista.
    """
    nomes = {c["nome"] for c in reserva_repo.chales_disponiveis("2027-02-14", "2027-02-18")}
    assert "Boracéia" not in nomes
    assert len(nomes) == TOTAL_DE_CHALES - 1


# ---------------------------------------------------------------------------
# Intervalo semiaberto
# ---------------------------------------------------------------------------

def test_entrada_no_dia_da_saida_mostra_o_chale_livre(aplicacao):
    """A mesma regra da trigger, do lado da consulta.

    O Boracéia sai dia 15. Quem entra dia 15 encontra o chalé livre — é
    como a hospedaria opera, com saída pela manhã e entrada à tarde.
    """
    nomes = {c["nome"] for c in reserva_repo.chales_disponiveis("2027-02-15", "2027-02-17")}
    assert "Boracéia" in nomes


def test_saida_no_dia_da_entrada_mostra_o_chale_livre(aplicacao):
    nomes = {c["nome"] for c in reserva_repo.chales_disponiveis("2027-02-08", "2027-02-10")}
    assert "Boracéia" in nomes


def test_um_dia_dentro_do_periodo_ja_ocupa(aplicacao):
    """A fronteira: 14 a 16 sobrepõe a reserva que vai até 15."""
    nomes = {c["nome"] for c in reserva_repo.chales_disponiveis("2027-02-14", "2027-02-16")}
    assert "Boracéia" not in nomes


# ---------------------------------------------------------------------------
# Situação da reserva
# ---------------------------------------------------------------------------

def test_reserva_cancelada_nao_ocupa(aplicacao):
    """O Dunas tem reserva concluída em julho de 2026."""
    nomes = {c["nome"] for c in reserva_repo.chales_disponiveis("2026-07-05", "2026-07-08")}
    assert "Dunas" in nomes, "reserva concluída não deveria ocupar"


def test_reserva_pendente_ocupa(aplicacao):
    """Pendente ainda não foi confirmada, mas já segura o chalé."""
    nomes = {c["nome"] for c in reserva_repo.chales_disponiveis("2027-02-11", "2027-02-13")}
    assert "Boracéia" not in nomes


def test_chale_com_cancelada_e_confirmada_no_mesmo_periodo_fica_ocupado(aplicacao):
    """O Asturias tem as duas. A confirmada é que vale."""
    nomes = {c["nome"] for c in reserva_repo.chales_disponiveis("2027-01-06", "2027-01-08")}
    assert "Asturias" not in nomes


# ---------------------------------------------------------------------------
# Chalé desativado
# ---------------------------------------------------------------------------

def test_chale_desativado_nao_aparece_mesmo_livre(aplicacao):
    """Oferecê-lo produziria reserva que a recepção teria de desfazer."""
    from app.dados import chale_repo

    chale_repo.definir_ativo(5, False)
    try:
        nomes = {c["nome"] for c in reserva_repo.chales_disponiveis(*LIVRE)}
        assert "Dunas" not in nomes
        assert len(nomes) == TOTAL_DE_CHALES - 1
    finally:
        chale_repo.definir_ativo(5, True)


# ---------------------------------------------------------------------------
# O complemento: chalés ocupados
# ---------------------------------------------------------------------------

def test_ocupados_traz_a_reserva_que_bloqueia(aplicacao):
    ocupados = reserva_repo.chales_ocupados("2027-02-11", "2027-02-13")

    assert len(ocupados) == 1
    assert ocupados[0]["nome"] == "Boracéia"
    assert ocupados[0]["data_entrada"] == BORACEIA_ENTRADA
    assert ocupados[0]["data_saida"] == BORACEIA_SAIDA


def test_livres_e_ocupados_somam_o_total(aplicacao):
    livres = reserva_repo.chales_disponiveis("2026-12-16", "2026-12-17")
    ocupados = reserva_repo.chales_ocupados("2026-12-16", "2026-12-17")

    assert len(livres) + len({o["id"] for o in ocupados}) == TOTAL_DE_CHALES


# ---------------------------------------------------------------------------
# Validação do período
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "entrada,saida,trecho",
    [
        ("2027-01-10", "2027-01-05", "posterior"),
        ("2027-01-10", "2027-01-10", "posterior"),
        ("", "2027-01-10", "obrigatória"),
        ("2027-01-10", "", "obrigatória"),
        ("10/01/2027", "2027-01-15", "AAAA-MM-DD"),
        ("2027-13-01", "2027-01-15", "AAAA-MM-DD"),
        ("2027-01-01", "2027-12-31", "máximo"),
    ],
)
def test_periodo_invalido_e_recusado(aplicacao, entrada, saida, trecho):
    with pytest.raises(ErroDeReserva) as erro:
        reserva_repo.chales_disponiveis(entrada, saida)
    assert trecho in str(erro.value)


def test_periodo_no_limite_de_diarias_e_aceito(aplicacao):
    inicio = dt.date(2028, 1, 1)
    fim = inicio + dt.timedelta(days=reserva_repo.MAXIMO_DE_DIARIAS)
    assert reserva_repo.chales_disponiveis(inicio, fim)


def test_aceita_objeto_date_alem_de_texto(aplicacao):
    """A rota passa texto; o repositório e os testes passam `date`."""
    assert reserva_repo.chales_disponiveis(
        dt.date(2027, 7, 1), dt.date(2027, 7, 5)
    )


# ---------------------------------------------------------------------------
# Conflitos para um chalé específico
# ---------------------------------------------------------------------------

def test_conflitos_lista_a_reserva_sobreposta(aplicacao):
    achados = reserva_repo.conflitos(4, "2027-02-11", "2027-02-13")
    assert len(achados) == 1
    assert achados[0]["situacao"] == "pendente"


def test_conflitos_vazio_em_periodo_livre(aplicacao):
    assert reserva_repo.conflitos(4, *LIVRE) == []


def test_conflitos_ignora_a_propria_reserva(aplicacao):
    """O risco que o plano registra para a trigger de UPDATE.

    Sem o parâmetro de exclusão, editar uma reserva a faria conflitar
    consigo mesma e qualquer alteração seria recusada.
    """
    with conexao.obter_cursor() as cursor:
        cursor.execute(
            "SELECT id FROM reserva WHERE chale_id = 4 AND situacao = 'pendente'"
        )
        identificador = cursor.fetchone()[0]

    assert reserva_repo.conflitos(4, "2027-02-11", "2027-02-13")
    assert reserva_repo.conflitos(
        4, "2027-02-11", "2027-02-13", ignorar_id=identificador
    ) == []


def test_chale_disponivel_responde_o_mesmo_que_conflitos(aplicacao):
    assert reserva_repo.chale_disponivel(4, *LIVRE) is True
    assert reserva_repo.chale_disponivel(4, "2027-02-11", "2027-02-13") is False


# ---------------------------------------------------------------------------
# A tela
# ---------------------------------------------------------------------------

def test_tela_abre_sem_periodo_informado(cliente):
    """Sem data, o formulário aparece vazio em vez de assumir um período.

    Adivinhar o que a recepção quer consultar produziria uma resposta que
    parece informação e não é.
    """
    resposta = cliente().get("/reservas/disponibilidade")
    assert resposta.status_code == 200
    assert "Livres no período" not in resposta.data.decode()


def test_tela_mostra_os_cinco_em_periodo_livre(cliente):
    resposta = cliente().get(
        f"/reservas/disponibilidade?entrada={LIVRE[0]}&saida={LIVRE[1]}"
    )
    assert len(_nomes_livres(resposta)) == TOTAL_DE_CHALES


def test_tela_mostra_mensagem_para_periodo_invalido(cliente):
    resposta = cliente().get(
        "/reservas/disponibilidade?entrada=2027-01-10&saida=2027-01-05"
    )
    assert resposta.status_code == 400
    assert "posterior" in _aviso(resposta)


def test_tela_nao_vaza_erro_tecnico(cliente):
    corpo = cliente().get(
        "/reservas/disponibilidade?entrada=errado&saida=errado"
    ).data.decode()

    for vazamento in ("Traceback", "mysql", "ValueError", "SELECT"):
        assert vazamento not in corpo


@pytest.mark.parametrize("credenciais", [ADMIN, RECEPCAO])
def test_administrador_e_recepcionista_consultam(cliente, credenciais):
    assert cliente(credenciais).get("/reservas/disponibilidade").status_code == 200


def test_hospede_nao_consulta_disponibilidade(cliente):
    """O perfil hospede vê apenas as próprias reservas."""
    assert cliente(HOSPEDE).get("/reservas/disponibilidade").status_code == 403


def test_sem_sessao_leva_ao_login(aplicacao):
    resposta = aplicacao.test_client().get(
        "/reservas/disponibilidade", follow_redirects=False
    )
    assert resposta.status_code == 302
    assert "/login" in resposta.headers["Location"]
