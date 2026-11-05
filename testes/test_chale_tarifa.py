"""Cadastro de chalés e tarifas.

Dois alvos. Os repositórios, que validam antes de gravar e traduzem os
erros do banco em mensagens legíveis. E as rotas, restritas ao perfil
administrador conforme a seção 3.3 do plano.

A validação na aplicação não substitui a do banco: ela antecipa, para
produzir mensagem amigável em vez de deixar um erro de restrição subir
até a tela. A garantia continua sendo das triggers e das restrições.
"""

import datetime as dt
from decimal import Decimal

import pytest

from app import create_app
from app.config import Configuracao
from app.dados import chale_repo, conexao, tarifa_repo
from app.dados.erros import (
    ErroDeDados,
    RegistroDuplicado,
    RegraDoBanco,
    traduzir,
)

pytestmark = pytest.mark.banco

ADMIN = ("admin", "Villa@Admin2026")
RECEPCAO = ("recepcao", "Villa@Recep2026")
HOSPEDE = ("joao", "Villa@Hospede2026")

CHALE_NOVO = {
    "nome": "Mirante",
    "capacidade": 4,
    "configuracao_camas": "2 camas de casal",
    "comodidades": "Wi-Fi, varanda",
    "descricao": "Vista para a mata.",
}

TARIFA_NOVA = {
    "data_inicio": "2027-06-01",
    "data_fim": "2027-06-30",
    "valor_diaria": "450.00",
    "descricao": "Inverno",
}


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
        cliente = aplicacao.test_client()
        login, senha = credenciais
        cliente.post("/login", data={"login": login, "senha": senha})
        return cliente

    return entrar


def _aviso(resposta) -> str:
    import re

    achados = re.findall(
        r'<p class="aviso aviso-erro">(.*?)</p>', resposta.data.decode(), re.S
    )
    return achados[0].strip() if achados else ""


# ---------------------------------------------------------------------------
# Tradução dos erros do banco
# ---------------------------------------------------------------------------

def test_signal_da_trigger_vira_mensagem_legivel():
    """A mensagem da trigger foi escrita em português, para ser exibida."""
    import mysql.connector

    erro = mysql.connector.Error(
        msg="Já existe reserva ativa para este chalé no período informado.",
        errno=1644,
    )
    traduzido = traduzir(erro)

    assert isinstance(traduzido, RegraDoBanco)
    assert "reserva ativa" in str(traduzido)


def test_duplicidade_vira_mensagem_generica():
    import mysql.connector

    traduzido = traduzir(mysql.connector.Error(msg="Duplicate entry", errno=1062))
    assert isinstance(traduzido, RegistroDuplicado)


def test_erro_desconhecido_nao_revela_detalhe():
    """Mostrar o erro cru entregaria tabela, coluna e tecnologia."""
    import mysql.connector

    traduzido = traduzir(
        mysql.connector.Error(
            msg="Table 'villaju.segredo' doesn't exist", errno=1146
        )
    )

    texto = str(traduzido)
    assert "villaju" not in texto
    assert "Table" not in texto
    assert texto == "Não foi possível concluir a operação."


# ---------------------------------------------------------------------------
# Repositório de chalés
# ---------------------------------------------------------------------------

def test_listar_traz_os_cinco_chales(aplicacao):
    nomes = {c["nome"] for c in chale_repo.listar()}
    assert {"Embaré", "Camburi", "Asturias", "Boracéia", "Dunas"} <= nomes


def test_criar_e_buscar(aplicacao):
    identificador = chale_repo.criar(dict(CHALE_NOVO, nome="Criado no teste"))
    lido = chale_repo.buscar_por_id(identificador)

    assert lido["nome"] == "Criado no teste"
    assert lido["capacidade"] == 4
    assert lido["ativo"] == 1

    chale_repo.atualizar(identificador, dict(CHALE_NOVO, nome="Renomeado"))
    assert chale_repo.buscar_por_id(identificador)["nome"] == "Renomeado"


def test_carimbo_de_tempo_e_preenchido_pela_trigger(aplicacao):
    identificador = chale_repo.criar(dict(CHALE_NOVO, nome="Com carimbo"))
    lido = chale_repo.buscar_por_id(identificador)

    assert isinstance(lido["criado_em"], dt.datetime)
    assert isinstance(lido["alterado_em"], dt.datetime)


