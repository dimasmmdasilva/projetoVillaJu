"""O hóspede cancela; a equipe devolve — Etapa 5, Bloco 9.

A regra é do Dimas (13/11): cancelamento com **7 dias ou mais** de
antecedência da entrada devolve tudo o que foi pago; com **menos**, nada —
"assim dá tempo de outra pessoa ocupar o chalé".

O que estes testes guardam:

1. **O card diz antes do clique** se haverá devolução, e quanto.
2. **Só se cancela reserva confirmada**, antes da entrada, sem Pix em
   conferência — e o dono vai no WHERE.
3. **A devolução é o que a equipe conferiu**, com sinal negativo, no mês
   em que é feita, uma vez só, e só com direito pela regra.
4. **O ENUM de situação não ganhou `cancelamento_solicitado`.**
"""

import datetime as dt
import re
from decimal import Decimal

import pytest

from app.dados import conexao, pagamento_repo
from app.servicos import pagamentos, pedido, regulamento

from test_pedido_do_hospede import (  # noqa: F401
    DADOS,
    OUTRA,
    _entrar,
    _pedir,
    _reserva,
    _sql,
    _token,
    aplicacao,
    chale,
    conta,
    hospede,
    outra,
)

pytestmark = pytest.mark.banco

RECEPCAO = ("recepcao", "Villa@Recep2026")
HOJE = dt.date.today()


def _usuario(aplicacao, login):
    with aplicacao.app_context():
        with conexao.obter_cursor(dicionario=True) as cursor:
            cursor.execute("SELECT * FROM usuario WHERE login = %s", (login,))
            return cursor.fetchone()


@pytest.fixture
def recepcao(aplicacao):
    return _usuario(aplicacao, RECEPCAO[0])


def _confirmada(aplicacao, conta, chale, recepcao, entrada=20, restante=False):
    """Um pedido com o sinal (ou o integral) conferido pela equipe."""
    identificador = _pedir(aplicacao, conta, chale, entrada=entrada, noites=2)
    with aplicacao.app_context():
        assert pagamentos.executar(pagamentos.PRIMEIRO, identificador, recepcao)
        if restante:
            assert pagamentos.executar(pagamentos.RESTANTE, identificador,
                                       recepcao)
    return identificador


def _cancelar(aplicacao, conta, identificador, hoje=None):
    with aplicacao.app_context():
        return pedido.cancelar(conta, identificador, hoje=hoje)


def _cancelado_ha(aplicacao, identificador, dias_antes_da_entrada):
    """Põe o momento do cancelamento N dias antes da entrada."""
    _sql(aplicacao,
         "UPDATE reserva SET cancelado_em = TIMESTAMP(data_entrada - "
         "INTERVAL %s DAY, '10:00:00') WHERE id = %s",
         (dias_antes_da_entrada, identificador))


# ===========================================================================
# 1. O hóspede cancela
# ===========================================================================

def test_cancelar_com_sete_dias_devolve_tudo(aplicacao, conta, chale, recepcao):
    identificador = _confirmada(aplicacao, conta, chale, recepcao, entrada=20)
    assert _cancelar(aplicacao, conta, identificador) == Decimal("300.00")

    linha = _reserva(aplicacao, identificador)
    assert linha["situacao"] == "cancelada"
    assert linha["motivo_cancelamento"] == "pedido_do_hospede"


def test_a_fronteira_dos_sete_dias_e_inclusiva(aplicacao, conta, chale,
                                              recepcao):
    identificador = _confirmada(aplicacao, conta, chale, recepcao, entrada=20)
    entrada = _reserva(aplicacao, identificador)["data_entrada"]
    assert _cancelar(aplicacao, conta, identificador,
                     hoje=entrada - dt.timedelta(days=7)) == Decimal("300.00")


def test_cancelar_com_menos_de_sete_dias_nao_devolve(aplicacao, conta, chale,
                                                    recepcao):
    identificador = _confirmada(aplicacao, conta, chale, recepcao, entrada=20)
    entrada = _reserva(aplicacao, identificador)["data_entrada"]
    assert _cancelar(aplicacao, conta, identificador,
                     hoje=entrada - dt.timedelta(days=6)) == Decimal("0.00")


def test_cancelar_libera_o_chale(aplicacao, conta, chale, recepcao):
    from app.dados import reserva_repo
    identificador = _confirmada(aplicacao, conta, chale, recepcao)
    _cancelar(aplicacao, conta, identificador)
    linha = _reserva(aplicacao, identificador)
    with aplicacao.app_context():
        assert reserva_repo.chale_disponivel(
            chale["id"], linha["data_entrada"], linha["data_saida"])


