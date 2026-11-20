"""A equipe confere o Pix — Etapa 5, Bloco 8.

O que estes testes guardam:

1. **O valor nunca vem do formulário** e sai da leitura travada da reserva.
2. **Dois cliques não lançam duas vezes**, e o clique errado é recusado.
3. **A urgência ordena os cards**, e a varredura roda antes da leitura.
4. **O card do hóspede mostra o caminho inteiro**, e "Valor pago" soma
   só o que a equipe conferiu.
5. **O controle é só do administrador.** A recepção usa os cards, mas não
   vê a receita.
"""

import datetime as dt
import re
from decimal import Decimal

import pytest

from app.dados import acompanhante_repo, conexao, pagamento_repo, reserva_repo
from app.servicos import pagamentos, pedido

from test_pedido_do_hospede import (  # noqa: F401
    DADOS,
    OUTRA,
    _confirmar,
    _entrar,
    _escolha,
    _pedir,
    _reserva,
    _sql,
    _token,
    _vencer,
    aplicacao,
    chale,
    conta,
    hospede,
    outra,
)

pytestmark = pytest.mark.banco

ADMIN = ("admin", "Villa@Admin2026")
RECEPCAO = ("recepcao", "Villa@Recep2026")


def _lancamentos(aplicacao, identificador):
    with aplicacao.app_context():
        return pagamento_repo.lancamentos_da_reserva(identificador)


def _usuario(aplicacao, login):
    with aplicacao.app_context():
        with conexao.obter_cursor(dicionario=True) as cursor:
            cursor.execute("SELECT * FROM usuario WHERE login = %s", (login,))
            return cursor.fetchone()


@pytest.fixture
def recepcao(aplicacao):
    return _usuario(aplicacao, RECEPCAO[0])


def _agir(aplicacao, acao, identificador, usuario):
    with aplicacao.app_context():
        return pagamentos.executar(acao, identificador, usuario)


def _informar(aplicacao, conta, identificador, etapa=pedido.PRIMEIRO):
    with aplicacao.app_context():
        assert pedido.informar_pix(conta, identificador, etapa)


# ===========================================================================
# 1. As três ações
# ===========================================================================

def test_pagou_50_confirma_e_lanca_o_sinal(aplicacao, conta, chale, recepcao):
    identificador = _pedir(aplicacao, conta, chale, entrada=10, noites=2)
    assert _agir(aplicacao, pagamentos.PRIMEIRO, identificador, recepcao)

    assert _reserva(aplicacao, identificador)["situacao"] == "confirmada"
    assert _lancamentos(aplicacao, identificador) == {"sinal": Decimal("300.00")}


def test_pagou_100_em_cima_da_hora_lanca_o_integral(aplicacao, conta, chale,
                                                    recepcao):
    identificador = _pedir(aplicacao, conta, chale, entrada=3, noites=2)
    assert _agir(aplicacao, pagamentos.PRIMEIRO, identificador, recepcao)
    assert _lancamentos(aplicacao, identificador) == {
        "integral": Decimal("600.00")
    }


def test_o_restante_completa_o_total(aplicacao, conta, chale, recepcao):
    """Com centavo ímpar: o restante é a diferença, não outra metade."""
    identificador = _pedir(aplicacao, conta, chale, entrada=10, noites=2)
    _sql(aplicacao, "UPDATE reserva SET valor_total = 600.01 WHERE id = %s",
         (identificador,))
    assert _agir(aplicacao, pagamentos.PRIMEIRO, identificador, recepcao)
    assert _agir(aplicacao, pagamentos.RESTANTE, identificador, recepcao)

    lancado = _lancamentos(aplicacao, identificador)
    assert lancado == {"sinal": Decimal("300.01"), "restante": Decimal("300.00")}
    assert sum(lancado.values()) == Decimal("600.01")


def test_o_autor_e_quem_clicou(aplicacao, conta, chale, recepcao):
    identificador = _pedir(aplicacao, conta, chale)
    _agir(aplicacao, pagamentos.PRIMEIRO, identificador, recepcao)
    with aplicacao.app_context():
        with conexao.obter_cursor() as cursor:
            cursor.execute(
                "SELECT registrado_por, registrado_em FROM pagamento "
                "WHERE reserva_id = %s", (identificador,))
            autor, quando = cursor.fetchone()
    assert autor == recepcao["id"]
    assert quando is not None


