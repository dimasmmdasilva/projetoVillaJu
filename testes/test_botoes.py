"""Os botões — Etapa 5, Bloco 17c (21/11, pedidos do Dimas).

O que estes testes guardam:

1. **Três tipos de botão, cada um com o seu efeito** de mouse e de clique,
   só em CSS: principal (escuro), secundário (contornado) e destrutivo
   (`.perigo`). O destrutivo está nos botões que desfazem algo.
2. **O teclado tem contorno próprio** (`:focus-visible`), e quem pede menos
   movimento não recebe transição nem deslocamento.
3. **Os cards ganharam o terceiro botão**: "Ver reserva" no Painel da
   equipe, em tamanho menor; "Conversa com a pousada" no card do hóspede,
   inclusive no card sem nada a pagar.
4. **O campo de resposta alinha com a caixa da mensagem.**

O CSS é lido pelo VALOR FINAL de cada propriedade, como em
`test_pagina_do_chale`. O bloco de movimento reduzido é lido à parte: sem
isso, o "transition: none" dele pareceria a regra de todo mundo.
"""

import pathlib
import re

import pytest

from test_pagina_do_chale import _valor_final
from test_pedido_do_hospede import (  # noqa: F401
    DADOS,
    SENHA,
    _confirmar,
    _entrar,
    _pedir,
    aplicacao,
    chale,
    conta,
)

pytestmark = pytest.mark.banco

CSS = (pathlib.Path(__file__).resolve().parents[1]
       / "app/static/css/villaju.css").read_text(encoding="utf-8")

_MOVIMENTO = re.compile(
    r"@media\s*\(prefers-reduced-motion:\s*reduce\)\s*\{((?:[^{}]*\{[^{}]*\})*[^{}]*)\}"
)
REDUZIDO = "\n".join(_MOVIMENTO.findall(CSS))
NORMAL = _MOVIMENTO.sub("", CSS)

PRINCIPAL = "button:not(.secundario):not(.sair)"
PRINCIPAL_LINK = "a.botao:not(.botao--secundario)"


# ===========================================================================
# 1. Um efeito por tipo
# ===========================================================================

@pytest.mark.parametrize("seletor", [PRINCIPAL, PRINCIPAL_LINK])
def test_o_principal_sobe_com_o_mouse_e_afunda_no_clique(seletor):
    assert _valor_final(NORMAL, seletor + ":hover", "transform") == "translateY(-1px)"
    assert _valor_final(NORMAL, seletor + ":hover", "box-shadow") not in (None, "none")
    assert _valor_final(NORMAL, seletor + ":active", "transform") == "translateY(0)"
    assert _valor_final(NORMAL, seletor + ":active", "box-shadow") == "none"


@pytest.mark.parametrize("seletor", ["button.secundario", "a.botao.botao--secundario"])
def test_o_secundario_ganha_fundo_e_escurece_a_borda(seletor):
    fundo = _valor_final(NORMAL, seletor + ":hover", "background")
    assert fundo and fundo != "#fff"
    assert _valor_final(NORMAL, seletor + ":active", "border-color")


def test_o_destrutivo_fica_vermelho():
    assert _valor_final(NORMAL, "button.perigo", "color") == "#8a2b22"
    assert _valor_final(NORMAL, "button.perigo:hover", "background") == "#fdecea"
    assert _valor_final(NORMAL, "button.perigo:active", "color") == "#6d1f18"


def test_os_tres_efeitos_sao_diferentes():
    """Um efeito por tipo, e não o mesmo com três nomes."""
    fundos = {
        _valor_final(NORMAL, PRINCIPAL + ":hover", "background"),
        _valor_final(NORMAL, "button.secundario:hover", "background"),
        _valor_final(NORMAL, "button.perigo:hover", "background"),
    }
    assert len(fundos) == 3 and None not in fundos