def test_pendente_nao_se_cancela_por_aqui(aplicacao, conta, chale):
    """Antes do Pix, o caminho é "Desistir do pedido"."""
    identificador = _pedir(aplicacao, conta, chale)
    assert _cancelar(aplicacao, conta, identificador) is None
    assert _reserva(aplicacao, identificador)["situacao"] == "pendente"


def test_pix_do_restante_em_conferencia_espera(aplicacao, conta, chale,
                                               recepcao):
    """Devolver com base numa declaração seria devolver o que ninguém viu."""
    identificador = _confirmada(aplicacao, conta, chale, recepcao)
    with aplicacao.app_context():
        assert pedido.informar_pix(conta, identificador, pedido.RESTANTE)
    assert _cancelar(aplicacao, conta, identificador) is None


def test_restante_conferido_volta_a_permitir(aplicacao, conta, chale, recepcao):
    identificador = _confirmada(aplicacao, conta, chale, recepcao)
    with aplicacao.app_context():
        pedido.informar_pix(conta, identificador, pedido.RESTANTE)
        pagamentos.executar(pagamentos.RESTANTE, identificador, recepcao)
    assert _cancelar(aplicacao, conta, identificador) == Decimal("600.00")


def test_no_dia_da_entrada_nao_se_cancela(aplicacao, conta, chale, recepcao):
    identificador = _confirmada(aplicacao, conta, chale, recepcao, entrada=3)
    _sql(aplicacao, "UPDATE reserva SET data_entrada = CURDATE(), "
                    "data_saida = CURDATE() + INTERVAL 2 DAY WHERE id = %s",
         (identificador,))
    assert _cancelar(aplicacao, conta, identificador) is None


def test_reserva_alheia_e_recusada_como_inexistente(aplicacao, conta, outra,
                                                    chale, recepcao):
    identificador = _confirmada(aplicacao, conta, chale, recepcao)
    assert _cancelar(aplicacao, outra, identificador) is None
    assert _cancelar(aplicacao, outra, 999999) is None
    assert _reserva(aplicacao, identificador)["situacao"] == "confirmada"


def test_o_dono_vai_no_where(aplicacao, conta, outra, chale, recepcao):
    """Contornando o serviço: o UPDATE sozinho recusa a reserva alheia."""
    from app.dados import reserva_repo
    identificador = _confirmada(aplicacao, conta, chale, recepcao)
    with aplicacao.app_context():
        assert not reserva_repo.cancelar_pelo_hospede(
            identificador, outra["hospede_id"])
    assert _reserva(aplicacao, identificador)["situacao"] == "confirmada"


def test_cancelar_duas_vezes_e_recusado(aplicacao, conta, chale, recepcao):
    identificador = _confirmada(aplicacao, conta, chale, recepcao)
    assert _cancelar(aplicacao, conta, identificador) is not None
    assert _cancelar(aplicacao, conta, identificador) is None


def test_o_cancelamento_vai_para_o_log(aplicacao, conta, chale, recepcao,
                                       conexao_banco):
    identificador = _confirmada(aplicacao, conta, chale, recepcao)
    _cancelar(aplicacao, conta, identificador)
    cursor = conexao_banco.cursor()
    cursor.execute("SELECT detalhe FROM log_auditoria WHERE registro_id = %s "
                   "AND acao = 'CANCELADA_PELO_HOSPEDE'", (identificador,))
    assert cursor.fetchall() == [
        ("Hóspede cancelou a reserva confirmada; com devolução",)]
    cursor.close()


def test_o_enum_nao_ganhou_cancelamento_solicitado(conexao_banco, aplicacao):
    cursor = conexao_banco.cursor()
    cursor.execute("SELECT column_type FROM information_schema.columns "
                   "WHERE table_schema = DATABASE() AND table_name = 'reserva' "
                   "AND column_name = 'situacao'")
    assert "cancelamento_solicitado" not in cursor.fetchone()[0]
    cursor.close()


# ===========================================================================
# 2. O card diz antes do clique
# ===========================================================================

AGORA = dt.datetime(2026, 11, 20, 12, 0, 0)


def _linha(**campos):
    base = {
        "situacao": "confirmada", "modalidade": "sinal",
        "valor_total": Decimal("1300.00"),
        "data_entrada": dt.date(2026, 12, 20),
        "prazo_pagamento": AGORA, "prazo_restante": dt.datetime(2026, 12, 18, 23, 59, 59),
        "pix_informado_em": AGORA, "restante_informado_em": None,
        "motivo_cancelamento": None,
        "lancamentos": {"sinal": Decimal("650.00")},
    }
    base.update(campos)
    return base