def test_dois_cliques_nao_lancam_duas_vezes(aplicacao, conta, chale, recepcao):
    identificador = _pedir(aplicacao, conta, chale)
    assert _agir(aplicacao, pagamentos.PRIMEIRO, identificador, recepcao)
    assert not _agir(aplicacao, pagamentos.PRIMEIRO, identificador, recepcao)
    assert _agir(aplicacao, pagamentos.RESTANTE, identificador, recepcao)
    assert not _agir(aplicacao, pagamentos.RESTANTE, identificador, recepcao)
    assert len(_lancamentos(aplicacao, identificador)) == 2


def test_restante_antes_do_sinal_e_recusado(aplicacao, conta, chale, recepcao):
    identificador = _pedir(aplicacao, conta, chale)
    assert not _agir(aplicacao, pagamentos.RESTANTE, identificador, recepcao)
    assert _lancamentos(aplicacao, identificador) == {}


def test_restante_no_integral_e_recusado(aplicacao, conta, chale, recepcao):
    identificador = _pedir(aplicacao, conta, chale, entrada=3)
    _agir(aplicacao, pagamentos.PRIMEIRO, identificador, recepcao)
    assert not _agir(aplicacao, pagamentos.RESTANTE, identificador, recepcao)


def test_restante_em_reserva_cancelada_e_recusado(aplicacao, conta, chale,
                                                 recepcao):
    """Sinal lançado, depois cancelada: o restante não entra no livro.

    Achado pela sabotagem 7 do Bloco 8 — era o único caso em que a
    exigência de reserva confirmada fazia diferença, e nenhum teste o
    exercitava.
    """
    identificador = _pedir(aplicacao, conta, chale)
    assert _agir(aplicacao, pagamentos.PRIMEIRO, identificador, recepcao)
    assert _agir(aplicacao, pagamentos.CANCELAR, identificador, recepcao)
    assert not _agir(aplicacao, pagamentos.RESTANTE, identificador, recepcao)
    assert _lancamentos(aplicacao, identificador) == {"sinal": Decimal("300.00")}


def test_restante_exige_o_sinal_lancado(aplicacao, conta, chale, recepcao):
    """Uma reserva confirmada sem lançamento de sinal — à mão, no banco —
    não aceita o restante: o livro ficaria sem a primeira metade."""
    identificador = _pedir(aplicacao, conta, chale)
    _confirmar(aplicacao, identificador)
    assert not _agir(aplicacao, pagamentos.RESTANTE, identificador, recepcao)


def test_confirmar_reserva_cancelada_e_recusado(aplicacao, conta, chale,
                                                recepcao):
    identificador = _pedir(aplicacao, conta, chale)
    with aplicacao.app_context():
        assert pedido.desistir(conta, identificador)
    assert not _agir(aplicacao, pagamentos.PRIMEIRO, identificador, recepcao)
    assert _lancamentos(aplicacao, identificador) == {}


def test_reserva_da_recepcao_nao_entra_no_livro(aplicacao, conta, chale,
                                                recepcao):
    """Sem modalidade, segue o fluxo manual de antes."""
    with aplicacao.app_context():
        identificador = reserva_repo.criar({
            "chale_id": chale["id"], "hospede_id": conta["hospede_id"],
            "data_entrada": _escolha(chale, entrada=50)["entrada"],
            "data_saida": _escolha(chale, entrada=50)["saida"],
            "num_pessoas": 2, "valor_total": "600.00",
        }, usuario_id=recepcao["id"])
    assert not _agir(aplicacao, pagamentos.PRIMEIRO, identificador, recepcao)


def test_reserva_inexistente_e_recusada(aplicacao, recepcao):
    assert not _agir(aplicacao, pagamentos.PRIMEIRO, 999999, recepcao)
    assert not _agir(aplicacao, pagamentos.CANCELAR, 999999, recepcao)


def test_acao_desconhecida_e_recusada(aplicacao, conta, chale, recepcao):
    identificador = _pedir(aplicacao, conta, chale)
    assert not _agir(aplicacao, "dobrar", identificador, recepcao)