@pytest.mark.parametrize(
    "dados,trecho",
    [
        ({"nome": "", "capacidade": 4}, "obrigatório"),
        ({"nome": "   ", "capacidade": 4}, "obrigatório"),
        ({"nome": "x" * 51, "capacidade": 4}, "limite"),
        ({"nome": "X", "capacidade": 0}, "entre 1 e 4"),
        ({"nome": "X", "capacidade": 5}, "entre 1 e 4"),
        ({"nome": "X", "capacidade": 40}, "entre 1 e 4"),
        ({"nome": "X", "capacidade": -1}, "entre 1 e 4"),
        ({"nome": "X", "capacidade": "muitas"}, "número inteiro"),
        ({"nome": "X", "capacidade": None}, "número inteiro"),
    ],
)
def test_dado_invalido_e_recusado_com_mensagem(aplicacao, dados, trecho):
    """A recusa acontece na aplicação, antes de chegar ao banco."""
    with pytest.raises(chale_repo.ErroDeChale) as erro:
        chale_repo.criar(dados)
    assert trecho in str(erro.value)


def test_nome_duplicado_e_recusado(aplicacao):
    with pytest.raises(RegistroDuplicado):
        chale_repo.criar(dict(CHALE_NOVO, nome="Embaré"))


def test_desativar_nao_apaga(aplicacao):
    """Apagar perderia o histórico de reservas."""
    identificador = chale_repo.criar(dict(CHALE_NOVO, nome="Para desativar"))

    chale_repo.definir_ativo(identificador, False)
    assert chale_repo.buscar_por_id(identificador)["ativo"] == 0

    ativos = {c["id"] for c in chale_repo.listar(apenas_ativos=True)}
    assert identificador not in ativos

    todos = {c["id"] for c in chale_repo.listar()}
    assert identificador in todos

    chale_repo.definir_ativo(identificador, True)
    assert chale_repo.buscar_por_id(identificador)["ativo"] == 1


def test_contagem_separa_ativos(aplicacao):
    assert chale_repo.contar() >= chale_repo.contar(apenas_ativos=True)


# ---------------------------------------------------------------------------
# Repositório de tarifas
# ---------------------------------------------------------------------------

def test_cada_chale_tem_tarifas_de_alta_e_baixa(aplicacao):
    tarifas = tarifa_repo.listar_por_chale(1)
    assert len(tarifas) >= 2


def test_criar_tarifa_em_periodo_livre(aplicacao):
    identificador = tarifa_repo.criar(1, TARIFA_NOVA)
    lida = tarifa_repo.buscar_por_id(identificador)

    assert lida["valor_diaria"] == Decimal("450.00")
    assert lida["data_inicio"] == dt.date(2027, 6, 1)

    tarifa_repo.excluir(identificador)


def test_valor_e_decimal_nao_ponto_flutuante(aplicacao):
    """Erro de arredondamento em dinheiro é inaceitável."""
    identificador = tarifa_repo.criar(
        1, dict(TARIFA_NOVA, data_inicio="2027-07-01",
                data_fim="2027-07-31", valor_diaria="333.33")
    )
    try:
        lida = tarifa_repo.buscar_por_id(identificador)
        assert isinstance(lida["valor_diaria"], Decimal)
        assert lida["valor_diaria"] == Decimal("333.33")
    finally:
        tarifa_repo.excluir(identificador)


def test_valor_com_virgula_e_aceito(aplicacao):
    """Quem digita em português escreve 450,00."""
    identificador = tarifa_repo.criar(
        1, dict(TARIFA_NOVA, data_inicio="2027-08-01",
                data_fim="2027-08-31", valor_diaria="450,50")
    )
    try:
        assert tarifa_repo.buscar_por_id(identificador)["valor_diaria"] == Decimal("450.50")
    finally:
        tarifa_repo.excluir(identificador)


def test_periodo_sobreposto_e_recusado(aplicacao):
    """Com duas tarifas na mesma data, o cálculo ficaria ambíguo.

    Esta regra mora na aplicação, não no banco. A trigger valida a
    coerência das datas e o valor não negativo, mas não a sobreposição.
    """
    with pytest.raises(tarifa_repo.ErroDeTarifa) as erro:
        tarifa_repo.criar(
            1, dict(TARIFA_NOVA, data_inicio="2026-12-01", data_fim="2026-12-20")
        )
    assert "sobrepor" in str(erro.value)


