"""A vitrine pública, e o que ela não alcança.

A face pública é a única parte do sistema que um desconhecido atinge: não
exige senha, é indexada por buscadores e recebe tráfego automatizado o
tempo todo. É, por isso, o alvo mais exposto do projeto.

A tarefa 11 da Etapa 4 enuncia a regra que a contém:

    a face pública não compartilha sessão com a área autenticada e não
    acessa nenhuma tabela além de `chale` (leitura) e `contato` (escrita).

Metade destes testes verifica o que a vitrine mostra. A outra metade
verifica o que ela **não** consegue mostrar — e essa metade é a que
importa, porque um teste que confirma que os cinco chalés aparecem passa
igualzinho numa página que também vazasse a lista de hóspedes.
"""

import re

import pytest

from app import create_app
from app.config import Configuracao
from app.dados import conexao

pytestmark = pytest.mark.banco

RECEPCAO = ("recepcao", "Villa@Recep2026")

CHALES = ("Embaré", "Camburi", "Asturias", "Boracéia", "Dunas")


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

    conexao.iniciar_pool(Configuracao(modo_teste=True))
    app = create_app(Configuracao(modo_teste=True))
    app.config["SESSION_COOKIE_SECURE"] = False
    yield app
    conexao.encerrar_pool()


@pytest.fixture
def visitante(aplicacao):
    """Cliente sem sessão. É assim que o mundo chega ao sistema."""
    return aplicacao.test_client()


def _entrar(aplicacao, credenciais=RECEPCAO):
    cliente = aplicacao.test_client()
    pagina = cliente.get("/login").data.decode()
    dados = {"login": credenciais[0], "senha": credenciais[1]}
    achado = re.search(r'name="csrf_token" value="([^"]+)"', pagina)
    if achado:
        dados["csrf_token"] = achado.group(1)
    cliente.post("/login", data=dados)
    return cliente


# ---------------------------------------------------------------------------
# A raiz deixou de ser o login
# ---------------------------------------------------------------------------

def test_a_raiz_serve_a_vitrine_sem_sessao(visitante):
    """Era a correção de produto mais visível do projeto.

    Até a Etapa 3 a raiz redirecionava ao login: um hóspede em potencial
    chegava ao endereço da hospedaria e encontrava um pedido de senha.
    """
    resposta = visitante.get("/")

    assert resposta.status_code == 200
    assert "VillaJu" in resposta.data.decode()


def test_a_raiz_nao_redireciona(visitante):
    """Sem `follow_redirects`: a resposta precisa ser a página, não um 302."""
    assert visitante.get("/").status_code == 200


def test_a_vitrine_oferece_o_caminho_da_equipe(visitante):
    html = visitante.get("/").data.decode()
    assert "/login" in html
    assert "Área da equipe" in html


def test_quem_tem_sessao_recebe_o_atalho_para_o_painel(aplicacao):
    """Conveniência: já autenticado, o link leva ao painel, não ao login."""
    html = _entrar(aplicacao).get("/").data.decode()

    assert "/painel" in html
    assert "Área da equipe" not in html


def test_a_vitrine_e_a_mesma_para_todos(aplicacao, visitante):
    """Nenhuma decisão de conteúdo depende de quem está olhando.

    Só o link da equipe muda. Se o corpo da página variasse por sessão,
    haveria aqui lógica de autorização — e a face pública não deveria ter
    nenhuma.
    """
    anonimo = visitante.get("/").data.decode()
    autenticado = _entrar(aplicacao).get("/").data.decode()

    for chale in CHALES:
        assert (chale in anonimo) == (chale in autenticado)


# ---------------------------------------------------------------------------
# O que a vitrine mostra
# ---------------------------------------------------------------------------

def test_os_cinco_chales_aparecem(visitante):
    html = visitante.get("/").data.decode()
    for chale in CHALES:
        assert chale in html, f"{chale} não aparece na vitrine"


def test_cada_chale_traz_capacidade_camas_e_comodidades(visitante):
    html = visitante.get("/").data.decode()

    assert "até 4 pessoas" in html
    assert "cama de casal" in html
    assert "Wi-Fi" in html


