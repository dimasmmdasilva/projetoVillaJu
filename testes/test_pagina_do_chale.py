"""Página de cada chalé, e os ajustes de interface da face pública.

Etapa 5, Blocos 10 e 13.

**A galeria funciona por navegação, sem JavaScript.** Cada miniatura é um
link para a mesma página com `?foto=N`, e o servidor desenha aquela como a
principal. A escolha é de 13/11, e as razões estão no cabeçalho de
`publico/chale.html` — em resumo: cada foto ganha endereço próprio, e a
alternativa em CSS puro baixaria as cinco de uma vez e as anunciaria ao
leitor de tela como botões de rádio.

**Conteúdo provisório declarado.** As fotografias da parceira não
chegaram, e cada lugar é um bloco de cor. Os testes verificam a estrutura
e os links, que são o que a troca das imagens não vai alterar.
"""

import re

import pytest

from app import create_app
from app.config import Configuracao
from app.dados import chale_repo, conexao
from app.rotas.publico import (
    FOTOS_POR_CHALE,
    TOTAL_DE_AMOSTRAS,
    apelido_do_chale,
    cor_da_foto,
)

ADMIN = ("admin", "Villa@Admin2026")


@pytest.fixture
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

    configuracao = Configuracao(modo_teste=True)
    conexao.iniciar_pool(configuracao)
    app = create_app(configuracao)
    app.config["SESSION_COOKIE_SECURE"] = False
    app.villaju = configuracao
    yield app
    conexao.encerrar_pool()


@pytest.fixture
def visitante(aplicacao):
    return aplicacao.test_client()


def _apelidos(aplicacao):
    with aplicacao.app_context():
        return [
            apelido_do_chale(chale["nome"])
            for chale in chale_repo.listar(apenas_ativos=True)
        ]


# ---------------------------------------------------------------------------
# O apelido, que é o endereço
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "nome,esperado",
    [
        ("Dunas", "dunas"),
        ("Embaré", "embare"),
        ("Boracéia", "boraceia"),
        ("Asturias", "asturias"),
        ("Camburi", "camburi"),
        ("Chalé do Mar", "chale-do-mar"),
        ("  Espaço  Aberto  ", "espaco-aberto"),
    ],
)
def test_o_apelido_remove_acento_e_espaco(nome, esperado):
    """`Boracéia` vira `boraceia`, e não `borac%C3%A9ia`.

    Um endereço com acento é percentualmente codificado pelo navegador, o
    que destrói justamente o que o endereço legível deveria entregar:
    alguém colar o link no WhatsApp e a outra pessoa ver de que chalé se
    trata antes de abrir.
    """
    assert apelido_do_chale(nome) == esperado


def test_o_apelido_nao_usa_a_normalizacao_da_criptografia():
    """`cripto.normalizar` não serve aqui, e o contraste explica por quê.

    Ele usa NFKC, que **mantém** o acento, porque existe para comparar
    valores — e dois valores que diferem só por acento precisam diferir.
    Formar endereço é outro problema.

    Sem este teste, alguém reaproveitaria a função que o plano menciona e
    produziria `/chale/boracéia`, que funciona e fica ilegível quando
    copiado.
    """
    from app.seguranca import cripto

    assert cripto.normalizar("Boracéia") == "boracéia"
    assert apelido_do_chale("Boracéia") == "boraceia"


def test_apelidos_dos_cinco_chales_sao_distintos(aplicacao):
    """O contraste que dá sentido aos testes de endereço.

    Se dois chalés caíssem no mesmo apelido, um deles seria inalcançável
    — e a busca devolveria o primeiro conforme a ordem do banco.
    """
    apelidos = _apelidos(aplicacao)

    assert len(apelidos) == 5
    assert len(set(apelidos)) == 5, f"apelidos repetidos: {apelidos}"


# ---------------------------------------------------------------------------
# A página
# ---------------------------------------------------------------------------

def test_cada_chale_tem_pagina_propria(aplicacao, visitante):
    for apelido in _apelidos(aplicacao):
        resposta = visitante.get(f"/chale/{apelido}")
        assert resposta.status_code == 200, apelido