def test_cancelar_por_falta_libera_o_chale(aplicacao, conta, chale, recepcao):
    identificador = _pedir(aplicacao, conta, chale)
    _informar(aplicacao, conta, identificador)
    assert _agir(aplicacao, pagamentos.CANCELAR, identificador, recepcao)

    linha = _reserva(aplicacao, identificador)
    assert linha["situacao"] == "cancelada"
    assert linha["motivo_cancelamento"] == "falta_de_pagamento"
    assert linha["cancelado_em"] is not None
    with aplicacao.app_context():
        assert reserva_repo.chale_disponivel(
            chale["id"], linha["data_entrada"], linha["data_saida"])


def test_nao_se_cancela_por_falta_quem_pagou_tudo(aplicacao, conta, chale,
                                                  recepcao):
    identificador = _pedir(aplicacao, conta, chale, entrada=3)
    _agir(aplicacao, pagamentos.PRIMEIRO, identificador, recepcao)
    assert not _agir(aplicacao, pagamentos.CANCELAR, identificador, recepcao)
    assert _reserva(aplicacao, identificador)["situacao"] == "confirmada"


def test_cancelar_com_sinal_pago_mantem_o_lancamento(aplicacao, conta, chale,
                                                     recepcao):
    """O livro só cresce: o sinal conferido continua lá."""
    identificador = _pedir(aplicacao, conta, chale)
    _agir(aplicacao, pagamentos.PRIMEIRO, identificador, recepcao)
    assert _agir(aplicacao, pagamentos.CANCELAR, identificador, recepcao)
    assert _lancamentos(aplicacao, identificador) == {"sinal": Decimal("300.00")}


def test_as_acoes_vao_para_o_log(aplicacao, conta, chale, recepcao,
                                 conexao_banco):
    identificador = _pedir(aplicacao, conta, chale)
    _agir(aplicacao, pagamentos.PRIMEIRO, identificador, recepcao)
    _agir(aplicacao, pagamentos.CANCELAR, identificador, recepcao)

    cursor = conexao_banco.cursor()
    cursor.execute(
        "SELECT acao, usuario_id FROM log_auditoria WHERE registro_id = %s "
        "AND acao IN ('PAGAMENTO_CONFIRMADO', 'CANCELADA_SEM_PAGAMENTO') "
        "ORDER BY id", (identificador,))
    linhas = cursor.fetchall()
    cursor.close()
    assert linhas == [
        ("PAGAMENTO_CONFIRMADO", recepcao["id"]),
        ("CANCELADA_SEM_PAGAMENTO", recepcao["id"]),
    ]


def test_o_valor_sai_da_leitura_travada(aplicacao, conta, chale, recepcao):
    """O repositório divide o valor que ELE leu com a trava — a função de
    divisão recebe exatamente o total gravado na reserva."""
    identificador = _pedir(aplicacao, conta, chale)
    vistos = []

    def espiao(total, modalidade):
        vistos.append((total, modalidade))
        return pedido.dividir(total, modalidade)

    with aplicacao.app_context():
        assert pagamento_repo.confirmar_primeiro(
            identificador, espiao, usuario_id=recepcao["id"])
    assert vistos == [(Decimal("600.00"), "sinal")]


# ===========================================================================
# 2. Os cards: urgência, varredura, lista completa
# ===========================================================================

AGORA = dt.datetime(2026, 11, 20, 12, 0, 0)


def _linha(**campos):
    base = {
        "id": 1, "situacao": "pendente", "modalidade": "sinal",
        "valor_total": Decimal("1300.00"),
        "prazo_pagamento": AGORA + dt.timedelta(minutes=60),
        "prazo_restante": AGORA + dt.timedelta(days=20),
        "pix_informado_em": None, "restante_informado_em": None,
    }
    base.update(campos)
    return base


def test_pix_informado_e_o_mais_urgente():
    estado = pagamentos.avaliar(
        _linha(pix_informado_em=AGORA), {}, AGORA)
    assert estado["urgencia"] == pagamentos.URGENCIA_PIX_INFORMADO
    assert estado["pode_cancelar"] is True
    assert estado["botoes"][0]["rotulo"] == "Pagou 50% Pix — R$ 650,00"