def test_a_localizacao_aparece(visitante):
    html = visitante.get("/").data.decode()
    assert "Alameda Jubéris" in html
    assert "São Sebastião" in html


def test_chale_desativado_nao_aparece(aplicacao, visitante):
    """Desativar é como a hospedaria tira um chalé de operação.

    Anunciá-lo produziria pedido de reserva que a recepção teria de
    recusar.
    """
    assert "Dunas" in visitante.get("/").data.decode()

    with conexao.obter_conexao() as ligacao:
        cursor = ligacao.cursor()
        cursor.execute("UPDATE chale SET ativo = FALSE WHERE nome = 'Dunas'")
        cursor.close()

    html = visitante.get("/").data.decode()

    assert "Dunas" not in html
    assert "Camburi" in html, "os demais precisam continuar na vitrine"


# ---------------------------------------------------------------------------
# O que a vitrine NÃO alcança
# ---------------------------------------------------------------------------

def test_a_vitrine_nao_mostra_hospede_algum(aplicacao, visitante):
    """A verificação mais importante deste arquivo.

    Os nomes vêm do repositório cifrado, decifrados. Se algum aparecer na
    página aberta, o sistema inteiro perde o sentido.
    """
    from app.dados import hospede_repo

    html = visitante.get("/").data.decode()

    for hospede in hospede_repo.listar():
        if hospede.get("nome"):
            assert hospede["nome"] not in html
        if hospede.get("documento"):
            assert hospede["documento"] not in html
        if hospede.get("email"):
            assert hospede["email"] not in html


def test_a_vitrine_nao_mostra_reserva_alguma(visitante):
    from app.dados import reserva_repo

    html = visitante.get("/").data.decode()

    reservas = reserva_repo.listar()
    assert reservas, "a massa precisa ter reservas para este teste valer"

    for reserva in reservas:
        # Datas, em qualquer dos dois formatos que o sistema usa.
        for data in (reserva["data_entrada"], reserva["data_saida"]):
            assert f"{data:%d/%m/%Y}" not in html
            assert f"{data:%Y-%m-%d}" not in html

        # E o valor da estadia, que é o dado comercial sensível.
        if reserva["valor_total"] is not None:
            assert f"{reserva['valor_total']:.2f}" not in html

    # Procurar pela palavra "reserva" no HTML não serviria: ela aparece na
    # descrição de um dos chalés ("o mais reservado dos cinco"). A
    # primeira versão deste teste fazia isso e falhou por esse motivo —
    # uma asserção sobre prosa não verifica vazamento de dado.
    assert 'class="barra"' not in html


def test_a_vitrine_nao_mostra_conta_de_usuario(visitante):
    html = visitante.get("/").data.decode()
    for login in ("admin", "recepcao", "joao"):
        assert f">{login}<" not in html


def test_a_vitrine_nao_mostra_preco(visitante):
    """Tarifa não é informação pública nesta etapa.

    O preço depende do período e da temporada, e publicá-lo como número
    solto convidaria a uma expectativa que a recepção teria de corrigir.
    Quem quiser saber usa o pedido de contato, que entra no Bloco 2.
    """
    assert "R$" not in visitante.get("/").data.decode()


def test_o_modulo_publico_nao_importa_repositorio_proibido():
    """A regra verificada no código, não só na saída.

    Um teste sobre o HTML confirma que o dado não vazou *nesta* página,
    com *esta* massa. Este confirma que a rota não tem como alcançá-lo:
    `app/rotas/publico.py` não importa `hospede_repo`, `reserva_repo`,
    `usuario_repo` nem `auditoria_repo`.

    É a diferença entre não ter vazado e não poder vazar.
    """
    import ast
    import pathlib

    raiz = pathlib.Path(__file__).resolve().parents[1]
    arvore = ast.parse(
        (raiz / "app" / "rotas" / "publico.py").read_text(encoding="utf-8")
    )

    importados = set()
    for no in ast.walk(arvore):
        if isinstance(no, ast.ImportFrom):
            for alvo in no.names:
                importados.add(alvo.name)
            if no.module:
                importados.add(no.module)
        elif isinstance(no, ast.Import):
            for alvo in no.names:
                importados.add(alvo.name)

    proibidos = {
        "hospede_repo",
        "reserva_repo",
        "usuario_repo",
        "auditoria_repo",
        "tarifa_repo",
        "calculo",
    }
    achados = {nome for nome in importados if nome.split(".")[-1] in proibidos}

    assert not achados, (
        f"a face pública importa repositório que não deveria alcançar: "
        f"{sorted(achados)}"
    )