def test_a_pagina_e_publica_e_nao_cria_sessao(aplicacao, visitante):
    apelido = _apelidos(aplicacao)[0]
    visitante.get(f"/chale/{apelido}")

    with visitante.session_transaction() as sessao:
        assert "usuario_id" not in dict(sessao)


def test_o_apelido_aceita_maiuscula(aplicacao, visitante):
    """Quem digita o endereço à mão não erra por causa de caixa.

    Endereço é comparado em minúsculas; `/chale/DUNAS` chega ao mesmo
    lugar que `/chale/dunas`.
    """
    assert visitante.get("/chale/DUNAS").status_code == 200


def test_apelido_inexistente_responde_404(visitante):
    assert visitante.get("/chale/nao-existe").status_code == 404


def test_chale_desativado_nao_tem_pagina(aplicacao, visitante):
    """Pela mesma razão que não aparece na vitrine.

    A desativação é como a hospedaria tira um chalé de operação.
    Informar que ele existe mas está desligado é dado de operação
    interna — e permitiria contar quantos chalés a hospedaria tem,
    somando os que respondem de um jeito e de outro.
    """
    with aplicacao.app_context():
        nome = chale_repo.buscar_por_id(5)["nome"]
        chale_repo.definir_ativo(5, False)

    apelido = apelido_do_chale(nome)

    desativado = visitante.get(f"/chale/{apelido}")
    inexistente = visitante.get("/chale/nao-existe")

    assert desativado.status_code == inexistente.status_code == 404
    assert desativado.data == inexistente.data, (
        "chalé desativado precisa responder exatamente como inexistente"
    )

    assert nome not in visitante.get("/").data.decode()


def test_a_pagina_do_chale_nao_mostra_valor(aplicacao, visitante):
    """A decisão de não publicar preço vale aqui também.

    É o lugar mais tentador de todos: quem abriu a página de um chalé
    está perto de pedir, e quer saber quanto custa. A resposta continua
    sendo depois do cadastro.
    """
    for apelido in _apelidos(aplicacao):
        html = visitante.get(f"/chale/{apelido}").data.decode()
        assert "R$" not in html, apelido
        assert "diária" not in html.lower(), apelido


def test_a_pagina_traz_as_informacoes_e_a_chamada(aplicacao, visitante):
    html = visitante.get("/chale/dunas").data.decode()

    assert "Dunas" in html
    assert "Capacidade" in html
    assert "Camas" in html
    assert "Solicitar reserva" in html
    assert "/disponibilidade" in html


# ---------------------------------------------------------------------------
# A galeria
# ---------------------------------------------------------------------------

def test_a_galeria_tem_uma_miniatura_por_foto(visitante):
    html = visitante.get("/chale/dunas").data.decode()

    miniaturas = re.findall(r'class="miniatura[ "]', html)
    assert len(miniaturas) == FOTOS_POR_CHALE


def test_cada_miniatura_e_um_link_com_endereco_proprio(visitante):
    """A forma escolhida da galeria, verificada pelo que ela entrega.

    Se a troca fosse por CSS ou por JavaScript, não haveria `href`
    nenhum — e a foto não teria endereço para compartilhar nem para o
    buscador indexar.
    """
    html = visitante.get("/chale/dunas").data.decode()

    enderecos = set(re.findall(r'href="/chale/dunas\?foto=(\d+)"', html))

    # Todas menos a que já está em exibição.
    assert enderecos == {
        str(n) for n in range(1, FOTOS_POR_CHALE + 1) if n != 1
    }


def test_a_foto_em_exibicao_nao_e_link(visitante):
    """Clicar nela não levaria a lugar nenhum.

    Um link que não leva a lugar nenhum confunde quem navega por teclado,
    que percorre todos eles em ordem. `aria-current` diz ao leitor de
    tela qual é a atual.
    """
    html = visitante.get("/chale/dunas?foto=3").data.decode()

    assert 'href="/chale/dunas?foto=3"' not in html
    assert 'aria-current="true"' in html