def test_o_destrutivo_vem_depois_do_secundario():
    """Os botões destrutivos levam as duas classes; com a mesma força de
    seletor, vale o que vem depois na folha."""
    assert NORMAL.index("button.perigo:hover") > NORMAL.index("button.secundario:hover")


# ===========================================================================
# 2. Teclado e movimento reduzido
# ===========================================================================

@pytest.mark.parametrize("seletor", ["button:focus-visible", "a.botao:focus-visible"])
def test_o_teclado_ve_o_contorno(seletor):
    assert _valor_final(NORMAL, seletor, "outline").startswith("2px solid")


def test_quem_pede_menos_movimento_nao_ve_animacao():
    assert _valor_final(NORMAL, "button", "transition")
    assert _valor_final(REDUZIDO, "button", "transition") == "none"
    assert _valor_final(REDUZIDO, PRINCIPAL + ":hover", "transform") == "none"


# ===========================================================================
# 3. O terceiro botão dos cards, e os botões destrutivos
# ===========================================================================

def test_ver_reserva_e_botao_contornado_e_menor_no_painel(aplicacao, conta, chale):
    _pedir(aplicacao, conta, chale)
    painel = _entrar(aplicacao, "recepcao", "Villa@Recep2026").get("/painel").data.decode()
    assert re.search(r'<a class="botao botao--secundario" href="/reservas/\d+">Ver reserva</a>',
                     painel)
    assert _valor_final(NORMAL, ".cartao-painel__acoes a.botao", "font-size") == "0.875rem"
    assert _valor_final(NORMAL, ".cartao-painel__acoes a.botao", "padding") == "0.35rem 0.75rem"


def _acoes_do_card(html):
    inicio = html.index('class="cartao-reserva__acoes"')
    return html[inicio:html.index("</div>", inicio)]


def test_a_conversa_e_o_terceiro_botao_do_card(aplicacao, conta, chale):
    _pedir(aplicacao, conta, chale)
    acoes = _acoes_do_card(
        _entrar(aplicacao, DADOS["email"], SENHA).get("/reservas/minhas").data.decode())
    assert re.search(r'class="botao botao--secundario"\s+href="/reservas/\d+/conversa"', acoes)
    assert acoes.index("Fiz o Pix") < acoes.index("Desistir do pedido") \
        < acoes.index("Conversa com a pousada")


def test_o_card_sem_pagamento_a_fazer_ainda_tem_a_conversa(aplicacao, conta, chale):
    """Confirmada e integral: sem "Fiz o Pix", e a conversa continua."""
    identificador = _pedir(aplicacao, conta, chale, entrada=3)
    _confirmar(aplicacao, identificador)
    acoes = _acoes_do_card(
        _entrar(aplicacao, DADOS["email"], SENHA).get("/reservas/minhas").data.decode())
    assert "Fiz o Pix" not in acoes
    assert "Conversa com a pousada" in acoes


def test_cancelar_e_desistir_sao_destrutivos(aplicacao, conta, chale):
    _pedir(aplicacao, conta, chale)
    html = _entrar(aplicacao, DADOS["email"], SENHA).get("/reservas/minhas").data.decode()
    assert 'class="secundario perigo">Desistir do pedido' in html


@pytest.mark.parametrize("arquivo,rotulo", [
    ("painel/minhas_reservas.html", "Cancelar reserva"),
    ("painel/pagamentos.html", "Cancelar reserva por falta de pagamento"),
    ("painel/quem_vai.html", "Remover da lista"),
    ("painel/chale_form.html", "Excluir"),
    ("painel/hospede_form.html", "Excluir hóspede"),
])
def test_os_botoes_que_desfazem_sao_destrutivos(arquivo, rotulo):
    texto = (pathlib.Path(__file__).resolve().parents[1] / "app/templates"
             / arquivo).read_text(encoding="utf-8")
    assert re.search(r'class="secundario perigo">' + re.escape(rotulo), texto)


