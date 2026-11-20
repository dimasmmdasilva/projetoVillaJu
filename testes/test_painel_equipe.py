"""O Painel da equipe em cards — Etapa 5, Bloco 16.

Pedido do Dimas em 20/11: Painel e Reservas numa tela só, um card por
reserva, para administrador e recepção, com dois selos — pagamento e
estadia — e uma lista que cresce com o tempo.

O que estes testes guardam:

1. **Os selos dizem coisas independentes**: o pagamento concluído não
   conclui a estadia, que segue a data.
2. **Os grupos**: o que precisa de ação no topo, as próximas estadias, e
   o histórico — paginado — com tudo o resto, inclusive o que já acabou.
3. **"Pelo site"** no lugar do login do hóspede, que é o HMAC do e-mail.
4. **Só a equipe** vê o Painel em cards; `/reservas/` leva a ele.
"""

import datetime as dt
import re
from decimal import Decimal

import pytest

from app.dados import conexao
from app.servicos import painel_equipe, pagamentos, pedido

from test_pedido_do_hospede import (  # noqa: F401
    DADOS,
    _entrar,
    _pedir,
    _sql,
    aplicacao,
    chale,
    conta,
    hospede,
)

pytestmark = pytest.mark.banco

ADMIN = ("admin", "Villa@Admin2026")
RECEPCAO = ("recepcao", "Villa@Recep2026")
HOJE = dt.date(2026, 11, 20)


def _usuario(aplicacao, login):
    with aplicacao.app_context():
        with conexao.obter_cursor(dicionario=True) as cursor:
            cursor.execute("SELECT * FROM usuario WHERE login = %s", (login,))
            return cursor.fetchone()


@pytest.fixture
def recepcao(aplicacao):
    return _usuario(aplicacao, RECEPCAO[0])


def _r(**campos):
    base = {
        "id": 1, "situacao": "confirmada", "modalidade": "sinal",
        "data_entrada": dt.date(2026, 12, 4), "data_saida": dt.date(2026, 12, 11),
        "pix_informado_em": None, "restante_informado_em": None,
        "motivo_cancelamento": None, "criado_por_login": "XbS_codigo",
    }
    base.update(campos)
    return base


# ===========================================================================
# 1. Os selos
# ===========================================================================

@pytest.mark.parametrize("campos,lancamentos,esperado", [
    ({"situacao": "pendente"}, {}, "aguardando Pix"),
    ({"situacao": "pendente", "pix_informado_em": dt.datetime(2026, 11, 13)},
     {}, "aguardando conferência"),
    ({}, {"sinal": Decimal("1")}, "50% pago"),
    ({"restante_informado_em": dt.datetime(2026, 11, 20)},
     {"sinal": Decimal("1")}, "restante em conferência"),
    ({}, {"sinal": Decimal("1"), "restante": Decimal("1")}, "concluído"),
    ({"modalidade": "integral"}, {"integral": Decimal("1")}, "concluído"),
    ({"modalidade": None}, {}, "combinado com a recepção"),
])
def test_selo_de_pagamento(campos, lancamentos, esperado):
    assert painel_equipe.selo_pagamento(_r(**campos), lancamentos) == esperado


def test_cancelada_nao_tem_selo_de_pagamento():
    assert painel_equipe.selo_pagamento(_r(situacao="cancelada"), {}) is None


@pytest.mark.parametrize("hoje,esperado", [
    (dt.date(2026, 11, 20), "próxima — 04/12/2026"),
    (dt.date(2026, 12, 4), "em andamento"),
    (dt.date(2026, 12, 10), "em andamento"),
    (dt.date(2026, 12, 11), "concluída"),
])
def test_selo_de_estadia_segue_a_data(hoje, esperado):
    assert painel_equipe.selo_estadia(_r(), hoje) == esperado


def test_pagamento_concluido_nao_conclui_a_estadia():
    """O motivo dos dois selos: a reserva nº 8 do teste do Dimas."""
    reserva = _r()
    lanc = {"sinal": Decimal("1330"), "restante": Decimal("1330")}
    assert painel_equipe.selo_pagamento(reserva, lanc) == "concluído"
    assert painel_equipe.selo_estadia(reserva, HOJE) == "próxima — 04/12/2026"


def test_cancelada_diz_o_motivo():
    assert painel_equipe.selo_estadia(
        _r(situacao="cancelada", motivo_cancelamento="falta_de_pagamento"),
        HOJE) == "cancelada — falta de pagamento"


def test_concluida_no_banco_e_concluida_na_tela():
    assert painel_equipe.selo_estadia(
        _r(situacao="concluida", data_saida=dt.date(2027, 1, 1)), HOJE
    ) == "concluída"


def test_pedido_do_site_diz_pelo_site_e_nao_o_codigo():
    assert painel_equipe.autor(_r()) == "pelo site"
    assert painel_equipe.autor(_r(modalidade=None,
                                  criado_por_login="recepcao")) == "recepcao"


# ===========================================================================
# 2. Os grupos
# ===========================================================================