def test_periodo_encostado_e_aceito(aplicacao):
    """Tarifas podem ser consecutivas, só não sobrepostas.

    A baixa temporada termina em 14/12 e a alta começa em 15/12 na massa
    de dados. Uma terceira tarifa começando no dia seguinte ao fim da alta
    precisa ser aceita.
    """
    identificador = tarifa_repo.criar(
        1, dict(TARIFA_NOVA, data_inicio="2027-03-01", data_fim="2027-03-31")
    )
    tarifa_repo.excluir(identificador)


def test_alterar_tarifa_nao_conflita_consigo_mesma(aplicacao):
    """O mesmo risco da trigger de UPDATE de reserva, em outro lugar."""
    identificador = tarifa_repo.criar(
        1, dict(TARIFA_NOVA, data_inicio="2027-09-01", data_fim="2027-09-30")
    )
    try:
        tarifa_repo.atualizar(
            identificador, 1,
            dict(TARIFA_NOVA, data_inicio="2027-09-01",
                 data_fim="2027-09-30", valor_diaria="500.00"),
        )
        assert tarifa_repo.buscar_por_id(identificador)["valor_diaria"] == Decimal("500.00")
    finally:
        tarifa_repo.excluir(identificador)


@pytest.mark.parametrize(
    "dados,trecho",
    [
        ({"data_inicio": "", "data_fim": "2027-01-10", "valor_diaria": "1"}, "obrigatória"),
        ({"data_inicio": "10/01/2027", "data_fim": "2027-01-20", "valor_diaria": "1"}, "AAAA-MM-DD"),
        ({"data_inicio": "2027-01-20", "data_fim": "2027-01-10", "valor_diaria": "1"}, "anterior"),
        ({"data_inicio": "2027-01-01", "data_fim": "2027-01-10", "valor_diaria": "-5"}, "negativo"),
        ({"data_inicio": "2027-01-01", "data_fim": "2027-01-10", "valor_diaria": "abc"}, "número"),
        ({"data_inicio": "2027-01-01", "data_fim": "2027-01-10", "valor_diaria": ""}, "obrigatório"),
    ],
)
def test_tarifa_invalida_e_recusada_com_mensagem(aplicacao, dados, trecho):
    with pytest.raises(tarifa_repo.ErroDeTarifa) as erro:
        tarifa_repo.criar(1, dados)
    assert trecho in str(erro.value)


def test_vigente_em_devolve_a_tarifa_da_data(aplicacao):
    """Base do cálculo de valor total, que entra no Bloco 4."""
    baixa = tarifa_repo.vigente_em(1, "2026-06-15")
    alta = tarifa_repo.vigente_em(1, "2026-12-20")

    assert baixa is not None and alta is not None
    assert alta["valor_diaria"] > baixa["valor_diaria"]


def test_vigente_em_data_sem_tarifa_devolve_nulo(aplicacao):
    assert tarifa_repo.vigente_em(1, "2030-01-01") is None


def test_trigger_recusa_valor_negativo_mesmo_por_sql_direto(aplicacao):
    """A validação da aplicação não é a única barreira."""
    import mysql.connector

    with conexao.obter_cursor() as cursor:
        with pytest.raises(mysql.connector.Error) as erro:
            cursor.execute(
                "INSERT INTO tarifa "
                "(chale_id, data_inicio, data_fim, valor_diaria) "
                "VALUES (1, '2028-01-01', '2028-01-31', -10.00)"
            )
    assert erro.value.errno == 1644


# ---------------------------------------------------------------------------
# Rotas: controle de acesso
# ---------------------------------------------------------------------------

ROTAS_DE_LEITURA = ["/chales/", "/chales/novo", "/chales/1"]


@pytest.mark.parametrize("rota", ROTAS_DE_LEITURA)
def test_administrador_acessa(cliente, rota):
    assert cliente(ADMIN).get(rota).status_code == 200