def test_os_dois_cancelamentos_da_equipe_sao_destrutivos():
    """Bloco 17e: "Marcar como cancelada" virou dois botões, um por motivo."""
    texto = (pathlib.Path(__file__).resolve().parents[1]
             / "app/templates/painel/reserva_detalhe.html").read_text(encoding="utf-8")
    assert '<button type="submit" class="secundario perigo">Cancelar {{ rotulo }}</button>'         in texto


# ===========================================================================
# 4. O campo de resposta alinha com a mensagem
# ===========================================================================

def test_a_caixa_da_mensagem_e_o_campo_medem_o_mesmo():
    assert _valor_final(NORMAL, ".mural-mensagem", "box-sizing") == "border-box"
    assert _valor_final(NORMAL, ".formulario--conversa textarea", "box-sizing") == "border-box"
    assert _valor_final(NORMAL, ".mural-mensagem", "max-width") == \
        _valor_final(NORMAL, ".formulario--conversa", "max-width")


# ===========================================================================
# 5. Os links soltos viraram botões (17c, segundo pedido do Dimas)
# ===========================================================================

TEMPLATES = pathlib.Path(__file__).resolve().parents[1] / "app/templates"

# As exceções da regra, cada uma com o motivo: links no meio de uma frase,
# nos menus, nas linhas de tabela, na paginação, na grade dos meses e na
# lista de chalés livres continuam links.
EXCECOES = {
    ("base.html", None): "menus das barras",
    ("publico/base.html", None): "menu público",
    ("painel/chales.html", "chales.editar"): "Editar em cada linha da tabela",
    ("painel/chale_form.html", "chales.editar_tarifa"): "Editar em cada linha",
    ("painel/hospedes.html", "hospedes.editar"): "Editar em cada linha",
    ("painel/disponibilidade.html", "reservas.nova"): "Reservar em cada linha",
    ("painel/controle_pagamentos.html", "pagamentos.controle"): "grade dos meses",
    ("painel/inicio.html", "painel.inicio"): "paginação",
    ("painel/quem_vai.html", "hospedes.listar"): "no meio de uma frase",
    ("painel/reserva_detalhe.html", "pagamentos.cards"): "no meio de uma frase",
    ("painel/reserva_form.html", "publico.regulamento"): "dentro da nota",
    ("painel/_respostas_novas.html", "conversa.do_hospede"): "dentro do aviso",
    ("publico/chale.html", "publico.inicio') }}\">{{ hospedaria.nome"): "trilha",
    ("publico/disponibilidade.html", "publico.disponibilidade"): "lista de livres",
    ("publico/disponibilidade.html", "publico.fale_com_a_pousada"): "frase",
}


def _excecao(relativo, linha):
    for (arquivo, trecho), _motivo in EXCECOES.items():
        if arquivo == relativo and (trecho is None or trecho in linha):
            return True
    return False


def test_nenhum_link_solto_fora_das_excecoes():
    soltos = []
    for arquivo in TEMPLATES.rglob("*.html"):
        relativo = arquivo.relative_to(TEMPLATES).as_posix()
        for numero, linha in enumerate(
                arquivo.read_text(encoding="utf-8").splitlines(), 1):
            if re.match(r"\s*(<p[^>]*>\s*)?<a href=", linha) \
                    and not _excecao(relativo, linha):
                soltos.append(f"{relativo}:{numero}: {linha.strip()[:70]}")
    assert not soltos, "link solto que devia ser botão:\n" + "\n".join(soltos)


def test_os_tres_exemplos_do_dimas_sao_botoes(aplicacao, conta, chale):
    visitante = aplicacao.test_client()
    vitrine = visitante.get("/").data.decode()
    assert re.search(r'<a class="botao botao--secundario" href="/chale/[^"]+">\s*'
                     r'Ver fotos e detalhes do', vitrine)
    pagina = visitante.get("/chale/asturias").data.decode()
    assert '<a class="botao botao--secundario" href="/">Ver todos os chalés</a>' in pagina
    minhas = _entrar(aplicacao, DADOS["email"], SENHA).get("/reservas/minhas").data.decode()
    assert re.search(r'<a class="botao botao--secundario" href="/[^"]*">Meu perfil</a>',
                     minhas)


