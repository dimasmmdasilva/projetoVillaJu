"""A reserva registrada pela recepção entra no controle do Pix — Bloco 16b.

O que estes testes guardam:

1. **Nasce pendente, com modalidade e prazo de 24 horas.** A modalidade sai
   da antecedência, como no site; o prazo, da constante do regulamento.
2. **O aceite é da recepção** e fica no log com o autor — sem o nome de
   quem vai se hospedar.
3. **Não há "Marcar como confirmada".** Nem o botão, nem a transição: um
   envio forjado é recusado, e quem confirma é a conferência do Pix.
4. **O limite de um pedido em aberto vale** também pela recepção.
5. **O que já existia continua:** cancelar e concluir, e a reserva antiga,
   sem modalidade, segue "combinada com a recepção".
"""

import pathlib
import re

import pytest

from app.dados import conexao
from app.servicos import painel_equipe, pagamentos, regulamento
from app.servicos import registro_pela_recepcao as registro

from test_pedido_do_hospede import (  # noqa: F401
    DADOS,
    _dia,
    _entrar,
    _pedir,
    _reserva,
    _sql,
    _token,
    _vencer,
    aplicacao,
    chale,
    conta,
)

pytestmark = pytest.mark.banco

ADMIN = ("admin", "Villa@Admin2026")
RECEPCAO = ("recepcao", "Villa@Recep2026")


def _formulario(conta, chale, entrada=10, noites=2, pessoas=2, aceite=True,
                **extra):
    dados = {
        "chale_id": str(chale["id"]),
        "hospede_id": str(conta["hospede_id"]),
        "data_entrada": _dia(entrada),
        "data_saida": _dia(entrada + noites),
        "num_pessoas": str(pessoas),
        "observacoes": "Pedido por telefone.",
    }
    if aceite:
        dados["aceite"] = "sim"
    dados.update(extra)
    return dados


def _registrar(cliente, dados):
    dados = dict(dados, csrf_token=_token(cliente, "/reservas/nova"))
    return cliente.post("/reservas/nova", data=dados)


def _numero(resposta):
    return int(re.search(r"/reservas/(\d+)$", resposta.headers["Location"]).group(1))


def _contar_reservas(aplicacao):
    with aplicacao.app_context():
        with conexao.obter_cursor() as cursor:
            cursor.execute("SELECT COUNT(*) FROM reserva")
            return cursor.fetchone()[0]


def _consulta(aplicacao, comando, parametros=()):
    with aplicacao.app_context():
        with conexao.obter_cursor() as cursor:
            cursor.execute(comando, parametros)
            linhas = cursor.fetchall()
            return linhas[0] if linhas else None


@pytest.fixture
def recepcao(aplicacao):
    return _entrar(aplicacao, *RECEPCAO)


# ===========================================================================
# 1. Nasce pendente, com modalidade e prazo de 24 horas
# ===========================================================================

def test_com_antecedencia_nasce_pendente_com_sinal_e_24_horas(aplicacao, conta,
                                                             chale, recepcao):
    resposta = _registrar(recepcao, _formulario(conta, chale, entrada=10))
    assert resposta.status_code == 302
    reserva = _reserva(aplicacao, _numero(resposta))

    assert reserva["situacao"] == "pendente"
    assert reserva["modalidade"] == regulamento.SINAL
    assert reserva["prazo_restante"] is not None
    assert reserva["regulamento_aceito_em"] is not None
    assert reserva["observacoes"] == "Pedido por telefone."
    horas = _consulta(
        aplicacao,
        "SELECT TIMESTAMPDIFF(MINUTE, criado_em, prazo_pagamento) "
        "FROM reserva WHERE id = %s", (reserva["id"],))[0]
    assert horas == regulamento.HORAS_PARA_O_PIX_PELA_RECEPCAO * 60


def test_em_cima_da_hora_nasce_integral(aplicacao, conta, chale, recepcao):
    resposta = _registrar(recepcao, _formulario(conta, chale, entrada=3))
    reserva = _reserva(aplicacao, _numero(resposta))
    assert reserva["modalidade"] == regulamento.INTEGRAL
    assert reserva["prazo_restante"] is None


