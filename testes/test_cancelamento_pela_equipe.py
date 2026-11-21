"""A equipe cancela dizendo o motivo — Etapa 5, Bloco 17e.

A lacuna, achada pelo Dimas testando: o "Marcar como cancelada" da equipe
só trocava a situação. A reserva paga ficava sem devolução, fora de
"Devoluções", e o hóspede via só "cancelada". A regra, confirmada por ele:

- **a pedido do hóspede** (por telefone ou mensagem): a mesma do site, com
  tudo devolvido com 7 dias ou mais antes da entrada, e nada com menos;
- **pela pousada**: devolve tudo, sempre.

O que estes testes guardam: o motivo é obrigatório e decide a devolução; a
devolução entra para a equipe com o valor certo; o hóspede vê quem
cancelou; e a regra é uma só, `regulamento.tem_direito_a_devolucao`.
"""

import datetime as dt
from decimal import Decimal

import pytest

from app.dados import conexao, pagamento_repo, reserva_repo
from app.servicos import pagamentos, pedido, regulamento

from test_pedido_do_hospede import (  # noqa: F401
    DADOS,
    SENHA,
    _entrar,
    _pedir,
    _reserva,
    _token,
    aplicacao,
    chale,
    conta,
)

pytestmark = pytest.mark.banco

ADMIN = ("admin", "Villa@Admin2026")
RECEPCAO = ("recepcao", "Villa@Recep2026")
HOJE = dt.date.today()


@pytest.fixture
def recepcao(aplicacao):
    with aplicacao.app_context():
        with conexao.obter_cursor(dicionario=True) as cursor:
            cursor.execute("SELECT * FROM usuario WHERE login = 'recepcao'")
            return cursor.fetchone()


def _quitada(aplicacao, conta, chale, recepcao, entrada):
    """Uma reserva paga por inteiro, com a entrada daqui a `entrada` dias."""
    identificador = _pedir(aplicacao, conta, chale, entrada=entrada)
    with aplicacao.app_context():
        assert pagamentos.executar(pagamentos.PRIMEIRO, identificador, recepcao)
        if _reserva(aplicacao, identificador)["modalidade"] == "sinal":
            assert pagamentos.executar(pagamentos.RESTANTE, identificador,
                                       recepcao)
    return identificador


def _cancelar(cliente, identificador, motivo):
    rota = f"/reservas/{identificador}"
    dados = {"situacao": "cancelada", "csrf_token": _token(cliente, rota)}
    if motivo is not None:
        dados["motivo"] = motivo
    return cliente.post(f"{rota}/situacao", data=dados, follow_redirects=True)


def _devolucoes(aplicacao):
    with aplicacao.app_context():
        return {d["reserva"]["id"]: d for d in pagamentos.devolucoes(usuario_id=1)}


# ===========================================================================
# 1. A regra, num lugar só
# ===========================================================================

@pytest.mark.parametrize("motivo,dias,esperado", [
    ("equipe", 30, True),
    ("equipe", 1, True),
    ("equipe", 0, True),
    ("pedido_do_hospede", 7, True),
    ("pedido_do_hospede", 6, False),
    ("prazo_do_pix", 30, False),
    ("falta_de_pagamento", 30, False),
    (None, 30, False),
])
def test_a_regra_da_devolucao_por_motivo(motivo, dias, esperado):
    entrada = HOJE + dt.timedelta(days=dias)
    assert regulamento.tem_direito_a_devolucao(motivo, entrada, HOJE) is esperado


def test_o_regulamento_diz_que_a_pousada_devolve_tudo():
    textos = " ".join(r["titulo"] + " " + r["texto"] for r in regulamento.regras())
    assert "Cancelamento pela pousada" in textos
    assert "todo o valor pago é devolvido" in textos


# ===========================================================================
# 2. O motivo é obrigatório, e fica gravado
# ===========================================================================

@pytest.mark.parametrize("motivo", [None, "", "prazo_do_pix", "falta_de_pagamento",
                                    "inventado"])