def test_a_foto_pedida_e_a_que_aparece_grande(visitante):
    """O que a galeria promete, verificado foto a foto.

    As cinco precisam dar amostras diferentes: se todas dessem a mesma,
    clicar numa miniatura não mudaria nada na tela — e o teste anterior,
    que só confere os links, passaria mesmo assim.
    """
    amostras = []
    for n in range(1, FOTOS_POR_CHALE + 1):
        html = visitante.get(f"/chale/dunas?foto={n}").data.decode()
        achado = re.search(r"galeria-grande amostra--(\d+)", html)
        assert achado, f"a foto {n} não desenhou a imagem grande"
        amostras.append(achado.group(1))

    assert len(set(amostras)) == FOTOS_POR_CHALE, (
        f"as fotos do mesmo chalé repetem amostra: {amostras}"
    )


def test_chales_diferentes_comecam_com_amostras_diferentes(aplicacao, visitante):
    """Senão as páginas pareceriam iguais ao olhar rápido.

    Importa justamente para quem está conferindo se os links funcionam:
    abrir cinco páginas idênticas não diz se o endereço levou ao chalé
    certo.
    """
    primeiras = []
    for apelido in _apelidos(aplicacao):
        html = visitante.get(f"/chale/{apelido}").data.decode()
        achado = re.search(r"galeria-grande amostra--(\d+)", html)
        primeiras.append(achado.group(1) if achado else None)

    assert len(set(primeiras)) == len(primeiras), (
        f"dois chalés abrem com a mesma amostra: {primeiras}"
    )


@pytest.mark.parametrize("valor", ["99", "0", "-3", "abc", ""])
def test_foto_fora_da_faixa_volta_a_primeira(visitante, valor):
    """Sem página de erro.

    Um `?foto=99` vindo de um link velho ou de alguém mexendo no endereço
    não é motivo para erro — é motivo para mostrar o chalé.
    """
    resposta = visitante.get(f"/chale/dunas?foto={valor}")

    assert resposta.status_code == 200

    html = resposta.data.decode()
    assert "foto 1 de" in html, (
        f"?foto={valor!r} deveria cair na primeira foto"
    )


def test_a_cor_da_foto_cabe_na_folha_de_estilo():
    """A classe gerada precisa existir no CSS.

    Uma classe `amostra--13` produziria um bloco **sem cor de fundo** —
    invisível na tela, e sem erro em lugar nenhum. É o mesmo gênero de
    defeito silencioso que a CSP causou na Etapa 4, quando os blocos
    apareciam vazios.
    """
    import pathlib

    css = (
        pathlib.Path(__file__).resolve().parents[1]
        / "app" / "static" / "css" / "villaju.css"
    ).read_text(encoding="utf-8")

    definidas = {
        int(n) for n in re.findall(r"\.amostra--(\d+)\s*\{", css)
    }
    assert definidas == set(range(1, TOTAL_DE_AMOSTRAS + 1)), (
        f"a folha de estilo define {sorted(definidas)}, e o código gera "
        f"de 1 a {TOTAL_DE_AMOSTRAS}"
    )

    # E nenhuma combinação de chalé e posição sai da faixa.
    for chale_id in range(1, 30):
        for posicao in range(1, FOTOS_POR_CHALE + 1):
            assert cor_da_foto(chale_id, posicao) in definidas


def test_a_galeria_nao_usa_javascript():
    """A decisão do projeto, verificada no molde.

    A galeria é o lugar onde a tentação de usar JavaScript é maior — é
    para isso que ele costuma servir. O molde precisa continuar sem
    nenhum.
    """
    import pathlib

    html = (
        pathlib.Path(__file__).resolve().parents[1]
        / "app" / "templates" / "publico" / "chale.html"
    ).read_text(encoding="utf-8")

    assert "<script" not in html.lower()
    assert "onclick" not in html.lower()
    assert "javascript:" not in html.lower()


# ---------------------------------------------------------------------------
# Os ajustes de interface — Bloco 13
# ---------------------------------------------------------------------------

def test_o_conteudo_e_o_cabecalho_ficam_na_mesma_coluna(visitante):
    """O item 1 dos ajustes.

    Sem a coluna no cabeçalho, os links encostavam na borda da janela
    enquanto o texto ficava centralizado, e as duas coisas pareciam de
    páginas diferentes.
    """
    html = visitante.get("/").data.decode()

    cabecalho = html.split("</header>")[0]
    assert 'class="coluna-central"' in cabecalho, (
        "o cabeçalho precisa alinhar com o conteúdo"
    )

    assert '<main class="coluna-central">' in html


