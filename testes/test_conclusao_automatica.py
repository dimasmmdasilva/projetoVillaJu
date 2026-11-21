"""A reserva se conclui sozinha — Etapa 5, Bloco 17d.

Decisão do Dimas em 21/11: o "Marcar como concluída" sai. A reserva passa a
`concluida` quando DUAS condições se encontram:

1. **o pagamento está quitado** — o Pix integral, ou o restante depois do
   sinal, conferido pela equipe;
2. **a data de saída chegou.**

O Pix 100% sozinho não basta: costuma ser conferido antes da estadia, e a
reserva pareceria encerrada antes de o hóspede chegar. E a saída sozinha
também não: com o restante em aberto, a reserva tem de continuar à vista
da equipe em Pagamentos, que só olha as pendentes e as confirmadas.
"""

import pytest

from app.dados import conexao, reserva_repo
from app.servicos import pagamentos

from test_pedido_do_hospede import (  # noqa: F401
    _entrar,
    _pedir,
    _reserva,
    _sql,
    _token,
    aplicacao,
    chale,
    conta,
)

pytestmark = pytest.mark.banco

ADMIN = ("admin", "Villa@Admin2026")
RECEPCAO = ("recepcao", "Villa@Recep2026")


@pytest.fixture
def recepcao(aplicacao):
    with aplicacao.app_context():
        with conexao.obter_cursor(dicionario=True) as cursor:
            cursor.execute("SELECT * FROM usuario WHERE login = 'recepcao'")
            return cursor.fetchone()


def _conferir(aplicacao, acao, identificador, usuario):
    with aplicacao.app_context():
        assert pagamentos.executar(acao, identificador, usuario)


def _terminar_em(aplicacao, identificador, dias_ate_a_saida):
    """Leva a estadia para o passado (ou para hoje). Entrada 5 dias antes
    da saída, para a ordem das datas continuar válida."""
    _sql(aplicacao,
         "UPDATE reserva SET data_entrada = CURDATE() + INTERVAL %s DAY, "
         "                   data_saida = CURDATE() + INTERVAL %s DAY "
         "WHERE id = %s",
         (dias_ate_a_saida - 5, dias_ate_a_saida, identificador))


def _varrer(aplicacao):
    with aplicacao.app_context():
        return reserva_repo.concluir_estadias_encerradas()


def _situacao(aplicacao, identificador):
    return _reserva(aplicacao, identificador)["situacao"]


# ===========================================================================
# 1. As duas condições
# ===========================================================================

def test_integral_quitado_e_saida_passada_conclui(aplicacao, conta, chale,
                                                  recepcao):
    identificador = _pedir(aplicacao, conta, chale, entrada=3)
    _conferir(aplicacao, pagamentos.PRIMEIRO, identificador, recepcao)
    _terminar_em(aplicacao, identificador, -1)
    assert _varrer(aplicacao) == 1
    assert _situacao(aplicacao, identificador) == "concluida"


def test_sinal_e_restante_quitados_concluem(aplicacao, conta, chale, recepcao):
    identificador = _pedir(aplicacao, conta, chale, entrada=10)
    _conferir(aplicacao, pagamentos.PRIMEIRO, identificador, recepcao)
    _conferir(aplicacao, pagamentos.RESTANTE, identificador, recepcao)
    _terminar_em(aplicacao, identificador, -1)
    _varrer(aplicacao)
    assert _situacao(aplicacao, identificador) == "concluida"


def test_no_dia_da_saida_ja_conclui(aplicacao, conta, chale, recepcao):
    """`<=` e não `<`: no dia da saída o hóspede vai embora, e o selo da
    estadia já diz "concluída" desde a manhã."""
    identificador = _pedir(aplicacao, conta, chale, entrada=3)
    _conferir(aplicacao, pagamentos.PRIMEIRO, identificador, recepcao)
    _terminar_em(aplicacao, identificador, 0)
    _varrer(aplicacao)
    assert _situacao(aplicacao, identificador) == "concluida"


def test_quitada_antes_da_saida_continua_confirmada(aplicacao, conta, chale,
                                                    recepcao):
    """O Pix 100% sozinho não basta."""
    identificador = _pedir(aplicacao, conta, chale, entrada=3)
    _conferir(aplicacao, pagamentos.PRIMEIRO, identificador, recepcao)
    _terminar_em(aplicacao, identificador, 1)
    assert _varrer(aplicacao) == 0
    assert _situacao(aplicacao, identificador) == "confirmada"