def test_o_grupo_de_botoes_fica_lado_a_lado():
    assert _valor_final(NORMAL, ".acoes-de-navegacao", "display") == "flex"
    assert _valor_final(NORMAL, ".acoes-de-navegacao", "flex-wrap") == "wrap"


def test_o_login_tem_os_tres_caminhos_numa_linha(aplicacao):
    """21/11: sem a frase entre eles, da largura do formulário."""
    html = aplicacao.test_client().get("/login").data.decode()
    inicio = html.index('class="voltar acoes-de-navegacao acoes-de-entrada"')
    linha = html[inicio:html.index("</p>", inicio)]
    assert re.findall(r">([^<]+)</a>", linha) == ["Esqueci a senha", "Criar conta", "Voltar"]
    assert "Não tem conta" not in html
    assert _valor_final(NORMAL, ".acoes-de-entrada", "flex-wrap") == "nowrap"
    assert _valor_final(NORMAL, ".acoes-de-entrada a.botao", "flex") == "1 1 auto"


def test_os_botoes_de_navegacao_sao_do_tamanho_menor():
    """O pedido foi sobre o cadastro; vale para todos os de navegação."""
    for propriedade in ("padding", "font-size"):
        assert _valor_final(NORMAL, ".acoes-de-navegacao a.botao", propriedade) == \
            _valor_final(NORMAL, ".cartao-painel__acoes a.botao", propriedade)


# ===========================================================================
# 6. Bloco 17d: os avisos do login e o "Marcar como cancelada"
# ===========================================================================

def test_o_aviso_do_login_vem_depois_dos_tres_caminhos(aplicacao):
    """Antes o aviso ficava entre o Entrar e os botões, e os empurrava para
    baixo quando aparecia. Conferido com um aviso de verdade na tela."""
    cliente = aplicacao.test_client()
    with cliente.session_transaction() as sessao:
        sessao["_flashes"] = [("sucesso", "Sessão encerrada.")]
    html = cliente.get("/login").data.decode()
    assert "Sessão encerrada." in html
    assert html.index("acoes-de-entrada") < html.index('class="avisos"') \
        < html.index("Sessão encerrada.")


def test_marcar_como_cancelada_tem_o_tamanho_dos_botoes_da_pagina(aplicacao, conta,
                                                                  chale):
    identificador = _pedir(aplicacao, conta, chale)
    pagina = _entrar(aplicacao, "admin", "Villa@Admin2026").get(
        f"/reservas/{identificador}").data.decode()
    inicio = pagina.index("Cancelar pela pousada")
    formulario = pagina[pagina.rindex("<form", 0, inicio):inicio]
    assert 'class="acao-da-situacao"' in formulario, "não é o .acao miúdo"
    # Sem regra de tamanho própria: vale o do `button`, o mesmo do
    # "Enviar resposta"; e sem o recuo à esquerda do `.acao`.
    assert _valor_final(NORMAL, ".acao-da-situacao button", "padding") is None
    assert _valor_final(NORMAL, ".acao-da-situacao button", "font-size") is None
    assert _valor_final(NORMAL, ".acao-da-situacao button", "margin") == "0"


def test_o_aviso_do_login_tem_a_largura_do_entrar():
    """21/11: no cartão do login, o aviso ocupa a largura do formulário;
    nas outras telas continua do tamanho do texto."""
    assert _valor_final(NORMAL, ".cartao-entrada .avisos .aviso", "width") == "100%"
    assert _valor_final(NORMAL, ".aviso", "width") == "fit-content"
    assert _valor_final(NORMAL, ".aviso", "box-sizing") == "border-box"