def test_a_coluna_e_abandonada_em_tela_estreita():
    """Sessenta por cento de um telefone seria estreito demais para ler.

    Abaixo de 60rem a proporção é trocada por uma calha fixa. A medida de
    60% com 20% de folga é para tela de computador; num telefone, 20% de
    margem de cada lado espremeria uma coluna que já é estreita.

    A porcentagem em si é verificada em
    `test_a_coluna_ocupa_sessenta_por_cento`.
    """
    css = _css()

    assert "@media (max-width: 60rem)" in css

    bloco = css.split("@media (max-width: 60rem) {")[1].split("}")[0]
    assert "width: auto" in bloco


def test_o_menu_nao_repete_a_consulta_de_disponibilidade(visitante):
    """O item 2.

    A chamada já está em botão no corpo da página inicial. Repetir a
    mesma ação duas vezes na mesma tela divide a atenção sem oferecer
    nada.
    """
    html = visitante.get("/").data.decode()
    cabecalho = html.split("</header>")[0]

    assert "Consultar disponibilidade" not in cabecalho

    # E continua no corpo, que é onde ela ficou.
    corpo = html.split("</header>", 1)[-1]
    assert re.search(r'href="/disponibilidade"', corpo)


def test_localizacao_e_o_ultimo_item_do_menu(visitante):
    """O item 6.

    "Localização" é âncora para o rodapé desta mesma página, não destino.
    Vinha antes de links que levam a outras telas, o que invertia a
    importância.
    """
    html = visitante.get("/").data.decode()
    cabecalho = html.split("</header>")[0]
    nav = cabecalho.split("<nav>")[1].split("</nav>")[0]

    rotulos = [
        texto.strip()
        for texto in re.findall(r"<a[^>]*>([^<]+)</a>", nav)
        if texto.strip()
    ]

    assert rotulos, "o menu ficou vazio"
    assert rotulos[-1] == "Localização", (
        f"Localização precisa ser o último do menu: {rotulos}"
    )


def test_a_massa_nao_traz_nome_de_pessoa_na_conta_de_administrador():
    """O item 7, e ele é mais do que cosmético.

    A conta de administrador da massa tinha o nome da anfitriã da
    hospedaria — uma pessoa real — num arquivo **versionado**, e esse
    nome aparecia na barra de navegação a cada tela.

    Massa fictícia com nome de pessoa real é dado pessoal em repositório
    público, e o projeto inteiro se apoia em não guardar o que não
    precisa.
    """
    import pathlib

    sql = (
        pathlib.Path(__file__).resolve().parents[1]
        / "banco" / "04_dados_ficticios.sql"
    ).read_text(encoding="utf-8")

    assert "'Vania" not in sql, (
        "o nome da anfitriã voltou à massa versionada"
    )
    assert "('admin', 'Administrador'," in sql


def test_a_barra_mostra_administrador_e_nao_nome_de_pessoa(aplicacao):
    cliente = aplicacao.test_client()
    pagina = cliente.get("/login").data.decode()
    dados = {"login": ADMIN[0], "senha": ADMIN[1]}
    achado = re.search(r'name="csrf_token" value="([^"]+)"', pagina)
    if achado:
        dados["csrf_token"] = achado.group(1)
    cliente.post("/login", data=dados)

    painel = cliente.get("/painel/").data.decode()

    assert "Administrador" in painel
    assert "Vania" not in painel


# ---------------------------------------------------------------------------
# Segunda rodada de ajustes — 13/11, depois de ver as telas
# ---------------------------------------------------------------------------

def _css():
    import pathlib

    return (
        pathlib.Path(__file__).resolve().parents[1]
        / "app" / "static" / "css" / "villaju.css"
    ).read_text(encoding="utf-8")