def test_a_vitrine_nao_exige_sessao_nem_cria_uma(visitante):
    """O visitante é anônimo e continua anônimo.

    Criar sessão para quem só olha a página gastaria cookie, convidaria a
    rastreamento e daria a uma rota pública um estado que ela não precisa.
    """
    visitante.get("/")

    with visitante.session_transaction() as sessao:
        assert not dict(sessao)


# ---------------------------------------------------------------------------
# Molde próprio, não o da área autenticada
# ---------------------------------------------------------------------------

def test_a_vitrine_nao_desenha_a_barra_da_area_interna(visitante):
    """Dois moldes separados, e este é o motivo.

    `base.html` desenha Reservas, Disponibilidade e Hóspedes conforme o
    perfil. Herdar dele aqui faria a página pública depender de lógica de
    autorização que ela não deveria sequer consultar.
    """
    html = visitante.get("/").data.decode()

    assert 'class="barra"' not in html
    for rota in ("/reservas/", "/hospedes/", "/chales/", "/auditoria"):
        assert rota not in html


def test_a_vitrine_nao_desenha_a_barra_nem_para_quem_tem_sessao(aplicacao):
    """O caso que de fato distingue os dois moldes.

    Para o visitante anônimo, `base.html` também não desenharia a barra —
    ela depende de `sessao_usuario`. Logo, a versão anterior deste teste
    passava mesmo com o molde errado, e foi a sabotagem que mostrou isso:
    trocar `publico/base.html` por `base.html` foi detectado por outro
    teste, não por este.

    O risco real é o membro da equipe que abre a vitrine com a sessão
    aberta e recebe a navegação interna numa página pública.
    """
    html = _entrar(aplicacao).get("/").data.decode()

    assert 'class="barra"' not in html
    for rota in ("/reservas/", "/hospedes/", "/auditoria"):
        assert rota not in html, (
            f"a vitrine expôs {rota} a quem tem sessão: está herdando o "
            f"molde da área autenticada"
        )


def test_a_vitrine_nao_carrega_recurso_externo(visitante):
    """Sem CDN e sem fonte remota, como o resto do projeto.

    Um recurso de outro servidor informaria a esse servidor quem acessou
    o sítio da hospedaria e quando.
    """
    html = visitante.get("/").data.decode()

    assert "http://" not in html
    assert "https://" not in html
    assert "<script" not in html


def test_a_pagina_declara_utf8_e_viewport(visitante):
    html = visitante.get("/").data.decode()
    assert 'charset="utf-8"' in html
    assert "viewport" in html


def test_a_vitrine_responde_com_o_banco_fora_do_ar(aplicacao, visitante):
    """A página inicial da hospedaria não cai junto com o MySQL.

    Este teste nasceu de uma falha: ao trocar a raiz pela vitrine, um
    teste que exercita a fábrica da aplicação sem banco passou a quebrar,
    porque a nova rota consulta `chale`. A lição foi maior que o conserto
    — a raiz é a página mais visível do sistema, a única que um
    desconhecido encontra pelo buscador, e devolver erro 500 nela por
    causa do banco é o pior lugar possível para falhar.

    A apresentação e a localização são texto fixo no módulo e continuam
    valendo. O visitante segue sabendo onde fica e como é o lugar; o que
    falta é a lista de chalés, e a página diz isso em vez de mentir com
    uma lista vazia.
    """
    conexao.encerrar_pool()
    try:
        resposta = visitante.get("/")
        html = resposta.data.decode()

        assert resposta.status_code == 200
        assert "Alameda Jubéris" in html, "a localização é texto fixo"
        assert "Não foi possível carregar a lista de chalés" in html

        # E não pode fingir que a hospedaria não tem chalés.
        assert "Nenhum chalé disponível" not in html
    finally:
        conexao.iniciar_pool(Configuracao(modo_teste=True))