def test_cancelar_sem_motivo_valido_e_recusado(aplicacao, conta, chale, recepcao,
                                               motivo):
    identificador = _quitada(aplicacao, conta, chale, recepcao, 3)
    html = _cancelar(_entrar(aplicacao, *ADMIN), identificador, motivo).data.decode()
    assert "a pedido do hóspede ou pela pousada" in html
    assert _reserva(aplicacao, identificador)["situacao"] == "confirmada"


@pytest.mark.parametrize("motivo", ["equipe", "pedido_do_hospede"])
def test_o_cancelamento_grava_motivo_e_data(aplicacao, conta, chale, recepcao,
                                            motivo):
    identificador = _quitada(aplicacao, conta, chale, recepcao, 3)
    _cancelar(_entrar(aplicacao, *RECEPCAO), identificador, motivo)
    reserva = _reserva(aplicacao, identificador)
    assert reserva["situacao"] == "cancelada"
    assert reserva["motivo_cancelamento"] == motivo
    assert reserva["cancelado_em"] is not None


def test_o_repositorio_tambem_exige_o_motivo(aplicacao, conta, chale):
    identificador = _pedir(aplicacao, conta, chale)
    with aplicacao.app_context():
        with pytest.raises(reserva_repo.ErroDeReserva):
            reserva_repo.mudar_situacao(identificador, "cancelada")


def test_cancelar_de_novo_e_recusado_sem_segundo_registro(aplicacao, conta, chale,
                                                          recepcao):
    identificador = _quitada(aplicacao, conta, chale, recepcao, 3)
    equipe = _entrar(aplicacao, *ADMIN)
    _cancelar(equipe, identificador, "equipe")
    html = _cancelar(equipe, identificador, "equipe").data.decode()
    assert "Reserva cancelada pela pousada" not in html
    with aplicacao.app_context():
        with conexao.obter_cursor() as cursor:
            cursor.execute("SELECT COUNT(*) FROM log_auditoria "
                           "WHERE acao = 'CANCELADA_PELA_EQUIPE' AND registro_id = %s",
                           (identificador,))
            assert cursor.fetchone()[0] == 1


# ===========================================================================
# 3. O motivo decide a devolução
# ===========================================================================

def test_pela_pousada_devolve_tudo_mesmo_em_cima_da_hora(aplicacao, conta, chale,
                                                         recepcao):
    identificador = _quitada(aplicacao, conta, chale, recepcao, 3)
    html = _cancelar(_entrar(aplicacao, *ADMIN), identificador, "equipe").data.decode()
    assert "Reserva cancelada pela pousada" in html
    assert "Devolva R$ 600,00 ao hóspede" in html

    devolucao = _devolucoes(aplicacao)[identificador]
    assert devolucao["texto"].startswith("A pousada cancelou")
    assert "Pix 100% retornado — R$ 600,00" == devolucao["rotulo"]

    with aplicacao.app_context():
        assert pagamentos.executar(pagamentos.DEVOLVER, identificador, recepcao)
        assert pagamento_repo.lancamentos_da_reserva(identificador)["devolucao"] == \
            Decimal("-600.00")
    assert identificador not in _devolucoes(aplicacao)


def test_a_pedido_do_hospede_com_antecedencia_devolve(aplicacao, conta, chale,
                                                      recepcao):
    identificador = _quitada(aplicacao, conta, chale, recepcao, 30)
    _cancelar(_entrar(aplicacao, *RECEPCAO), identificador, "pedido_do_hospede")
    assert _devolucoes(aplicacao)[identificador]["texto"].startswith(
        "O hóspede cancelou")


def test_a_pedido_do_hospede_em_cima_da_hora_nao_devolve(aplicacao, conta, chale,
                                                         recepcao):
    identificador = _quitada(aplicacao, conta, chale, recepcao, 3)
    html = _cancelar(_entrar(aplicacao, *RECEPCAO), identificador,
                     "pedido_do_hospede").data.decode()
    assert "Não há devolução" in html
    assert "Nada tinha sido pago" not in html
    assert identificador not in _devolucoes(aplicacao)
    with aplicacao.app_context():
        assert not pagamentos.executar(pagamentos.DEVOLVER, identificador, recepcao)