def test_o_prazo_sai_da_constante(aplicacao, conta, chale, recepcao, monkeypatch):
    """Se o número mudar no regulamento, muda no registro — um lugar só."""
    monkeypatch.setattr(registro, "MINUTOS_PARA_O_PIX", 5 * 60)
    resposta = _registrar(recepcao, _formulario(conta, chale))
    minutos = _consulta(
        aplicacao,
        "SELECT TIMESTAMPDIFF(MINUTE, criado_em, prazo_pagamento) "
        "FROM reserva WHERE id = %s", (_numero(resposta),))[0]
    assert minutos == 5 * 60


def test_o_regulamento_e_a_tela_dizem_as_24_horas(aplicacao, recepcao):
    horas = f"{regulamento.HORAS_PARA_O_PIX_PELA_RECEPCAO} horas"
    assert any(horas in regra["texto"] for regra in regulamento.regras())
    pagina = recepcao.get("/reservas/nova").data.decode()
    assert f"aguardando o Pix por\n    {regulamento.HORAS_PARA_O_PIX_PELA_RECEPCAO} horas" in pagina
    assert "Registrar reserva" in pagina


@pytest.mark.parametrize("entrada,trecho", [
    (-1, "A data de entrada já passou"),
    (500, "Não há tarifa cadastrada"),
])
def test_entrada_passada_ou_sem_tarifa_e_recusada(aplicacao, conta, chale,
                                                  recepcao, entrada, trecho):
    antes = _contar_reservas(aplicacao)
    resposta = _registrar(recepcao, _formulario(conta, chale, entrada=entrada))
    assert resposta.status_code == 400
    assert trecho in resposta.data.decode()
    assert _contar_reservas(aplicacao) == antes


# ===========================================================================
# 2. O aceite é da recepção
# ===========================================================================

def test_sem_o_aceite_nada_e_gravado(aplicacao, conta, chale, recepcao):
    antes = _contar_reservas(aplicacao)
    resposta = _registrar(recepcao, _formulario(conta, chale, aceite=False))
    assert resposta.status_code == 400
    assert "leu as regras do regulamento ao hóspede" in resposta.data.decode()
    assert _contar_reservas(aplicacao) == antes


def test_aceite_com_outro_valor_nao_vale(aplicacao, conta, chale, recepcao):
    antes = _contar_reservas(aplicacao)
    dados = _formulario(conta, chale, aceite=False)
    dados["aceite"] = "talvez"
    assert _registrar(recepcao, dados).status_code == 400
    assert _contar_reservas(aplicacao) == antes


def test_o_log_guarda_o_aceite_e_o_autor_sem_o_nome(aplicacao, conta, chale,
                                                    recepcao):
    identificador = _numero(_registrar(recepcao, _formulario(conta, chale)))
    acao, autor, detalhe = _consulta(
        aplicacao,
        "SELECT a.acao, u.login, a.detalhe FROM log_auditoria a "
        "JOIN usuario u ON u.id = a.usuario_id "
        "WHERE a.tabela_afetada = 'reserva' AND a.registro_id = %s "
        "  AND a.acao = 'PEDIDO_DE_RESERVA'", (identificador,))
    assert acao == "PEDIDO_DE_RESERVA"
    assert autor == RECEPCAO[0]
    assert "regras lidas ao hóspede pela recepção" in detalhe
    for pessoal in (DADOS["nome"], DADOS["documento"], DADOS["email"]):
        assert pessoal not in detalhe


# ===========================================================================
# 3. Não há "Marcar como confirmada"
# ===========================================================================

def test_o_detalhe_nao_oferece_confirmar(aplicacao, conta, chale, recepcao):
    identificador = _numero(_registrar(recepcao, _formulario(conta, chale)))
    pagina = recepcao.get(f"/reservas/{identificador}").data.decode()
    assert "Marcar como confirmada" not in pagina
    assert "Cancelar pela pousada" in pagina and "Cancelar a pedido do hóspede" in pagina
    assert 'href="/pagamentos"' in pagina


@pytest.mark.parametrize("credenciais", [ADMIN, RECEPCAO])
def test_envio_forjado_de_confirmada_e_recusado(aplicacao, conta, chale,
                                                credenciais):
    cliente = _entrar(aplicacao, *credenciais)
    identificador = _numero(_registrar(cliente, _formulario(conta, chale)))
    rota = f"/reservas/{identificador}"
    resposta = cliente.post(
        f"{rota}/situacao",
        data={"situacao": "confirmada", "csrf_token": _token(cliente, rota)},
        follow_redirects=True,
    )
    assert "só pode passar para cancelada" in resposta.data.decode()
    assert _reserva(aplicacao, identificador)["situacao"] == "pendente"