def test_card_avisa_a_devolucao_e_o_ultimo_dia():
    estado = pedido.cartao(_linha(), agora=AGORA)
    assert estado["pode_cancelar"] is True
    assert estado["aviso_cancelamento"] == (
        "Cancelando até 13/12/2026, você recebe de volta tudo o que pagou "
        "(R$ 650,00).")


def test_card_avisa_que_nao_ha_devolucao():
    estado = pedido.cartao(_linha(data_entrada=dt.date(2026, 11, 26)),
                           agora=AGORA)
    assert estado["pode_cancelar"] is True
    assert estado["aviso_cancelamento"] == (
        "Faltam menos de 7 dias para a entrada: se cancelar agora, o valor "
        "pago (R$ 650,00) não é devolvido.")


def test_o_aviso_sai_da_constante_do_regulamento(monkeypatch):
    """A lição do Bloco 14: o texto acompanha a regra."""
    monkeypatch.setattr(regulamento, "DIAS_ANTES_DA_ENTRADA_PARA_DEVOLUCAO", 10)
    estado = pedido.cartao(_linha(data_entrada=dt.date(2026, 11, 29)),
                           agora=AGORA)
    assert "menos de 10 dias" in estado["aviso_cancelamento"]


def test_card_pendente_nao_oferece_cancelar():
    estado = pedido.cartao(_linha(situacao="pendente",
                                  prazo_pagamento=AGORA + dt.timedelta(hours=1),
                                  pix_informado_em=None, lancamentos={}),
                           agora=AGORA)
    assert estado["pode_cancelar"] is False


def test_card_com_restante_em_conferencia_nao_oferece_cancelar():
    estado = pedido.cartao(_linha(restante_informado_em=AGORA), agora=AGORA)
    assert estado["pode_cancelar"] is False


def test_card_no_dia_da_entrada_nao_oferece_cancelar():
    estado = pedido.cartao(_linha(data_entrada=AGORA.date()), agora=AGORA)
    assert estado["pode_cancelar"] is False


def test_o_botao_pela_tela(aplicacao, hospede, conta, chale, recepcao):
    identificador = _confirmada(aplicacao, conta, chale, recepcao)
    html = hospede.get("/reservas/minhas").data.decode()
    card = html[html.index(f'id="reserva-{identificador}"'):]
    card = card[:card.index("</article>")]
    assert "Cancelar reserva" in card
    assert "você recebe de volta tudo o que pagou (R$ 300,00)" in card

    resposta = hospede.post(
        f"/reservas/{identificador}/cancelar",
        data={"csrf_token": _token(hospede, "/reservas/minhas")},
        follow_redirects=True)
    assert "vai devolver R$ 300,00 pelo Pix" in resposta.data.decode()


def test_cancelar_exige_csrf(aplicacao, hospede, conta, chale, recepcao):
    identificador = _confirmada(aplicacao, conta, chale, recepcao)
    aplicacao.config["WTF_CSRF_ENABLED"] = True
    try:
        resposta = hospede.post(f"/reservas/{identificador}/cancelar")
    finally:
        aplicacao.config["WTF_CSRF_ENABLED"] = False
    assert resposta.status_code == 400
    assert _reserva(aplicacao, identificador)["situacao"] == "confirmada"


def test_a_equipe_nao_usa_o_botao_do_hospede(aplicacao, conta, chale, recepcao):
    identificador = _confirmada(aplicacao, conta, chale, recepcao)
    equipe = _entrar(aplicacao, *RECEPCAO)
    assert equipe.post(f"/reservas/{identificador}/cancelar").status_code == 403


# ===========================================================================
# 3. A equipe devolve
# ===========================================================================

def _devolver(aplicacao, identificador, recepcao):
    with aplicacao.app_context():
        return pagamentos.executar(pagamentos.DEVOLVER, identificador, recepcao)


def test_devolucao_do_sinal(aplicacao, conta, chale, recepcao):
    identificador = _confirmada(aplicacao, conta, chale, recepcao)
    _cancelar(aplicacao, conta, identificador)

    with aplicacao.app_context():
        lista = pagamentos.devolucoes(usuario_id=recepcao["id"])
    assert [d["reserva"]["id"] for d in lista] == [identificador]
    assert lista[0]["rotulo"] == "Pix 50% retornado — R$ 300,00"

    assert _devolver(aplicacao, identificador, recepcao)
    with aplicacao.app_context():
        assert pagamento_repo.lancamentos_da_reserva(identificador)[
            "devolucao"] == Decimal("-300.00")
        assert pagamentos.devolucoes(usuario_id=recepcao["id"]) == []