def test_a_coluna_ocupa_sessenta_por_cento():
    """A medida pedida: 60% de largura, 20% de folga de cada lado.

    Era 70%, e a revisão na tela pediu mais ar. As margens automáticas
    repartem o que sobra em partes iguais, que é o que produz os 20%.
    """
    bloco = _css().split("\n.coluna-central {")[1].split("}")[0]

    assert "width: 60%" in bloco
    assert "margin-left: auto" in bloco
    assert "margin-right: auto" in bloco


def test_o_aviso_tem_a_largura_do_texto_e_nao_da_coluna():
    """O aviso não se estende além do que ele avisa.

    Na tela do pedido, "O chalé Embaré recebe até 4 pessoas" ocupava a
    coluna inteira, muito além do formulário de 22rem logo abaixo. Com
    `fit-content` a caixa acompanha o texto; com `max-width: 100%`, uma
    mensagem longa quebra dentro da coluna em vez de vazar dela.
    """
    bloco = _css().split("\n.aviso {")[1].split("}")[0]

    assert "width: fit-content" in bloco
    assert "max-width: 100%" in bloco
    assert "box-sizing: border-box" in bloco, (
        "sem border-box, o padding soma à largura e a caixa passa da coluna"
    )


def test_nenhuma_regra_compete_com_a_largura_da_coluna():
    """O defeito que desalinhou o menu, prendido.

    `body.publico main` é um seletor MAIS ESPECÍFICO que `.coluna-central`.
    Enquanto ele trouxe `max-width: none`, venceu o teto da coluna: a
    `main` ficava com a porcentagem da janela e o cabeçalho travado no
    teto em rem. As duas colunas desalinhavam, e o menu começava mais de
    cem pixels à direita do conteúdo.

    Duas regras disputando a mesma medida é o erro. A largura vive num
    lugar só.
    """
    css = _css()

    for seletor in ("body.publico main {", "main.coluna-central {"):
        assert seletor in css, f"{seletor} sumiu da folha de estilo"
        bloco = css.split(seletor)[1].split("}")[0]
        assert "width" not in bloco, (
            f"{seletor} voltou a definir largura, e compete com a coluna: "
            f"{bloco.strip()}"
        )


def test_a_barra_da_area_autenticada_usa_a_mesma_coluna(aplicacao):
    """O item 4 dos ajustes.

    Antes, a marca encostava na borda esquerda da janela e a
    identificação na direita, enquanto o conteúdo ficava centralizado no
    meio: três alinhamentos diferentes na mesma tela.
    """
    cliente = aplicacao.test_client()
    pagina = cliente.get("/login").data.decode()
    dados = {"login": ADMIN[0], "senha": ADMIN[1]}
    achado = re.search(r'name="csrf_token" value="([^"]+)"', pagina)
    if achado:
        dados["csrf_token"] = achado.group(1)
    cliente.post("/login", data=dados)

    painel = cliente.get("/painel/").data.decode()

    barra = painel.split("</header>")[0]
    assert 'class="coluna-central"' in barra, (
        "a barra da área autenticada precisa alinhar com o conteúdo"
    )
    assert '<main class="coluna-central">' in painel


def test_a_main_tem_um_filho_so(aplicacao, visitante):
    """O que conserta a tela de entrada, e vale para todas.

    As mensagens e o conteúdo eram irmãos dentro da `main`. Na tela de
    entrada, em que `body.entrada main` é flex centralizado, os dois
    viravam itens LADO A LADO — e "Sessão encerrada" aparecia à esquerda
    do cartão, empurrando-o para fora do centro.

    Não era descentralização: era a mensagem ocupando uma coluna própria.
    """
    html = visitante.get("/login").data.decode()

    assert 'class="conteudo-principal"' in html


def test_a_mensagem_de_saida_fica_dentro_do_cartao(aplicacao):
    """E abaixo do formulário, como pedido."""
    cliente = aplicacao.test_client()
    pagina = cliente.get("/login").data.decode()
    dados = {"login": ADMIN[0], "senha": ADMIN[1]}
    achado = re.search(r'name="csrf_token" value="([^"]+)"', pagina)
    if achado:
        dados["csrf_token"] = achado.group(1)
    cliente.post("/login", data=dados)

    saida = cliente.post("/logout", follow_redirects=True)
    html = saida.data.decode()

    assert "encerrada" in html

    posicao_cartao = html.index('class="cartao-entrada"')
    posicao_aviso = html.index('class="avisos"')
    posicao_form = html.index("</form>")

    assert posicao_aviso > posicao_cartao, (
        "o aviso precisa estar DENTRO do cartão; fora dele, vira um "
        "segundo item do flex e empurra o cartão para o lado"
    )
    assert posicao_aviso > posicao_form, "o aviso vai abaixo do formulário"