def test_quem_confirma_e_a_conferencia_do_pix(aplicacao, conta, chale, recepcao):
    identificador = _numero(_registrar(recepcao, _formulario(conta, chale)))
    cards = recepcao.get("/pagamentos").data.decode()
    assert "Pagou 50% Pix" in cards
    resposta = recepcao.post(
        f"/pagamentos/{identificador}/{pagamentos.PRIMEIRO}",
        data={"csrf_token": _token(recepcao, "/pagamentos")},
    )
    assert resposta.status_code == 302
    assert _reserva(aplicacao, identificador)["situacao"] == "confirmada"


def test_sem_pix_em_24_horas_a_varredura_cancela(aplicacao, conta, chale,
                                                 recepcao):
    identificador = _numero(_registrar(recepcao, _formulario(conta, chale)))
    _vencer(aplicacao, identificador)
    recepcao.get("/painel")
    reserva = _reserva(aplicacao, identificador)
    assert reserva["situacao"] == "cancelada"
    assert reserva["motivo_cancelamento"] == "prazo_do_pix"


def test_o_hospede_com_conta_pode_informar_o_pix(aplicacao, conta, chale,
                                                 recepcao):
    """A reserva é dele: aparece em Minhas reservas, com o botão do Pix."""
    _registrar(recepcao, _formulario(conta, chale))
    from test_pedido_do_hospede import SENHA
    pagina = _entrar(aplicacao, DADOS["email"], SENHA).get(
        "/reservas/minhas").data.decode()
    assert "Fiz o Pix" in pagina


# ===========================================================================
# 4. O limite de um pedido em aberto
# ===========================================================================

def test_um_pedido_em_aberto_barra_o_registro(aplicacao, conta, chale, recepcao):
    _pedir(aplicacao, conta, chale, entrada=40)
    antes = _contar_reservas(aplicacao)
    resposta = _registrar(recepcao, _formulario(conta, chale, entrada=60))
    assert resposta.status_code == 400
    html = resposta.data.decode()
    assert "Este hóspede já tem um pedido" in html
    assert "Você já tem" not in html, "a mensagem é para a equipe"
    assert _contar_reservas(aplicacao) == antes


def test_conflito_mostra_as_datas_a_equipe(aplicacao, conta, chale, recepcao):
    """A equipe pode ver as datas do conflito; o hóspede nunca vê."""
    _registrar(recepcao, _formulario(conta, chale, entrada=10, noites=4))
    _sql(aplicacao, "UPDATE reserva SET situacao = 'confirmada'")
    resposta = _registrar(recepcao, _formulario(conta, chale, entrada=12))
    assert resposta.status_code == 409
    assert "já está reservado de" in resposta.data.decode()


# ===========================================================================
# 5. Quem registrou, e o que não mudou
# ===========================================================================

def test_o_card_diz_quem_registrou(aplicacao, conta, chale, recepcao):
    _registrar(recepcao, _formulario(conta, chale))
    painel = recepcao.get("/painel").data.decode()
    assert re.search(r"· recepcao\s*<", painel), "o autor é a recepção"
    assert "· pelo site" not in painel


def test_autor_pelo_perfil():
    assert painel_equipe.autor({"criado_por_perfil": "hospede",
                                "criado_por_login": "XbS_codigo"}) == "pelo site"
    assert painel_equipe.autor({"criado_por_perfil": "recepcionista",
                                "criado_por_login": "recepcao",
                                "modalidade": "sinal"}) == "recepcao"
    assert painel_equipe.autor({"criado_por_perfil": None,
                                "criado_por_login": None}) == "—"


def test_a_reserva_antiga_segue_combinada_com_a_recepcao():
    antiga = {"situacao": "pendente", "modalidade": None}
    assert painel_equipe.selo_pagamento(antiga, {}) == "combinado com a recepção"


def test_nenhuma_rota_cria_reserva_fora_do_pix():
    """`reserva_repo.criar` não cria modalidade nem prazo. Se uma rota ou
    serviço voltar a chamá-la, a reserva sairia do controle do Pix."""
    raiz = pathlib.Path(__file__).resolve().parents[1] / "app"
    for arquivo in raiz.rglob("*.py"):
        if arquivo.name == "reserva_repo.py":
            continue
        assert "reserva_repo.criar(" not in arquivo.read_text(encoding="utf-8"), (
            arquivo
        )