def test_devolucao_de_tudo(aplicacao, conta, chale, recepcao):
    identificador = _confirmada(aplicacao, conta, chale, recepcao, restante=True)
    _cancelar(aplicacao, conta, identificador)
    with aplicacao.app_context():
        lista = pagamentos.devolucoes(usuario_id=recepcao["id"])
    assert lista[0]["rotulo"] == "Pix 100% retornado — R$ 600,00"
    assert _devolver(aplicacao, identificador, recepcao)
    with aplicacao.app_context():
        assert pagamento_repo.lancamentos_da_reserva(identificador)[
            "devolucao"] == Decimal("-600.00")


def test_devolucao_nao_se_repete(aplicacao, conta, chale, recepcao):
    identificador = _confirmada(aplicacao, conta, chale, recepcao)
    _cancelar(aplicacao, conta, identificador)
    assert _devolver(aplicacao, identificador, recepcao)
    assert not _devolver(aplicacao, identificador, recepcao)


def test_sem_direito_nao_ha_devolucao(aplicacao, conta, chale, recepcao):
    """Cancelada a 6 dias da entrada: nem card, nem botão que valha."""
    identificador = _confirmada(aplicacao, conta, chale, recepcao)
    _cancelar(aplicacao, conta, identificador)
    _cancelado_ha(aplicacao, identificador, 6)
    with aplicacao.app_context():
        assert pagamentos.devolucoes(usuario_id=recepcao["id"]) == []
    assert not _devolver(aplicacao, identificador, recepcao)


def test_o_direito_conta_do_dia_do_cancelamento(aplicacao, conta, chale,
                                               recepcao):
    """Cancelada a 7 dias: a equipe pode devolver depois, quando for."""
    identificador = _confirmada(aplicacao, conta, chale, recepcao)
    _cancelar(aplicacao, conta, identificador)
    _cancelado_ha(aplicacao, identificador, 7)
    assert _devolver(aplicacao, identificador, recepcao)


def test_cancelada_pela_equipe_nao_devolve(aplicacao, conta, chale, recepcao):
    identificador = _confirmada(aplicacao, conta, chale, recepcao)
    with aplicacao.app_context():
        pagamentos.executar(pagamentos.CANCELAR, identificador, recepcao)
    assert not _devolver(aplicacao, identificador, recepcao)


def test_reserva_viva_nao_devolve(aplicacao, conta, chale, recepcao):
    identificador = _confirmada(aplicacao, conta, chale, recepcao)
    assert not _devolver(aplicacao, identificador, recepcao)


def test_a_devolucao_vai_para_o_log(aplicacao, conta, chale, recepcao,
                                    conexao_banco):
    identificador = _confirmada(aplicacao, conta, chale, recepcao)
    _cancelar(aplicacao, conta, identificador)
    _devolver(aplicacao, identificador, recepcao)
    cursor = conexao_banco.cursor()
    cursor.execute("SELECT usuario_id FROM log_auditoria WHERE registro_id = %s "
                   "AND acao = 'DEVOLUCAO_REGISTRADA'", (identificador,))
    assert cursor.fetchall() == [(recepcao["id"],)]
    cursor.close()


def test_a_devolucao_sai_do_mes(aplicacao, conta, chale, recepcao):
    identificador = _confirmada(aplicacao, conta, chale, recepcao)
    _cancelar(aplicacao, conta, identificador)
    _devolver(aplicacao, identificador, recepcao)
    with aplicacao.app_context():
        dados = pagamentos.controle(HOJE.year, HOJE.month,
                                    usuario_id=recepcao["id"])
    assert dados["meses"][HOJE.month - 1]["total"] == Decimal("0.00")


def test_o_card_de_devolucao_pela_tela(aplicacao, conta, chale, recepcao):
    identificador = _confirmada(aplicacao, conta, chale, recepcao)
    _cancelar(aplicacao, conta, identificador)

    equipe = _entrar(aplicacao, *RECEPCAO)
    html = equipe.get("/pagamentos").data.decode()
    card = html[html.index(f'id="devolucao-{identificador}"'):]
    card = card[:card.index("</article>")]
    assert "Pix 50% retornado — R$ 300,00" in card
    assert DADOS["nome"] in card
    assert not re.search(r'name="valor"', card)

    resposta = equipe.post(
        f"/pagamentos/{identificador}/devolver",
        data={"csrf_token": _token(equipe, "/pagamentos")},
        follow_redirects=True)
    assert "Devolução registrada" in resposta.data.decode()