def test_o_aviso_de_saida_some_sozinho(aplicacao):
    cliente = aplicacao.test_client()
    pagina = cliente.get("/login").data.decode()
    dados = {"login": ADMIN[0], "senha": ADMIN[1]}
    achado = re.search(r'name="csrf_token" value="([^"]+)"', pagina)
    if achado:
        dados["csrf_token"] = achado.group(1)
    cliente.post("/login", data=dados)

    html = cliente.post("/logout", follow_redirects=True).data.decode()

    assert "aviso-temporario" in html


def test_o_aviso_de_erro_de_senha_NAO_some(aplicacao):
    """A diferença deliberada, e a razão dela.

    Quem errou a senha está digitando de novo enquanto lê. Uma mensagem
    que some em três segundos desaparece justamente durante a releitura —
    e o critério 2.2.1 das WCAG trata disso: limite de tempo precisa ser
    ajustável, e mensagem de erro temporizada é o exemplo clássico.

    "Sessão encerrada" é outra coisa: confirma algo que já aconteceu e
    não precisa continuar na tela.
    """
    cliente = aplicacao.test_client()
    pagina = cliente.get("/login").data.decode()
    dados = {"login": ADMIN[0], "senha": "senha-errada-de-proposito"}
    achado = re.search(r'name="csrf_token" value="([^"]+)"', pagina)
    if achado:
        dados["csrf_token"] = achado.group(1)

    resposta = cliente.post("/login", data=dados)
    html = resposta.data.decode()

    assert resposta.status_code == 401
    assert "aviso-erro" in html

    classe = re.search(r'class="(aviso aviso-erro[^"]*)"', html)
    assert classe, "o aviso de erro mudou de forma"
    assert "aviso-temporario" not in classe.group(1), (
        "o aviso de erro não pode desaparecer sozinho: quem errou a senha "
        "está digitando de novo enquanto lê"
    )


def test_o_desaparecimento_e_por_css_e_respeita_movimento_reduzido():
    """Sem JavaScript, e sem animação para quem pediu para não ter.

    `visibility` junto da opacidade, e não só a opacidade: um elemento
    apenas transparente continua ocupando espaço e continua sendo lido
    por leitor de tela — invisível para quem vê e presente para quem não
    vê, que é o pior dos dois mundos.
    """
    import pathlib

    css = _css()

    assert "@keyframes sumir-devagar" in css
    assert "visibility: hidden" in css.split("@keyframes sumir-devagar")[1][:200]
    assert "prefers-reduced-motion" in css

    molde = (
        pathlib.Path(__file__).resolve().parents[1]
        / "app" / "templates" / "login.html"
    ).read_text(encoding="utf-8")

    assert "<script" not in molde.lower()
    assert "onclick" not in molde.lower()


# ---------------------------------------------------------------------------
# Terceira rodada — espacamento e tamanho das imagens, 13/11
# ---------------------------------------------------------------------------

def test_a_nota_so_encosta_depois_de_campo_de_formulario():
    """A margem negativa tinha alcance maior do que a razao dela.

    Ela nasceu para encostar a explicacao na caixa a que se refere, que o
    `gap` do formulario afastaria. Mas valia para toda `.nota-campo` da
    folha — e, depois de um BOTAO, grudava o texto nele.

    Apareceu em dois lugares com duas aparencias: embaixo de "Consultar
    disponibilidade", na pagina inicial, e de "Solicitar reserva", na
    pagina do chale. Mesmo defeito.
    """
    css = _css()

    padrao = css.split("\n.nota-campo {")[1].split("}")[0]
    assert "margin: -" not in padrao, (
        "a margem negativa voltou a valer para toda nota, e gruda o texto "
        "no botao que vier acima"
    )

    # E continua valendo onde foi escrita para valer.
    assert "input + .nota-campo" in css
    encostada = css.split("textarea + .nota-campo {")[1].split("}")[0]
    assert "margin-top: -" in encostada