def test_sem_declaracao_nao_se_cancela_pelo_card():
    """Dentro dos 90 minutos e sem "Fiz o Pix": a varredura cuida."""
    estado = pagamentos.avaliar(_linha(), {}, AGORA)
    assert estado["urgencia"] == pagamentos.URGENCIA_AGUARDANDO
    assert estado["pode_cancelar"] is False


def test_integral_pendente_oferece_pagou_100():
    estado = pagamentos.avaliar(
        _linha(modalidade="integral", prazo_restante=None), {}, AGORA)
    assert estado["botoes"][0]["rotulo"] == "Pagou 100% Pix — R$ 1.300,00"


def test_restante_vencendo_em_dois_dias_e_urgente():
    estado = pagamentos.avaliar(
        _linha(situacao="confirmada",
               prazo_restante=AGORA + dt.timedelta(days=2)),
        {"sinal": Decimal("650.00")}, AGORA)
    assert estado["urgencia"] == pagamentos.URGENCIA_RESTANTE
    assert estado["pode_cancelar"] is False
    assert estado["botoes"][0]["acao"] == pagamentos.RESTANTE


def test_restante_longe_aguarda():
    estado = pagamentos.avaliar(
        _linha(situacao="confirmada",
               prazo_restante=AGORA + dt.timedelta(days=2, seconds=1)),
        {"sinal": Decimal("650.00")}, AGORA)
    assert estado["urgencia"] == pagamentos.URGENCIA_AGUARDANDO


def test_restante_vencido_permite_cancelar():
    estado = pagamentos.avaliar(
        _linha(situacao="confirmada", prazo_restante=AGORA),
        {"sinal": Decimal("650.00")}, AGORA)
    assert estado["urgencia"] == pagamentos.URGENCIA_RESTANTE
    assert estado["pode_cancelar"] is True


def test_restante_informado_e_o_mais_urgente():
    estado = pagamentos.avaliar(
        _linha(situacao="confirmada", restante_informado_em=AGORA),
        {"sinal": Decimal("650.00")}, AGORA)
    assert estado["urgencia"] == pagamentos.URGENCIA_PIX_INFORMADO
    assert estado["pode_cancelar"] is True


def test_valor_pago_soma_so_o_conferido():
    estado = pagamentos.avaliar(
        _linha(situacao="confirmada", restante_informado_em=AGORA),
        {"sinal": Decimal("650.00")}, AGORA)
    assert estado["valor_pago"] == Decimal("650.00")


def test_os_cards_vem_por_urgencia(aplicacao, conta, outra, chale, recepcao):
    """O pedido com Pix informado vem antes, mesmo entrando depois."""
    primeiro = _pedir(aplicacao, conta, chale, entrada=10)
    segundo = _pedir(aplicacao, outra, chale, entrada=30)
    _informar(aplicacao, outra, segundo)

    with aplicacao.app_context():
        lista = pagamentos.cards(usuario_id=recepcao["id"])
    assert [c["reserva"]["id"] for c in lista] == [segundo, primeiro]


def test_quitada_sai_dos_cards(aplicacao, conta, chale, recepcao):
    identificador = _pedir(aplicacao, conta, chale, entrada=3)
    _agir(aplicacao, pagamentos.PRIMEIRO, identificador, recepcao)
    with aplicacao.app_context():
        assert pagamentos.cards(usuario_id=recepcao["id"]) == []


def test_os_cards_varrem_antes_de_ler(aplicacao, conta, chale, recepcao):
    """Um pedido vencido, sem Pix e ainda não cancelado não é pendência.

    O gancho de requisição varre de todo modo — por isso o teste chama o
    serviço direto, sem requisição: só a varredura de `cards` o tira.
    """
    identificador = _pedir(aplicacao, conta, chale)
    _vencer(aplicacao, identificador)
    with aplicacao.app_context():
        lista = pagamentos.cards(usuario_id=recepcao["id"])
    assert lista == []
    assert _reserva(aplicacao, identificador)["situacao"] == "cancelada"