@pytest.mark.parametrize("rota", ROTAS_DE_LEITURA)
@pytest.mark.parametrize("credenciais", [RECEPCAO, HOSPEDE])
def test_outros_perfis_recebem_403(cliente, rota, credenciais):
    """A seção 3.3 do plano: o recepcionista não altera chalés nem tarifas."""
    assert cliente(credenciais).get(rota).status_code == 403


@pytest.mark.parametrize("rota", ROTAS_DE_LEITURA)
def test_sem_sessao_leva_ao_login(aplicacao, rota):
    resposta = aplicacao.test_client().get(rota, follow_redirects=False)
    assert resposta.status_code == 302
    assert "/login" in resposta.headers["Location"]


def test_recepcionista_nao_altera_tarifa(cliente):
    """Caso obrigatório da Etapa 3."""
    resposta = cliente(RECEPCAO).post(
        "/chales/1/tarifas/nova",
        data={"data_inicio": "2028-05-01", "data_fim": "2028-05-31",
              "valor_diaria": "100"},
    )
    assert resposta.status_code == 403


def test_link_de_chales_so_aparece_para_administrador(cliente):
    assert "/chales/" in cliente(ADMIN).get("/painel").data.decode()
    assert "/chales/" not in cliente(RECEPCAO).get("/painel").data.decode()


# ---------------------------------------------------------------------------
# Rotas: comportamento
# ---------------------------------------------------------------------------

def test_cadastro_pela_tela(cliente):
    sessao = cliente(ADMIN)
    resposta = sessao.post(
        "/chales/novo",
        data={"nome": "Pela tela", "capacidade": "4",
              "configuracao_camas": "1 cama de casal",
              "comodidades": "Wi-Fi", "descricao": "Teste"},
        follow_redirects=False,
    )
    assert resposta.status_code == 302
    assert "Pela tela" in sessao.get("/chales/").data.decode()


def test_erro_de_validacao_mostra_mensagem_e_preserva_o_formulario(cliente):
    """O que foi digitado não pode sumir quando a validação recusa."""
    resposta = cliente(ADMIN).post(
        "/chales/novo", data={"nome": "", "capacidade": "3"}
    )

    assert resposta.status_code == 400
    assert "obrigatório" in _aviso(resposta)
    assert 'value="3"' in resposta.data.decode()


def test_erro_do_banco_nao_vaza_para_a_tela(cliente):
    """Nome duplicado vira mensagem legível, não erro de SQL."""
    resposta = cliente(ADMIN).post(
        "/chales/novo", data={"nome": "Embaré", "capacidade": "4"}
    )

    corpo = resposta.data.decode()
    assert resposta.status_code == 400
    assert _aviso(resposta)
    for vazamento in ("1062", "Duplicate", "mysql", "uq_chale_nome", "Traceback"):
        assert vazamento not in corpo


def test_tarifa_sobreposta_mostra_o_periodo_em_conflito(cliente):
    resposta = cliente(ADMIN).post(
        "/chales/1/tarifas/nova",
        data={"data_inicio": "2026-12-01", "data_fim": "2026-12-20",
              "valor_diaria": "400"},
    )

    assert resposta.status_code == 400
    assert "sobrepor" in _aviso(resposta)


def test_chale_inexistente_devolve_404(cliente):
    assert cliente(ADMIN).get("/chales/999999").status_code == 404


def test_tarifa_de_outro_chale_devolve_404(cliente):
    """Referência direta a objeto: trocar o número na URL não vale.

    A tarifa 1 pertence ao chalé 1. Pedi-la pela URL do chalé 2 precisa
    falhar, senão bastaria alterar o endereço para editar a tarifa de
    outro chalé.
    """
    assert cliente(ADMIN).get("/chales/2/tarifas/1").status_code == 404


def test_alternar_situacao_nao_aceita_get(cliente):
    """Com GET, uma imagem em sítio hostil desativaria o chalé."""
    assert cliente(ADMIN).get("/chales/1/situacao").status_code == 405


def test_conteudo_com_script_e_escapado(cliente):
    """Escape do Jinja2: o que vem do usuário não vira código."""
    sessao = cliente(ADMIN)
    sessao.post(
        "/chales/novo",
        data={"nome": "<script>alert(1)</script>", "capacidade": "4"},
    )

    corpo = sessao.get("/chales/").data.decode()
    assert "<script>alert(1)</script>" not in corpo