def test_a_imagem_do_cartao_ocupa_quase_metade():
    """A fotografia e o conteudo da vitrine; o texto e o apoio.

    Eram `13rem` de largura fixa, cerca de 18% do cartao — uma miniatura
    ao lado de um bloco de texto, numa pagina cujo proposito e divulgar
    os chales.
    """
    css = _css()
    bloco = css.split("\n.foto-provisoria {")[1].split("}")[0]

    achado = re.search(r"flex:\s*0\s+0\s+(\d+)%", bloco)
    assert achado, f"a largura da imagem deixou de ser proporcional: {bloco}"
    assert 40 <= int(achado.group(1)) <= 50

    assert "aspect-ratio" in bloco, (
        "sem proporcao, a altura fixa achata a fotografia definitiva"
    )
    assert "height: auto" in bloco, "a altura fixa precisa ser desfeita"


def test_a_imagem_da_galeria_nao_fica_achatada():
    """A caixa precisa ter a forma da imagem que vai ocupa-la.

    Eram `22rem` de altura fixa. Com a coluna em 60% de uma tela larga, a
    caixa ficava com mais de tres vezes mais largura do que altura — uma
    fatia, nao uma fotografia.
    """
    css = _css()
    bloco = css.split(".galeria-grande {")[1].split("}")[0]

    assert "aspect-ratio: 3 / 2" in bloco
    assert "height: 22rem" not in bloco

    # E cabe na tela: uma imagem de galeria que obrigue a rolar para ser
    # vista inteira deixa as miniaturas fora de vista.
    assert "max-height: 60vh" in bloco


def test_a_miniatura_usa_a_mesma_proporcao_da_grande():
    """Proporcoes diferentes mostram enquadramentos diferentes.

    O recorte que a pessoa ve na miniatura precisa ser o que ela encontra
    ao ampliar.
    """
    css = _css()

    grande = css.split(".galeria-grande {")[1].split("}")[0]
    miniatura = css.split("\n.miniatura {")[1].split("}")[0]

    achado_grande = re.search(r"aspect-ratio:\s*([\d\s/]+);", grande)
    achado_mini = re.search(r"aspect-ratio:\s*([\d\s/]+);", miniatura)

    assert achado_grande and achado_mini
    assert achado_grande.group(1).strip() == achado_mini.group(1).strip()


def test_o_botao_de_chamada_tem_respiro_dos_dois_lados():
    css = _css()
    bloco = css.split(".acao-chale {")[1].split("}")[0]

    achado = re.search(r"margin:\s*([\d.]+)rem\s+0\s+([\d.]+)rem", bloco)
    assert achado, f"a margem do botao mudou de forma: {bloco}"

    assert float(achado.group(1)) >= 1.0, "falta respiro acima do botao"
    assert float(achado.group(2)) > 0, "falta respiro abaixo do botao"


# ---------------------------------------------------------------------------
# Quarta rodada — o botao Sair e a navegacao do hospede, 13/11
# ---------------------------------------------------------------------------

HOSPEDE = ("joao", "Villa@Hospede2026")


def _entrar_como(aplicacao, credenciais):
    cliente = aplicacao.test_client()
    pagina = cliente.get("/login").data.decode()
    dados = {"login": credenciais[0], "senha": credenciais[1]}
    achado = re.search(r'name="csrf_token" value="([^"]+)"', pagina)
    if achado:
        dados["csrf_token"] = achado.group(1)
    cliente.post("/login", data=dados)
    return cliente


def test_o_sair_e_um_botao_alinhado(aplicacao):
    """Era botao comum, e herdava `margin-top: 1rem`.

    Essa margem existe para afastar o botao de envio do ultimo campo de um
    formulario. Numa barra de navegacao ela empurrava o "Sair" para baixo
    da linha, desalinhado de tudo o mais.
    """
    html = _entrar_como(aplicacao, ADMIN).get("/painel/").data.decode()

    assert 'class="sair"' in html

    bloco = _css().split(".barra .sair {")[1].split("}")[0]
    assert "margin: 0" in bloco, (
        "sem zerar a margem, o botao desce para fora da linha da barra"
    )
    assert "border-radius" in bloco