def test_o_card_da_equipe_tem_a_lista_completa(aplicacao, conta, chale):
    dados = _escolha(chale, pessoas=3)
    dados["acompanhante_2_nome"] = "Criança Prado"
    dados["acompanhante_2_documento"] = ""
    dados["acompanhante_2_menor"] = "sim"
    with aplicacao.app_context():
        identificador = pedido.pedir(conta, dados)

    equipe = _entrar(aplicacao, *RECEPCAO)
    html = equipe.get("/pagamentos").data.decode()
    card = html[html.index(f'id="pagamento-{identificador}"'):]
    card = card[:card.index("</article>")]
    itens = re.findall(r"<li>(.*?)</li>", card, re.S)

    assert DADOS["nome"] in itens[0] and "titular" in itens[0]
    assert DADOS["documento"] in itens[0], "a equipe vê o documento inteiro"
    assert DADOS["telefone"] in itens[0]
    assert "Acompanhante Teste 1" in itens[1] and "RG0000001" in itens[1]
    assert "Criança Prado" in itens[2] and "menor de idade" in itens[2]


def test_o_card_nao_tem_campo_de_valor(aplicacao, conta, chale):
    identificador = _pedir(aplicacao, conta, chale)
    equipe = _entrar(aplicacao, *RECEPCAO)
    html = equipe.get("/pagamentos").data.decode()
    card = html[html.index(f'id="pagamento-{identificador}"'):]
    card = card[:card.index("</article>")]
    nomes = set(re.findall(r'name="([^"]+)"', card))
    assert nomes == {"csrf_token"}


# ===========================================================================
# 3. As rotas e os perfis
# ===========================================================================

def test_recepcao_e_admin_veem_os_cards(aplicacao):
    for login in (RECEPCAO, ADMIN):
        assert _entrar(aplicacao, *login).get("/pagamentos").status_code == 200


def test_hospede_nao_ve_os_cards(aplicacao, hospede):
    assert hospede.get("/pagamentos").status_code == 403


def test_hospede_nao_confirma_o_proprio_pix(aplicacao, hospede, conta, chale):
    identificador = _pedir(aplicacao, conta, chale)
    resposta = hospede.post(f"/pagamentos/{identificador}/primeiro",
                            data={"csrf_token": _token(hospede, "/reservas/minhas")})
    assert resposta.status_code == 403
    assert _lancamentos(aplicacao, identificador) == {}


def test_o_botao_pela_tela(aplicacao, conta, chale):
    identificador = _pedir(aplicacao, conta, chale)
    equipe = _entrar(aplicacao, *RECEPCAO)
    resposta = equipe.post(f"/pagamentos/{identificador}/primeiro",
                           data={"csrf_token": _token(equipe, "/pagamentos")},
                           follow_redirects=True)
    assert "Pix conferido" in resposta.data.decode()
    assert _lancamentos(aplicacao, identificador) == {"sinal": Decimal("300.00")}


def test_o_formulario_nao_manda_valor(aplicacao, conta, chale):
    """Um valor forjado no envio é ignorado."""
    identificador = _pedir(aplicacao, conta, chale)
    equipe = _entrar(aplicacao, *RECEPCAO)
    equipe.post(f"/pagamentos/{identificador}/primeiro",
                data={"csrf_token": _token(equipe, "/pagamentos"),
                      "valor": "0.01", "tipo": "integral"})
    assert _lancamentos(aplicacao, identificador) == {"sinal": Decimal("300.00")}


def test_acao_desconhecida_na_url_responde_404(aplicacao, conta, chale):
    identificador = _pedir(aplicacao, conta, chale)
    equipe = _entrar(aplicacao, *RECEPCAO)
    resposta = equipe.post(f"/pagamentos/{identificador}/dobrar",
                           data={"csrf_token": _token(equipe, "/pagamentos")})
    assert resposta.status_code == 404


def test_o_botao_exige_csrf(aplicacao, conta, chale):
    identificador = _pedir(aplicacao, conta, chale)
    equipe = _entrar(aplicacao, *RECEPCAO)
    aplicacao.config["WTF_CSRF_ENABLED"] = True
    try:
        resposta = equipe.post(f"/pagamentos/{identificador}/primeiro")
    finally:
        aplicacao.config["WTF_CSRF_ENABLED"] = False
    assert resposta.status_code == 400
    assert _lancamentos(aplicacao, identificador) == {}