def test_grupo_de_acao_vem_dos_urgentes_e_das_devolucoes():
    assert painel_equipe.grupo(_r(id=5), HOJE, {5}, set()) == painel_equipe.ACAO
    assert painel_equipe.grupo(_r(id=5, situacao="cancelada"), HOJE, set(),
                               {5}) == painel_equipe.ACAO


def test_viva_e_futura_e_proxima():
    assert painel_equipe.grupo(_r(), HOJE, set(), set()) == painel_equipe.PROXIMAS


def test_terminada_cancelada_e_paga_vao_ao_historico():
    passada = _r(data_entrada=dt.date(2026, 7, 4), data_saida=dt.date(2026, 7, 8))
    assert painel_equipe.grupo(passada, HOJE, set(), set()) == painel_equipe.HISTORICO
    assert painel_equipe.grupo(_r(situacao="cancelada"), HOJE, set(),
                               set()) == painel_equipe.HISTORICO


def test_no_dia_da_saida_vai_ao_historico():
    assert painel_equipe.grupo(_r(data_saida=HOJE), HOJE, set(),
                               set()) == painel_equipe.HISTORICO


def _montar(aplicacao, usuario, **kwargs):
    with aplicacao.app_context():
        return painel_equipe.montar(usuario["id"], **kwargs)


def test_pix_informado_vai_para_acao_com_o_botao(aplicacao, conta, chale,
                                                 recepcao):
    identificador = _pedir(aplicacao, conta, chale)
    with aplicacao.app_context():
        pedido.informar_pix(conta, identificador, pedido.PRIMEIRO)
    dados = _montar(aplicacao, recepcao)
    assert [c["reserva"]["id"] for c in dados["acao"]] == [identificador]
    card = dados["acao"][0]
    assert card["botao"] == {"rotulo": "Conferir pagamento",
                             "ancora": f"pagamento-{identificador}"}
    assert card["pagamento"] == "aguardando conferência"
    assert card["titular"] == DADOS["nome"]
    assert card["autor"] == "pelo site"


def test_pago_continua_no_painel_em_proximas(aplicacao, conta, chale, recepcao):
    """A lista cresce: quitada não some — vai para próximas, sem botão."""
    identificador = _pedir(aplicacao, conta, chale, entrada=3)
    with aplicacao.app_context():
        pagamentos.executar(pagamentos.PRIMEIRO, identificador, recepcao)
    dados = _montar(aplicacao, recepcao)
    card = next(c for c in dados["proximas"] if c["reserva"]["id"] == identificador)
    assert card["pagamento"] == "concluído"
    assert card["estadia"].startswith("próxima")
    assert card["botao"] is None
    assert card["valor_pago"] == Decimal("600.00")


def test_aguardando_o_hospede_vai_para_proximas_com_botao(aplicacao, conta,
                                                          chale, recepcao):
    """Sem urgência, mas com pagamento em aberto: o botão leva ao card."""
    identificador = _pedir(aplicacao, conta, chale)
    dados = _montar(aplicacao, recepcao)
    card = next(c for c in dados["proximas"] if c["reserva"]["id"] == identificador)
    assert card["botao"]["rotulo"] == "Conferir pagamento"


def test_devolucao_pendente_vai_para_acao(aplicacao, conta, chale, recepcao):
    identificador = _pedir(aplicacao, conta, chale, entrada=20)
    with aplicacao.app_context():
        pagamentos.executar(pagamentos.PRIMEIRO, identificador, recepcao)
        pedido.cancelar(conta, identificador)
    dados = _montar(aplicacao, recepcao)
    card = next(c for c in dados["acao"] if c["reserva"]["id"] == identificador)
    assert card["botao"] == {"rotulo": "Registrar devolução",
                             "ancora": f"devolucao-{identificador}"}
    assert card["estadia"] == "cancelada — a pedido do hóspede"


def test_proximas_vem_da_entrada_mais_proxima(aplicacao, conta, chale, recepcao):
    longe = _pedir(aplicacao, conta, chale, entrada=40)
    _sql(aplicacao, "UPDATE reserva SET situacao = 'confirmada' WHERE id = %s",
         (longe,))
    perto = _pedir(aplicacao, conta, chale, entrada=10)
    ids = [c["reserva"]["id"] for c in _montar(aplicacao, recepcao)["proximas"]]
    assert ids.index(perto) < ids.index(longe)


def test_o_historico_e_paginado(aplicacao, conta, chale, recepcao,
                                monkeypatch):
    monkeypatch.setattr(painel_equipe, "POR_PAGINA", 2)
    for dias in (100, 110, 120):
        identificador = _pedir(aplicacao, conta, chale, entrada=dias)
        with aplicacao.app_context():
            pedido.desistir(conta, identificador)

    primeira = _montar(aplicacao, recepcao, pagina=1)
    segunda = _montar(aplicacao, recepcao, pagina=2)
    assert primeira["paginas"] == 2 and len(primeira["historico"]) == 2
    assert len(segunda["historico"]) == 1
    # Do mais recente (entrada mais distante) ao mais antigo.
    entradas = [c["reserva"]["data_entrada"] for c in primeira["historico"]]
    assert entradas == sorted(entradas, reverse=True)
    # Página fora da faixa volta para dentro dela, sem erro.
    assert _montar(aplicacao, recepcao, pagina=99)["pagina"] == 2
    assert _montar(aplicacao, recepcao, pagina="x")["pagina"] == 1