# ---------------------------------------------------------------------------
# O que a política de conteúdo bloqueia nos templates
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "atributo,explicacao",
    [
        ('style="', "`style-src 'self'` bloqueia estilo embutido"),
        ("<style", "`style-src 'self'` bloqueia bloco <style> embutido"),
        ("onclick=", "`script-src 'self'` bloqueia manipulador embutido"),
        ("onload=", "`script-src 'self'` bloqueia manipulador embutido"),
        ("onerror=", "`script-src 'self'` bloqueia manipulador embutido"),
        ("javascript:", "`script-src 'self'` bloqueia URL javascript:"),
    ],
)
def test_nenhum_template_usa_recurso_que_a_csp_bloqueia(atributo, explicacao):
    """Template que viola a própria política da aplicação falha em silêncio.

    Este teste nasceu de um defeito que a suíte inteira deixou passar e
    que foi visto na tela. A vitrine desenhava uma faixa de cor por chalé
    com `style="background: ..."` no HTML. O navegador bloqueou o
    atributo, porque a aplicação declara:

        Content-Security-Policy: ... style-src 'self' ...

    sem `unsafe-inline`. O resultado não foi erro nem aviso: os blocos
    simplesmente apareceram vazios, e vinte testes continuaram passando,
    porque todos olhavam o HTML que o servidor produziu — que estava
    correto — e nenhum olhava o que o navegador faria com ele.

    A correção foi tirar o estilo do HTML e pô-lo em classe no arquivo
    CSS. **Não** afrouxar a política: permitir estilo embutido para
    resolver um bloco decorativo enfraqueceria a defesa contra XSS em
    todo o sistema, e por motivo cosmético.
    """
    import pathlib

    raiz = pathlib.Path(__file__).resolve().parents[1]
    violacoes = []

    for caminho in sorted((raiz / "app" / "templates").rglob("*.html")):
        texto = caminho.read_text(encoding="utf-8")
        for numero, linha in enumerate(texto.split("\n"), start=1):
            # Comentários do Jinja2 e do HTML explicam a regra; não a violam.
            despido = linha.strip()
            if despido.startswith(("{#", "#}", "<!--")) or "{#" in linha:
                continue
            if atributo in linha:
                relativo = caminho.relative_to(raiz).as_posix()
                violacoes.append(f"{relativo}:{numero}: {despido[:70]}")

    assert not violacoes, (
        f"{explicacao}; o navegador bloqueia e a página falha em "
        f"silêncio:\n  " + "\n  ".join(violacoes)
    )


def test_a_tela_de_entrada_oferece_volta_ao_sitio(visitante):
    """Quem errou de porta precisa poder sair.

    O caminho natural é: visitante chega à vitrine, clica em "Área da
    equipe" por curiosidade, e encontra um pedido de senha. Sem link de
    volta, a única saída é o botão do navegador — e a página de login
    ficava sendo um beco.
    """
    html = visitante.get("/login").data.decode()

    assert "Voltar" in html
    assert 'href="/"' in html


def test_o_cartao_de_entrada_e_centralizado(visitante):
    """O formulário vive num invólucro próprio, e não solto na `main`.

    `.formulario` tem 22rem e a `main` tem 44rem: solto, ele encostava na
    borda esquerda do bloco centralizado e a tela parecia deslocada numa
    janela larga. A centralização é do cartão, no CSS.
    """
    html = visitante.get("/login").data.decode()
    assert 'class="cartao-entrada"' in html
    assert 'class="entrada"' in html, "o corpo precisa declarar a classe"

    import pathlib

    css = (
        pathlib.Path(__file__).resolve().parents[1]
        / "app" / "static" / "css" / "villaju.css"
    ).read_text(encoding="utf-8")

    assert ".cartao-entrada" in css, "a classe existe no HTML mas não no CSS"

    # Horizontal: `auto` nas laterais.
    assert "margin: 0 auto" in css, "o cartão precisa centralizar na horizontal"

    # Vertical: a `main` da tela de entrada vira um contêiner flex. Sem
    # isso o cartão fica colado no topo de uma janela alta, com a página
    # inteira vazia abaixo dele.
    assert "body.entrada main" in css, "falta o tratamento vertical"
    assert "align-items: center" in css
    assert "min-height: 100vh" in css