# ===========================================================================
# 4. O card do hóspede em linha do tempo (8b)
# ===========================================================================

def test_linha_do_tempo_do_sinal_tem_duas_etapas():
    from test_pedido_do_hospede import AGORA as AGORA7, _linha as linha7
    estado = pedido.cartao(linha7(), agora=AGORA7)
    passos = estado["linha_do_tempo"]
    assert [p["rotulo"] for p in passos] == ["Pix 50% (sinal)", "Pix 100% (restante)"]
    assert passos[0]["situacao"] == "Aguardando o seu Pix"
    assert passos[1]["situacao"] == "Liberado depois da confirmação do sinal"
    assert passos[1]["valor"] == Decimal("650.00")
    assert estado["valor_pago"] == Decimal("0.00")


def test_linha_do_tempo_com_pix_informado():
    from test_pedido_do_hospede import AGORA as AGORA7, _linha as linha7
    estado = pedido.cartao(linha7(pix_informado_em=AGORA7), agora=AGORA7)
    assert estado["linha_do_tempo"][0]["situacao"] == (
        "Aguardando confirmação do Pix 50%")
    assert "Aguardando confirmação do Pix 50%" in estado["texto"]


def test_linha_do_tempo_sinal_confirmado():
    from test_pedido_do_hospede import AGORA as AGORA7, _linha as linha7
    estado = pedido.cartao(
        linha7(situacao="confirmada", lancamentos={"sinal": Decimal("650.00")}),
        agora=AGORA7)
    passos = estado["linha_do_tempo"]
    assert passos[0]["situacao"] == "Pix 50% confirmado" and passos[0]["feito"]
    assert passos[1]["situacao"] == "Aguardando o seu Pix"
    assert estado["botao"]["rotulo"] == "Fiz o Pix 100% — R$ 650,00"
    assert estado["valor_pago"] == Decimal("650.00")


def test_linha_do_tempo_restante_informado():
    from test_pedido_do_hospede import AGORA as AGORA7, _linha as linha7
    estado = pedido.cartao(
        linha7(situacao="confirmada", restante_informado_em=AGORA7,
               lancamentos={"sinal": Decimal("650.00")}),
        agora=AGORA7)
    assert estado["etapa"] == "restante_em_conferencia"
    assert estado["linha_do_tempo"][1]["situacao"] == (
        "Aguardando confirmação do Pix 100%")
    assert estado["valor_pago"] == Decimal("650.00"), (
        "a declaração do hóspede não é pagamento")


def test_linha_do_tempo_concluida():
    from test_pedido_do_hospede import AGORA as AGORA7, _linha as linha7
    estado = pedido.cartao(
        linha7(situacao="confirmada", restante_informado_em=AGORA7,
               lancamentos={"sinal": Decimal("650.00"),
                            "restante": Decimal("650.00")}),
        agora=AGORA7)
    assert estado["etapa"] == "pagamento_concluido"
    assert estado["texto"] == (
        "Pagamento concluído. Obrigado por escolher a VillaJu — vamos "
        "preparar tudo para a sua chegada.")
    assert all(p["feito"] for p in estado["linha_do_tempo"])
    assert estado["valor_pago"] == Decimal("1300.00")
    assert estado["botao"] is None


def test_linha_do_tempo_em_cima_da_hora_tem_uma_etapa():
    from test_pedido_do_hospede import AGORA as AGORA7, _linha as linha7
    estado = pedido.cartao(linha7(modalidade="integral", prazo_restante=None),
                           agora=AGORA7)
    assert [p["rotulo"] for p in estado["linha_do_tempo"]] == ["Pix 100%"]


def test_o_card_do_hospede_pela_tela(aplicacao, hospede, conta, chale,
                                     recepcao):
    identificador = _pedir(aplicacao, conta, chale)
    _agir(aplicacao, pagamentos.PRIMEIRO, identificador, recepcao)

    html = hospede.get("/reservas/minhas").data.decode()
    card = html[html.index(f'id="reserva-{identificador}"'):]
    card = card[:card.index("</article>")]
    assert "Pix 50% confirmado" in card
    assert "Valor pago: <strong>R$ 300,00</strong>" in card
    assert "Fiz o Pix 100% — R$ 300,00" in card