def test_o_sair_tem_a_mesma_cara_do_entrar():
    """Sao a mesma coisa vista dos dois lados: atravessar a porta.

    Quem reconhece um precisa reconhecer o outro — e, se as duas regras
    divergirem, um deles vira um botao estranho numa tela so.
    """
    css = _css()

    entrar = css.split(".cabecalho-publico .entrar {")[1].split("}")[0]
    sair = css.split(".barra .sair {")[1].split("}")[0]

    for propriedade in ("background", "color", "border-radius", "font-weight"):
        nos_dois = propriedade in entrar and propriedade in sair
        assert nos_dois, f"{propriedade} falta em um dos dois botoes"

    def valor(bloco, nome):
        achado = re.search(rf"{nome}:\s*([^;]+);", bloco)
        return achado.group(1).strip() if achado else None

    assert valor(entrar, "background") == valor(sair, "background")
    assert valor(entrar, "color") == valor(sair, "color")


def test_o_hospede_alcanca_os_chales_e_a_disponibilidade(aplicacao):
    """O cercado, e o que o abriu.

    Quem entrava como hospede via duas telas: o painel e as proprias
    reservas. A face publica continuava aberta — nao exige sessao —, mas
    sem link para ela a partir de dentro a pessoa nao tinha como chegar.

    A alternativa seria criar telas internas equivalentes, que mostrariam
    a mesma coisa com outro molde e teriam de ser mantidas em dobro.
    """
    html = _entrar_como(aplicacao, HOSPEDE).get("/painel/").data.decode()
    barra = html.split("</header>")[0]

    assert "/disponibilidade" in barra, (
        "o hospede precisa alcancar a consulta de disponibilidade"
    )
    assert "Chalés" in barra
    assert "/reservas/minhas" in barra


def test_a_marca_da_barra_leva_a_vitrine(aplicacao):
    """E vale para os tres perfis, nao so para o hospede."""
    for credenciais in (ADMIN, HOSPEDE):
        html = _entrar_como(aplicacao, credenciais).get("/painel/").data.decode()
        barra = html.split("</header>")[0]

        achado = re.search(r'<a class="marca" href="([^"]+)"', barra)
        assert achado, f"a marca deixou de ser link para {credenciais[0]}"
        assert achado.group(1) == "/", (
            "a marca precisa levar a vitrine publica"
        )


def test_a_tela_de_reservas_vazia_diz_o_que_fazer(aplicacao):
    """Uma tela vazia que nao diz o que fazer e um beco.

    Ate o Bloco 7, o caminho era consultar as datas e falar com a
    recepcao, e a tela dizia que a reserva pelo site estava "em
    construcao". Com o Bloco 7, o caminho e o proprio pedido — e a nota
    antiga, que passou a ser mentira, nao pode sobreviver.
    """
    html = _entrar_como(aplicacao, HOSPEDE).get(
        "/reservas/minhas"
    ).data.decode()

    # Verificado com ou sem reservas: a massa dá reservas ao `joao`, e o
    # caminho precisa aparecer nos dois casos — quem já tem uma reserva
    # também faz a próxima.
    assert 'href="/reservas/pedir"' in html, (
        "a tela precisa oferecer o pedido de reserva"
    )
    assert "Fazer uma reserva" in html
    assert "em construção" not in html, (
        "a nota de que a reserva pelo site não existe ficou para trás"
    )


def test_o_hospede_continua_sem_alcancar_as_telas_da_equipe(aplicacao):
    """Os links novos sao da face PUBLICA, e nao afrouxaram nada.

    Esconder link e conveniencia de interface, nao controle de acesso — e
    o contrario tambem vale: mostrar link para rota publica nao da acesso
    a rota interna nenhuma.
    """
    cliente = _entrar_como(aplicacao, HOSPEDE)

    for rota in (
        "/reservas/",
        "/reservas/nova",
        "/reservas/disponibilidade",
        "/hospedes/",
        "/contatos/",
        "/chales/",
    ):
        assert cliente.get(rota).status_code == 403, rota
