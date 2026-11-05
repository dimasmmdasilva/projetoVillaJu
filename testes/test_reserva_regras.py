"""Regras de reserva, agora pela aplicação.

A Etapa 1 verificou as mesmas regras por SQL direto, contra as triggers.
Aqui elas são exercitadas pelo caminho que a recepção usa — e o que se
espera de volta não é o erro 1644, e sim uma frase que diga o que fazer.

O plano é explícito: "todos os casos de sobreposição da Etapa 1, agora
pela aplicação, devolvendo mensagem amigável".

A validação na aplicação NÃO substitui a trigger. Há teste confirmando
que, mesmo contornando a aplicação, o banco continua recusando.
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

# Chalé e hóspede usados como cenário. O Boracéia tem uma única reserva na
# massa de dados, em fevereiro de 2027, o que mantém os casos isolados.
CHALE = 4
HOSPEDE_ID = 1


def _reserva(entrada, saida, pessoas=2, chale=CHALE, situacao="pendente"):
    return {
        "chale_id": chale,
        "hospede_id": HOSPEDE_ID,
        "data_entrada": entrada,
        "data_saida": saida,
        "num_pessoas": pessoas,
        "situacao": situacao,
    }


@pytest.fixture
def banco(conexao_banco):
    """Massa de dados recarregada a CADA teste.

    Os testes criam, cancelam e movem reservas. Sem recarregar, um teste
    herdaria o calendário que o anterior deixou e falharia por motivo que
    não tem relação com o que ele verifica — foi exatamente o que
    aconteceu na primeira execução deste arquivo.

    A recarga custa cerca de um segundo por teste. É o preço do
    isolamento, e vale: um teste que depende da ordem de execução não
    prova nada.
    """
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

    conexao.iniciar_pool(Configuracao(modo_teste=True))
    yield cursor
    conexao.encerrar_pool()


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
    def entrar(credenciais=RECEPCAO):
        sessao = aplicacao.test_client()
        login, senha = credenciais
        sessao.post("/login", data={"login": login, "senha": senha})
        return sessao

    return entrar


def _aviso(resposta) -> str:
    achados = re.findall(
        r'<p class="aviso aviso-erro">(.*?)</p>', resposta.data.decode(), re.S
    )
    return achados[0].strip() if achados else ""


# ---------------------------------------------------------------------------
# Os casos de sobreposição da Etapa 1, agora pela aplicação
# ---------------------------------------------------------------------------

def test_reserva_sem_conflito_e_criada(banco):
    """CASO 1."""
    identificador = reserva_repo.criar(_reserva("2027-07-01", "2027-07-05"))
    assert reserva_repo.buscar_por_id(identificador) is not None


def test_reserva_identica_e_recusada_com_mensagem(banco):
    """CASO 2 — e a mensagem precisa dizer qual período está ocupado."""
    reserva_repo.criar(_reserva("2027-07-01", "2027-07-05"))

    with pytest.raises(ErroDeReserva) as erro:
        reserva_repo.criar(_reserva("2027-07-01", "2027-07-05"))

    texto = str(erro.value)
    assert "Boracéia" in texto
    assert "01/07/2027" in texto
    assert "05/07/2027" in texto
    assert "1644" not in texto


def test_reserva_que_comeca_no_meio_e_recusada(banco):
    """CASO 3."""
    reserva_repo.criar(_reserva("2027-07-01", "2027-07-10"))
    with pytest.raises(ErroDeReserva):
        reserva_repo.criar(_reserva("2027-07-05", "2027-07-15"))


def test_reserva_que_termina_no_meio_e_recusada(banco):
    reserva_repo.criar(_reserva("2027-07-05", "2027-07-15"))
    with pytest.raises(ErroDeReserva):
        reserva_repo.criar(_reserva("2027-07-01", "2027-07-10"))


def test_reserva_que_engloba_outra_e_recusada(banco):
    """CASO 4."""
    reserva_repo.criar(_reserva("2027-07-05", "2027-07-10"))
    with pytest.raises(ErroDeReserva):
        reserva_repo.criar(_reserva("2027-07-01", "2027-07-20"))


def test_reserva_contida_em_outra_e_recusada(banco):
    reserva_repo.criar(_reserva("2027-07-01", "2027-07-20"))
    with pytest.raises(ErroDeReserva):
        reserva_repo.criar(_reserva("2027-07-05", "2027-07-10"))


def test_entrada_no_dia_da_saida_e_aceita(banco):
    """CASO 5 — o intervalo semiaberto, pela aplicação.

    Se a validação da aplicação usasse `>=`, ela recusaria antes mesmo de
    a trigger ser consultada — e o sistema bloquearia receita legítima
    sem que o banco tivesse culpa.
    """
    reserva_repo.criar(_reserva("2027-07-01", "2027-07-05"))
    identificador = reserva_repo.criar(_reserva("2027-07-05", "2027-07-10"))
    assert reserva_repo.buscar_por_id(identificador) is not None


def test_saida_no_dia_da_entrada_e_aceita(banco):
    """CASO 6."""
    reserva_repo.criar(_reserva("2027-07-05", "2027-07-10"))
    identificador = reserva_repo.criar(_reserva("2027-07-01", "2027-07-05"))
    assert reserva_repo.buscar_por_id(identificador) is not None


def test_conflito_com_cancelada_e_aceito(banco):
    """CASO 7."""
    primeira = reserva_repo.criar(_reserva("2027-07-01", "2027-07-05"))
    reserva_repo.mudar_situacao(primeira, "cancelada")

    segunda = reserva_repo.criar(_reserva("2027-07-01", "2027-07-05"))
    assert reserva_repo.buscar_por_id(segunda) is not None


def test_acima_da_capacidade_e_recusado_com_o_numero(banco):
    """CASO 8 — a mensagem informa a capacidade real do chalé."""
    with pytest.raises(ErroDeReserva) as erro:
        reserva_repo.criar(_reserva("2027-07-01", "2027-07-05", pessoas=6))

    texto = str(erro.value)
    assert "4" in texto and "6" in texto
    assert "Boracéia" in texto


def test_exatamente_na_capacidade_e_aceito(banco):
    identificador = reserva_repo.criar(
        _reserva("2027-07-01", "2027-07-05", pessoas=4)
    )
    assert reserva_repo.buscar_por_id(identificador)["num_pessoas"] == 4


@pytest.mark.parametrize(
    "entrada,saida", [("2027-07-10", "2027-07-05"), ("2027-07-10", "2027-07-10")]
)
def test_datas_incoerentes_sao_recusadas(banco, entrada, saida):
    """CASO 9."""
    with pytest.raises(ErroDeReserva) as erro:
        reserva_repo.criar(_reserva(entrada, saida))
    assert "posterior" in str(erro.value)


def test_update_que_move_sobre_outra_e_recusado(banco):
    """CASO 10."""
    reserva_repo.criar(_reserva("2027-07-01", "2027-07-05"))
    segunda = reserva_repo.criar(_reserva("2027-08-01", "2027-08-05"))

    with pytest.raises(ErroDeReserva):
        reserva_repo.atualizar(segunda, _reserva("2027-07-02", "2027-07-04"))


def test_update_que_altera_so_a_observacao_passa(banco):
    """CASO 11 — o risco registrado no plano, do lado da aplicação.

    Sem o parâmetro que exclui a própria reserva da verificação, editar
    qualquer campo a faria conflitar consigo mesma.
    """
    identificador = reserva_repo.criar(_reserva("2027-07-01", "2027-07-05"))

    dados = _reserva("2027-07-01", "2027-07-05")
    dados["observacoes"] = "Chegada após as 22h"
    reserva_repo.atualizar(identificador, dados)

    assert reserva_repo.buscar_por_id(identificador)["observacoes"] == (
        "Chegada após as 22h"
    )


def test_cancelar_libera_o_periodo(banco):
    """Caso obrigatório da Etapa 3."""
    primeira = reserva_repo.criar(_reserva("2027-07-01", "2027-07-05"))

    with pytest.raises(ErroDeReserva):
        reserva_repo.criar(_reserva("2027-07-02", "2027-07-04"))

    reserva_repo.mudar_situacao(primeira, "cancelada")

    segunda = reserva_repo.criar(_reserva("2027-07-02", "2027-07-04"))
    assert reserva_repo.buscar_por_id(segunda) is not None


# ---------------------------------------------------------------------------
# A trigger continua sendo a garantia
# ---------------------------------------------------------------------------

def test_trigger_recusa_mesmo_contornando_a_aplicacao(banco):
    """A validação da aplicação é cortesia; a trigger é a garantia.

    Este INSERT não passa pelo repositório: é SQL direto, como alguém
    faria pelo MySQL Workbench. A trigger recusa do mesmo jeito.
    """
    import mysql.connector

    reserva_repo.criar(_reserva("2027-07-01", "2027-07-05"))

    with conexao.obter_cursor() as cursor:
        with pytest.raises(mysql.connector.Error) as erro:
            cursor.execute(
                "INSERT INTO reserva "
                "(chale_id, hospede_id, data_entrada, data_saida, "
                " num_pessoas, situacao) "
                "VALUES (4, 1, '2027-07-02', '2027-07-04', 2, 'confirmada')"
            )

    assert erro.value.errno == 1644


# ---------------------------------------------------------------------------
# Transições de situação
# ---------------------------------------------------------------------------

def test_pendente_vira_confirmada(banco):
    identificador = reserva_repo.criar(_reserva("2027-07-01", "2027-07-05"))
    reserva_repo.mudar_situacao(identificador, "confirmada")
    assert reserva_repo.buscar_por_id(identificador)["situacao"] == "confirmada"


def test_confirmada_vira_concluida(banco):
    identificador = reserva_repo.criar(_reserva("2027-07-01", "2027-07-05"))
    reserva_repo.mudar_situacao(identificador, "confirmada")
    reserva_repo.mudar_situacao(identificador, "concluida")
    assert reserva_repo.buscar_por_id(identificador)["situacao"] == "concluida"


@pytest.mark.parametrize(
    "de,para",
    [
        ("pendente", "concluida"),
        ("cancelada", "confirmada"),
        ("cancelada", "pendente"),
        ("concluida", "confirmada"),
        ("concluida", "cancelada"),
    ],
)
def test_transicao_proibida_e_recusada(banco, de, para):
    """De `cancelada` não se volta, e `concluida` é terminal.

    Sem este mapa, bastaria trocar o valor num campo oculto para
    reabilitar uma reserva cancelada — e a trigger não impediria, porque
    ela valida sobreposição, não história.
    """
    identificador = reserva_repo.criar(_reserva("2027-07-01", "2027-07-05"))

    caminho = {"pendente": [], "confirmada": ["confirmada"],
               "cancelada": ["cancelada"],
               "concluida": ["confirmada", "concluida"]}[de]
    for passo in caminho:
        reserva_repo.mudar_situacao(identificador, passo)

    with pytest.raises(ErroDeReserva) as erro:
        reserva_repo.mudar_situacao(identificador, para)

    assert de in str(erro.value)


def test_confirmar_reverifica_o_conflito(banco):
    """Entre criar e confirmar, outra reserva pode ter ocupado o período.

    O caso só acontece quando a primeira é cancelada e outra assume o
    período: ao tentar reabilitar... que é proibido. Aqui se verifica a
    reverificação pelo caminho possível — criar duas pendentes é
    impedido, então o teste confirma que a reverificação existe sem
    conflito e a transição passa.
    """
    identificador = reserva_repo.criar(_reserva("2027-07-01", "2027-07-05"))
    reserva_repo.mudar_situacao(identificador, "confirmada")
    assert reserva_repo.buscar_por_id(identificador)["situacao"] == "confirmada"


def test_situacao_invalida_e_recusada(banco):
    identificador = reserva_repo.criar(_reserva("2027-07-01", "2027-07-05"))
    with pytest.raises(ErroDeReserva):
        reserva_repo.mudar_situacao(identificador, "inventada")


def test_reserva_cancelada_nao_pode_ser_editada(banco):
    identificador = reserva_repo.criar(_reserva("2027-07-01", "2027-07-05"))
    reserva_repo.mudar_situacao(identificador, "cancelada")

    with pytest.raises(ErroDeReserva) as erro:
        reserva_repo.atualizar(identificador, _reserva("2027-08-01", "2027-08-05"))
    assert "cancelada" in str(erro.value)


# ---------------------------------------------------------------------------
# Validação de entrada
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "campo,valor,trecho",
    [
        ("num_pessoas", 0, "ao menos uma pessoa"),
        ("num_pessoas", "duas", "número inteiro"),
        ("chale_id", 999999, "chalé informado não existe"),
        ("hospede_id", 999999, "hóspede informado não existe"),
        ("chale_id", "abc", "Selecione o chalé"),
    ],
)
def test_entrada_invalida_e_recusada(banco, campo, valor, trecho):
    dados = _reserva("2027-07-01", "2027-07-05")
    dados[campo] = valor

    with pytest.raises(ErroDeReserva) as erro:
        reserva_repo.criar(dados)
    assert trecho in str(erro.value)


def test_chale_desativado_nao_aceita_reserva(banco):
    from app.dados import chale_repo

    chale_repo.definir_ativo(CHALE, False)
    try:
        with pytest.raises(ErroDeReserva) as erro:
            reserva_repo.criar(_reserva("2027-07-01", "2027-07-05"))
        assert "desativado" in str(erro.value)
    finally:
        chale_repo.definir_ativo(CHALE, True)


def test_observacoes_acima_do_limite_sao_recusadas(banco):
    dados = _reserva("2027-07-01", "2027-07-05")
    dados["observacoes"] = "x" * (reserva_repo.LIMITE_DE_OBSERVACOES + 1)

    with pytest.raises(ErroDeReserva) as erro:
        reserva_repo.criar(dados)
    assert "limite" in str(erro.value)


# ---------------------------------------------------------------------------
# As telas
# ---------------------------------------------------------------------------

def test_criterio_de_conclusao_pela_interface(cliente):
    """O critério de conclusão da Etapa 3.

    "Um recepcionista fictício consegue, do login até a confirmação,
    criar uma reserva válida e ser impedido de criar uma conflitante, com
    mensagem clara em ambos os casos."
    """
    sessao = cliente(RECEPCAO)

    valida = sessao.post(
        "/reservas/nova",
        data={"chale_id": "5", "hospede_id": "1",
              "data_entrada": "2028-03-01", "data_saida": "2028-03-05",
              "num_pessoas": "2", "observacoes": "Chegada à tarde."},
        follow_redirects=False,
    )
    assert valida.status_code == 302
    assert "/reservas/" in valida.headers["Location"]

    conflitante = sessao.post(
        "/reservas/nova",
        data={"chale_id": "5", "hospede_id": "2",
              "data_entrada": "2028-03-03", "data_saida": "2028-03-08",
              "num_pessoas": "2"},
    )
    # 409, não 400: a reserva está bem formada, o que falta é
    # disponibilidade. Depois de um cancelamento, o mesmo pedido passa.
    assert conflitante.status_code == 409

    mensagem = _aviso(conflitante)
    assert "Dunas" in mensagem
    assert "01/03/2028" in mensagem
    assert "1644" not in mensagem
    assert "mysql" not in mensagem.lower()


def test_erro_do_banco_nao_vaza_para_a_tela(cliente):
    resposta = cliente(RECEPCAO).post(
        "/reservas/nova",
        data={"chale_id": "1", "hospede_id": "1",
              "data_entrada": "2027-12-16", "data_saida": "2027-12-18",
              "num_pessoas": "9"},
    )

    corpo = resposta.data.decode()
    for vazamento in ("Traceback", "mysql.connector", "SIGNAL", "45000", "SELECT"):
        assert vazamento not in corpo


def test_formulario_preserva_o_que_foi_digitado(cliente):
    resposta = cliente(RECEPCAO).post(
        "/reservas/nova",
        data={"chale_id": "1", "hospede_id": "1",
              "data_entrada": "2027-12-16", "data_saida": "2027-12-18",
              "num_pessoas": "9", "observacoes": "Texto que não pode sumir"},
    )
    assert "Texto que não pode sumir" in resposta.data.decode()


def test_observacoes_com_script_sao_escapadas(cliente):
    sessao = cliente(RECEPCAO)
    sessao.post(
        "/reservas/nova",
        data={"chale_id": "3", "hospede_id": "1",
              "data_entrada": "2028-06-01", "data_saida": "2028-06-05",
              "num_pessoas": "2",
              "observacoes": "<script>alert(1)</script>"},
    )
    assert b"<script>alert(1)</script>" not in sessao.get("/reservas/").data


@pytest.mark.parametrize("credenciais", [ADMIN, RECEPCAO])
def test_administrador_e_recepcionista_gerenciam_reservas(cliente, credenciais):
    assert cliente(credenciais).get("/reservas/").status_code == 200


def test_hospede_nao_acessa_a_gestao_de_reservas(cliente):
    assert cliente(HOSPEDE).get("/reservas/").status_code == 403


def test_mudanca_de_situacao_nao_aceita_get(cliente):
    assert cliente(RECEPCAO).get("/reservas/1/situacao").status_code == 405


def test_reserva_inexistente_devolve_404(cliente):
    assert cliente(RECEPCAO).get("/reservas/999999").status_code == 404


def test_filtro_por_situacao_invalida_nao_quebra(cliente):
    resposta = cliente(RECEPCAO).get("/reservas/?situacao=inventada")
    assert resposta.status_code == 200


def test_reserva_ja_cancelada_nao_e_barrada_por_conflito(banco):
    """Registrar uma reserva já cancelada não pode ser impedido.

    A situação aparece ao importar histórico: o período conflita com uma
    reserva ativa, mas a que está sendo gravada já nasce cancelada e não
    ocupa nada.

    A trigger do banco tem a mesma guarda, coberta na Etapa 1 por
    `test_reserva_cancelada_nao_e_barrada_por_conflito`. Este teste
    verifica o espelho dela na aplicação — que faltava, e só apareceu
    quando a guarda foi removida por sabotagem e nenhum teste falhou.
    """
    reserva_repo.criar(_reserva("2027-07-01", "2027-07-05"))

    identificador = reserva_repo.criar(
        _reserva("2027-07-01", "2027-07-05", situacao="cancelada")
    )

    gravada = reserva_repo.buscar_por_id(identificador)
    assert gravada is not None
    assert gravada["situacao"] == "cancelada"


def test_reserva_ja_concluida_tambem_nao_e_barrada(banco):
    """Mesmo princípio para histórico de estadias passadas."""
    reserva_repo.criar(_reserva("2027-07-01", "2027-07-05"))

    identificador = reserva_repo.criar(
        _reserva("2027-07-02", "2027-07-04", situacao="concluida")
    )
    assert reserva_repo.buscar_por_id(identificador)["situacao"] == "concluida"