def test_restante_em_aberto_continua_confirmada_e_a_vista(aplicacao, conta,
                                                          chale, recepcao):
    """A saída sozinha não basta, e a cobrança não some de Pagamentos."""
    identificador = _pedir(aplicacao, conta, chale, entrada=10)
    _conferir(aplicacao, pagamentos.PRIMEIRO, identificador, recepcao)
    _terminar_em(aplicacao, identificador, 0)
    _varrer(aplicacao)
    assert _situacao(aplicacao, identificador) == "confirmada"
    cards = _entrar(aplicacao, *RECEPCAO).get("/pagamentos").data.decode()
    assert f'id="pagamento-{identificador}"' in cards

    # Conferido o restante, a varredura seguinte conclui.
    _conferir(aplicacao, pagamentos.RESTANTE, identificador, recepcao)
    _varrer(aplicacao)
    assert _situacao(aplicacao, identificador) == "concluida"


def test_pendente_nunca_conclui(aplicacao, conta, chale):
    """Uma pendente ANTIGA (sem modalidade), com a saída passada: a única
    coisa que a segura é a condição `situacao = 'confirmada'`.

    A primeira versão usava uma pendente do site, sem pagamento — que a
    condição do pagamento já barrava. A sabotagem que deixava concluir
    pendentes passou despercebida: o teste não exercitava o que dizia.
    """
    identificador = _pedir(aplicacao, conta, chale, entrada=3)
    _sql(aplicacao, "UPDATE reserva SET modalidade = NULL, prazo_pagamento = NULL "
                    "WHERE id = %s", (identificador,))
    _terminar_em(aplicacao, identificador, -1)
    _varrer(aplicacao)
    assert _situacao(aplicacao, identificador) == "pendente"


def test_a_reserva_antiga_conclui_pela_data(aplicacao, conta, chale):
    """Sem modalidade, nunca entrou no controle do Pix: não há lançamento a
    esperar."""
    identificador = _pedir(aplicacao, conta, chale, entrada=3)
    _sql(aplicacao, "UPDATE reserva SET modalidade = NULL, prazo_pagamento = NULL, "
                    "situacao = 'confirmada' WHERE id = %s", (identificador,))
    _terminar_em(aplicacao, identificador, -1)
    _varrer(aplicacao)
    assert _situacao(aplicacao, identificador) == "concluida"


def test_a_varredura_e_idempotente(aplicacao, conta, chale, recepcao):
    identificador = _pedir(aplicacao, conta, chale, entrada=3)
    _conferir(aplicacao, pagamentos.PRIMEIRO, identificador, recepcao)
    _terminar_em(aplicacao, identificador, -1)
    assert _varrer(aplicacao) == 1
    assert _varrer(aplicacao) == 0


# ===========================================================================
# 2. Quem dispara, e o botão que saiu
# ===========================================================================

def test_qualquer_requisicao_dispara_a_varredura(aplicacao, conta, chale,
                                                 recepcao):
    identificador = _pedir(aplicacao, conta, chale, entrada=3)
    _conferir(aplicacao, pagamentos.PRIMEIRO, identificador, recepcao)
    _terminar_em(aplicacao, identificador, -1)
    aplicacao.varredura_de_pedidos["ultima"] = 0.0
    aplicacao.test_client().get("/")
    assert _situacao(aplicacao, identificador) == "concluida"


def test_o_detalhe_nao_oferece_concluir(aplicacao, conta, chale, recepcao):
    identificador = _pedir(aplicacao, conta, chale, entrada=3)
    _conferir(aplicacao, pagamentos.PRIMEIRO, identificador, recepcao)
    pagina = _entrar(aplicacao, *ADMIN).get(f"/reservas/{identificador}").data.decode()
    assert "Marcar como concluida" not in pagina
    assert "Cancelar pela pousada" in pagina and "Cancelar a pedido do hóspede" in pagina
    assert "concluída automaticamente" in pagina


@pytest.mark.parametrize("credenciais", [ADMIN, RECEPCAO])
def test_envio_forjado_de_concluida_e_recusado(aplicacao, conta, chale,
                                               recepcao, credenciais):
    identificador = _pedir(aplicacao, conta, chale, entrada=3)
    _conferir(aplicacao, pagamentos.PRIMEIRO, identificador, recepcao)
    cliente = _entrar(aplicacao, *credenciais)
    rota = f"/reservas/{identificador}"
    resposta = cliente.post(
        f"{rota}/situacao",
        data={"situacao": "concluida", "csrf_token": _token(cliente, rota)},
        follow_redirects=True,
    )
    assert "só pode passar para cancelada" in resposta.data.decode()
    assert _situacao(aplicacao, identificador) == "confirmada"