def test_sem_pagamento_o_aviso_diz_que_nada_foi_pago(aplicacao, conta, chale):
    """Bloco 18e, achado do Dimas nas telas: "Não há devolução" de uma
    reserva que ninguém pagou soava como dinheiro retido."""
    identificador = _pedir(aplicacao, conta, chale, entrada=30)
    html = _cancelar(_entrar(aplicacao, *ADMIN), identificador,
                     "equipe").data.decode()
    assert "Nada tinha sido pago, então não há o que devolver." in html
    assert "Não há devolução" not in html


def test_o_detalhe_mostra_quanto_volta_em_cada_motivo(aplicacao, conta, chale,
                                                      recepcao):
    identificador = _quitada(aplicacao, conta, chale, recepcao, 3)
    pagina = _entrar(aplicacao, *ADMIN).get(f"/reservas/{identificador}").data.decode()
    bloco = pagina[pagina.index('class="cancelamento-pela-equipe"'):]
    a_pedido = bloco[bloco.index("Cancelar a pedido do hóspede"):]
    pela_pousada = bloco[bloco.index("Cancelar pela pousada"):]
    assert a_pedido.index("Sem devolução") < a_pedido.index("Cancelar pela pousada")
    assert "Devolve R$ 600,00 ao hóspede" in pela_pousada
    assert "Nada foi pago ainda" not in bloco


def test_sem_pagamento_o_detalhe_diz_que_nada_foi_pago(aplicacao, conta, chale):
    """Bloco 18e: "Sem devolução" numa reserva que ninguém pagou dava a
    entender que havia dinheiro retido."""
    identificador = _pedir(aplicacao, conta, chale, entrada=3)
    pagina = _entrar(aplicacao, *ADMIN).get(f"/reservas/{identificador}").data.decode()
    bloco = pagina[pagina.index('class="cancelamento-pela-equipe"'):]
    bloco = bloco[:bloco.index("</div>")]
    assert bloco.count("Nada foi pago ainda.") == 2
    assert "Sem devolução" not in bloco
    assert "Devolve" not in bloco


# ===========================================================================
# 4. O hóspede vê quem cancelou
# ===========================================================================

@pytest.mark.parametrize("motivo,rotulo,devolucao", [
    ("equipe", "Cancelada pela pousada", "aguardando a pousada fazer o Pix"),
    ("pedido_do_hospede", "Cancelada a seu pedido", "Sem devolução"),
])
def test_o_historico_do_hospede_diz_quem_cancelou(aplicacao, conta, chale,
                                                  recepcao, motivo, rotulo,
                                                  devolucao):
    identificador = _quitada(aplicacao, conta, chale, recepcao, 3)
    _cancelar(_entrar(aplicacao, *ADMIN), identificador, motivo)
    html = _entrar(aplicacao, DADOS["email"], SENHA).get("/reservas/minhas").data.decode()
    assert rotulo in html
    assert devolucao in html


@pytest.mark.parametrize("motivo,rotulo", sorted(pedido.ROTULOS_DO_CANCELAMENTO.items()))
def test_cada_motivo_tem_a_sua_frase(motivo, rotulo):
    assert pedido.rotulo_do_cancelamento(
        {"situacao": "cancelada", "motivo_cancelamento": motivo}) == rotulo


def test_reserva_viva_nao_tem_frase_de_cancelamento():
    assert pedido.rotulo_do_cancelamento({"situacao": "confirmada"}) is None
    assert pedido.rotulo_do_cancelamento(
        {"situacao": "cancelada", "motivo_cancelamento": None}) == "Cancelada"


# ===========================================================================
# 5. O log diz o motivo, nunca o nome
# ===========================================================================

def test_o_log_registra_o_motivo_e_a_devolucao(aplicacao, conta, chale, recepcao):
    identificador = _quitada(aplicacao, conta, chale, recepcao, 3)
    _cancelar(_entrar(aplicacao, *ADMIN), identificador, "equipe")
    with aplicacao.app_context():
        with conexao.obter_cursor() as cursor:
            cursor.execute("SELECT detalhe FROM log_auditoria "
                           "WHERE acao = 'CANCELADA_PELA_EQUIPE' AND registro_id = %s",
                           (identificador,))
            detalhe = cursor.fetchone()[0]
    assert detalhe == "Equipe cancelou a reserva pela pousada; com devolução"
    assert DADOS["nome"] not in detalhe