def test_o_historico_nao_decifra_outras_paginas(aplicacao, conta, chale,
                                                recepcao, monkeypatch):
    monkeypatch.setattr(painel_equipe, "POR_PAGINA", 1)
    for dias in (100, 110, 120):
        identificador = _pedir(aplicacao, conta, chale, entrada=dias)
        with aplicacao.app_context():
            pedido.desistir(conta, identificador)
    chamadas = []
    original = painel_equipe._titular
    monkeypatch.setattr(painel_equipe, "_titular",
                        lambda *a: chamadas.append(a) or original(*a))
    _montar(aplicacao, recepcao)
    assert len(chamadas) == 1


def test_o_filtro_por_situacao(aplicacao, conta, chale, recepcao):
    viva = _pedir(aplicacao, conta, chale, entrada=10)
    dados = _montar(aplicacao, recepcao, situacao="cancelada")
    todos = dados["acao"] + dados["proximas"] + dados["historico"]
    assert viva not in [c["reserva"]["id"] for c in todos]


# ===========================================================================
# 3. A tela e os perfis
# ===========================================================================

def test_recepcao_e_admin_veem_o_mesmo_painel(aplicacao, conta, chale):
    identificador = _pedir(aplicacao, conta, chale)
    for login in (RECEPCAO, ADMIN):
        html = _entrar(aplicacao, *login).get("/painel").data.decode()
        assert f'id="painel-reserva-{identificador}"' in html
        assert "Pagamento: <strong>aguardando Pix</strong>" in html
        assert "Estadia: <strong>próxima" in html


def test_o_hospede_nao_ve_os_cards(aplicacao, hospede, conta, chale):
    identificador = _pedir(aplicacao, conta, chale)
    html = hospede.get("/painel").data.decode()
    assert f"painel-reserva-{identificador}" not in html
    assert "Precisa de ação" not in html


def test_o_card_leva_ao_pagamento_e_a_reserva(aplicacao, conta, chale):
    identificador = _pedir(aplicacao, conta, chale)
    html = _entrar(aplicacao, *RECEPCAO).get("/painel").data.decode()
    card = html[html.index(f'id="painel-reserva-{identificador}"'):]
    card = card[:card.index("</article>")]
    assert f'href="/pagamentos#pagamento-{identificador}"' in card
    assert f'href="/reservas/{identificador}"' in card
    assert re.search(r'class="foto-provisoria amostra--\d+', card)
    assert "pelo site" in card
    assert "XbS_" not in card


def test_reservas_leva_ao_painel_com_o_filtro(aplicacao):
    resposta = _entrar(aplicacao, *RECEPCAO).get("/reservas/?situacao=cancelada")
    assert resposta.status_code == 302
    assert resposta.headers["Location"].endswith("/painel?situacao=cancelada")


def test_a_barra_nao_tem_mais_reservas(aplicacao):
    html = _entrar(aplicacao, *ADMIN).get("/painel").data.decode()
    barra = html[html.index("<nav>"):html.index("</nav>")]
    assert 'href="/reservas/"' not in barra
    assert "Pagamentos" in barra


def test_o_detalhe_diz_pelo_site(aplicacao, conta, chale):
    identificador = _pedir(aplicacao, conta, chale)
    html = _entrar(aplicacao, *RECEPCAO).get(
        f"/reservas/{identificador}").data.decode()
    assert re.search(r"Registrada por</th><td>pelo site</td>", html)


def test_toda_cor_de_card_existe_na_folha_de_estilo(aplicacao, conta, chale):
    """O card usava a classe da vitrine, que tem 5 cores; `cor_da_foto` dá
    12. As cores 6 a 12 saíam em branco — o Dimas viu nos prints. O teste
    confere no lugar certo: a folha de estilo, para cada chalé."""
    import pathlib
    from app.rotas.publico import cor_da_foto, TOTAL_DE_AMOSTRAS

    css = (pathlib.Path(__file__).resolve().parents[1]
           / "app/static/css/villaju.css").read_text(encoding="utf-8")
    definidas = {int(n) for n in re.findall(r"\.amostra--(\d+)\s*\{", css)}
    assert set(range(1, TOTAL_DE_AMOSTRAS + 1)) <= definidas
    for chale_id in range(1, 40):
        assert cor_da_foto(chale_id, 1) in definidas

    identificador = _pedir(aplicacao, conta, chale)
    html = _entrar(aplicacao, *RECEPCAO).get("/painel").data.decode()
    card = html[html.index(f'id="painel-reserva-{identificador}"'):]
    usada = int(re.search(r'amostra--(\d+)', card).group(1))
    assert usada in definidas