def test_o_hospede_nao_ve_lancamentos_alheios(aplicacao, conta, outra, chale,
                                             recepcao):
    identificador = _pedir(aplicacao, conta, chale)
    _agir(aplicacao, pagamentos.PRIMEIRO, identificador, recepcao)
    with aplicacao.app_context():
        assert pagamento_repo.lancamentos_por_reserva_do_hospede(
            outra["hospede_id"]) == {}


# ===========================================================================
# 5. O controle do administrador (8c)
# ===========================================================================

def _lancar_tudo(aplicacao, conta, chale, recepcao):
    identificador = _pedir(aplicacao, conta, chale)
    _agir(aplicacao, pagamentos.PRIMEIRO, identificador, recepcao)
    _agir(aplicacao, pagamentos.RESTANTE, identificador, recepcao)
    return identificador


def test_o_controle_soma_o_mes_e_o_ano(aplicacao, conta, chale, recepcao):
    _lancar_tudo(aplicacao, conta, chale, recepcao)
    hoje = dt.date.today()
    with aplicacao.app_context():
        dados = pagamentos.controle(hoje.year, hoje.month,
                                    usuario_id=recepcao["id"])
    mes = dados["meses"][hoje.month - 1]
    assert mes["total"] == Decimal("600.00") and mes["lancamentos"] == 2
    assert dados["total_do_ano"] == Decimal("600.00")
    assert [l["tipo"] for l in dados["lancamentos"]] == ["sinal", "restante"]
    assert dados["lancamentos"][0]["conferido_por"] == recepcao["nome"]
    assert dados["lancamentos"][0]["hospede_nome"] == DADOS["nome"]


def test_a_devolucao_desconta_do_mes(aplicacao, conta, chale, recepcao,
                                     conexao_banco):
    identificador = _lancar_tudo(aplicacao, conta, chale, recepcao)
    cursor = conexao_banco.cursor()
    cursor.execute(
        "INSERT INTO pagamento (reserva_id, tipo, valor, registrado_por) "
        "VALUES (%s, 'devolucao', -600.00, %s)", (identificador, recepcao["id"]))
    cursor.close()
    hoje = dt.date.today()
    with aplicacao.app_context():
        dados = pagamentos.controle(hoje.year, None, usuario_id=recepcao["id"])
    assert dados["total_do_ano"] == Decimal("0.00")


def test_o_ano_e_o_mes_invalidos_caem_no_padrao(aplicacao):
    hoje = dt.date.today()
    with aplicacao.app_context():
        dados = pagamentos.controle("abc", "13")
    assert dados["ano"] == hoje.year and dados["mes"] is None
    with aplicacao.app_context():
        assert pagamentos.controle("1999", "0")["ano"] == hoje.year


def test_o_controle_nao_compara_com_o_ano_anterior(aplicacao):
    with aplicacao.app_context():
        dados = pagamentos.controle()
    assert set(dados) == {"ano", "anos", "mes", "mes_nome", "meses",
                          "total_do_ano", "lancamentos"}


def test_o_controle_e_so_do_administrador(aplicacao, hospede):
    assert _entrar(aplicacao, *ADMIN).get("/pagamentos/controle").status_code == 200
    assert _entrar(aplicacao, *RECEPCAO).get(
        "/pagamentos/controle").status_code == 403
    assert hospede.get("/pagamentos/controle").status_code == 403


def test_o_controle_pela_tela(aplicacao, conta, chale, recepcao):
    _lancar_tudo(aplicacao, conta, chale, recepcao)
    hoje = dt.date.today()
    admin = _entrar(aplicacao, *ADMIN)
    html = admin.get(f"/pagamentos/controle?ano={hoje.year}&mes={hoje.month}"
                     ).data.decode()
    assert "R$ 600,00" in html
    assert DADOS["nome"] in html
    assert html.count('class="mes-nome"') == 12


def test_a_recepcao_nao_tem_link_para_o_controle(aplicacao):
    html = _entrar(aplicacao, *RECEPCAO).get("/pagamentos").data.decode()
    assert "/pagamentos/controle" not in html
    html = _entrar(aplicacao, *ADMIN).get("/pagamentos").data.decode()
    assert "/pagamentos/controle" in html
