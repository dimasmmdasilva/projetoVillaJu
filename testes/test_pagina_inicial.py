"""O logo e as fotos da página inicial — Etapa 5, Bloco 18e.

Pedido do Dimas em 21/11: o logo pequeno na barra e grande ao lado do
nome no banner, e as fotos da pousada passando sozinhas entre a
apresentação e "Consultar disponibilidade".

O que estes testes guardam:

1. **Nenhuma imagem publicada leva metadado.** Foto de câmera diz modelo,
   data, hora e, às vezes, o lugar exato (GPS). As destas pastas foram
   gravadas só com os pixels; o teste lê os marcadores do JPEG, sem
   biblioteca de imagem, e reprova qualquer bloco além do JFIF.
2. **Leves e com nome previsível.** Até 250 KB, sem espaço no nome.
3. **Cada foto tem texto alternativo**, e só a primeira carrega de cara.
4. **Dá para parar.** Caixa "Pausar fotos", pausa com o cursor em cima, e
   nenhum movimento para quem pediu menos movimento (WCAG 2.2.2).
5. **CSS e lista concordam:** um atraso por foto, no ciclo certo.
"""

import html
import pathlib
import re

import pytest

from app import create_app
from app.config import Configuracao
from app.dados import conexao
from app.rotas.publico import FOTOS_DA_POUSADA

pytestmark = pytest.mark.banco

RAIZ = pathlib.Path(__file__).resolve().parents[1]
IMAGENS = RAIZ / "app" / "static" / "img"
CSS = (RAIZ / "app" / "static" / "css" / "villaju.css").read_text(encoding="utf-8")
LIMITE_EM_BYTES = 250 * 1024
SEGUNDOS_POR_FOTO = 4
ADMIN = ("admin", "Villa@Admin2026")
HOSPEDE = ("joao", "Villa@Hospede2026")

_MOVIMENTO = re.compile(
    r"@media\s*\(prefers-reduced-motion:\s*reduce\)\s*\{((?:[^{}]*\{[^{}]*\})*[^{}]*)\}"
)
REDUZIDO = "\n".join(_MOVIMENTO.findall(CSS))
NORMAL = _MOVIMENTO.sub("", CSS)


def _publicadas():
    return sorted((IMAGENS / "inicio").iterdir()) + sorted((IMAGENS / "marca").iterdir())


def _blocos_do_jpeg(dados: bytes) -> list:
    """Os marcadores antes da imagem propriamente dita (SOS)."""
    assert dados[:2] == b"\xff\xd8", "não é JPEG"
    blocos, i = [], 2
    while i < len(dados):
        assert dados[i] == 0xFF, f"marcador quebrado na posição {i}"
        marcador = dados[i + 1]
        if marcador == 0xDA:
            return blocos
        tamanho = int.from_bytes(dados[i + 2:i + 4], "big")
        blocos.append((marcador, dados[i + 4:i + 4 + min(tamanho - 2, 12)]))
        i += 2 + tamanho
    raise AssertionError("JPEG sem início de imagem")


def _valor_final(css, seletor, propriedade):
    valor = None
    for seletores, corpo in re.findall(r"([^{}]+)\{([^{}]*)\}", css):
        lista = [s.strip() for s in re.sub(r"/\*.*?\*/", "", seletores, flags=re.S).split(",")]
        if seletor in lista:
            achado = re.findall(rf"(?:^|;|\s){re.escape(propriedade)}\s*:\s*([^;]+);", corpo)
            if achado:
                valor = achado[-1].strip()
    return valor


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
    yield app
    conexao.encerrar_pool()


@pytest.fixture
def inicial(aplicacao):
    return aplicacao.test_client().get("/").data.decode()


def _fotos_na_pagina(pagina):
    bloco = pagina[pagina.index('class="fotos-da-pousada"'):]
    bloco = bloco[:bloco.index("</ul>")]
    return re.findall(r"<img\b[^>]*>", bloco, re.S)


def _atributo(tag, nome):
    achado = re.search(rf'\b{nome}="([^"]*)"', tag)
    return html.unescape(achado.group(1)) if achado else None


# ===========================================================================
# 1 e 2. Os arquivos
# ===========================================================================

@pytest.mark.parametrize("arquivo", _publicadas(), ids=lambda a: a.name)
def test_a_imagem_publicada_nao_leva_metadado(arquivo):
    """Só o JFIF (APP0) e as tabelas da imagem. EXIF e XMP vêm em APP1,
    o perfil de cor em APP2, o Photoshop em APP13; comentário em COM."""
    proibidos = [
        hex(m) for m, _ in _blocos_do_jpeg(arquivo.read_bytes())
        if 0xE1 <= m <= 0xEF or m == 0xFE
    ]
    assert not proibidos, f"{arquivo.name} leva metadado: {proibidos}"


@pytest.mark.parametrize("arquivo", _publicadas(), ids=lambda a: a.name)
def test_a_imagem_publicada_e_leve_e_tem_nome_simples(arquivo):
    assert re.fullmatch(r"[a-z0-9-]+\.jpg", arquivo.name), arquivo.name
    assert arquivo.stat().st_size <= LIMITE_EM_BYTES, (
        f"{arquivo.name} tem {arquivo.stat().st_size // 1024} KB"
    )


def test_as_pastas_tem_so_o_que_a_pagina_usa():
    """Foto solta na pasta é foto que ninguém tratou — e é servida."""
    assert {a.name for a in (IMAGENS / "inicio").iterdir()} == {
        arquivo for arquivo, _ in FOTOS_DA_POUSADA
    }
    assert {a.name for a in (IMAGENS / "marca").iterdir()} == {
        "villaju-logo.jpg", "villaju-selo.jpg"
    }


# ===========================================================================
# 3. A página
# ===========================================================================

def test_cada_foto_aparece_com_o_seu_texto_alternativo(inicial):
    fotos = _fotos_na_pagina(inicial)
    assert len(fotos) == len(FOTOS_DA_POUSADA)
    for tag, (arquivo, descricao) in zip(fotos, FOTOS_DA_POUSADA):
        assert _atributo(tag, "src") == f"/static/img/inicio/{arquivo}"
        assert descricao and _atributo(tag, "alt") == descricao


def test_so_a_primeira_foto_carrega_de_imediato(inicial):
    fotos = _fotos_na_pagina(inicial)
    assert _atributo(fotos[0], "loading") is None
    assert all(_atributo(t, "loading") == "lazy" for t in fotos[1:])


def test_as_fotos_ficam_entre_a_apresentacao_e_a_consulta(inicial):
    apresentacao = inicial.index('class="apresentacao"')
    fotos = inicial.index('class="fotos-da-pousada"')
    consulta = inicial.index("Consultar disponibilidade", apresentacao)
    assert apresentacao < fotos < consulta


def test_o_logo_esta_no_banner(inicial):
    banner = inicial[inicial.index('class="banner"'):]
    banner = banner[:banner.index("</section>")]
    assert "/static/img/marca/villaju-logo.jpg" in banner
    assert '<h1 class="apenas-leitor">' in banner


def test_o_selo_esta_nas_duas_barras(aplicacao):
    publica = aplicacao.test_client().get("/").data.decode()
    cliente = aplicacao.test_client()
    cliente.post("/login", data={"login": ADMIN[0], "senha": ADMIN[1]})
    interna = cliente.get("/painel/").data.decode()
    for pagina in (publica, interna):
        barra = pagina[:pagina.index("</header>")]
        marca = re.search(r'<a class="marca"[^>]*>(.*?)</a>', barra, re.S)
        assert marca and "/static/img/marca/villaju-selo.jpg" in marca.group(1)
        # O nome continua escrito: o selo é decoração, de `alt` vazio.
        assert 'alt=""' in marca.group(1) and "VillaJu" in marca.group(1)


@pytest.mark.parametrize("credenciais,rotulo", [
    (ADMIN, "administrador"),
    # O banco guarda `hospede`; a tela escreve com acento.
    (HOSPEDE, "hóspede"),
])
def test_a_barra_mostra_so_o_perfil_em_portugues(aplicacao, credenciais, rotulo):
    cliente = aplicacao.test_client()
    cliente.post("/login", data={"login": credenciais[0], "senha": credenciais[1]})
    pagina = cliente.get("/painel/").data.decode()
    achado = re.search(r'<span class="identificacao">\s*(.*?)\s*</span>', pagina, re.S)
    assert achado and achado.group(1) == rotulo


# ===========================================================================
# 4. Parar
# ===========================================================================

def test_a_caixa_de_pausa_vem_antes_da_lista(inicial):
    """`:checked ~` só alcança irmãos seguintes."""
    bloco = inicial[inicial.index('class="fotos-da-pousada"'):]
    caixa = bloco.index('type="checkbox" id="pausar-fotos"')
    rotulo = bloco.index('<label for="pausar-fotos">Pausar fotos</label>')
    lista = bloco.index('class="fotos-da-pousada__lista"')
    assert caixa < rotulo < lista


@pytest.mark.parametrize("seletor", [
    ".fotos-da-pousada:focus-within .fotos-da-pousada__lista li",
    ".fotos-da-pousada__pausa:checked ~ .fotos-da-pousada__lista li",
])
def test_a_caixa_marcada_ou_com_foco_para_as_fotos(seletor):
    assert _valor_final(NORMAL, seletor, "animation-play-state") == "paused"


def test_o_cursor_em_cima_nao_para_as_fotos():
    """22/11, pedido do Dimas: as fotos param só pelo botão "Pausar fotos".
    A WCAG 2.2.2 pede UM jeito de parar; a caixa basta."""
    for seletores, corpo in re.findall(r"([^{}]+)\{([^{}]*)\}", NORMAL):
        if "fotos-da-pousada" in seletores and ":hover" in seletores:
            assert "animation-play-state" not in corpo, seletores


def test_quem_pede_menos_movimento_ve_a_primeira_foto_parada():
    assert _valor_final(REDUZIDO, ".fotos-da-pousada__lista li", "animation") == "none"
    assert _valor_final(NORMAL, ".fotos-da-pousada__lista li", "opacity") == "0"
    assert _valor_final(NORMAL, ".fotos-da-pousada__lista li:first-child", "opacity") == "1"
    assert _valor_final(REDUZIDO, ".fotos-da-pousada__pausa", "display") == "none"


# ===========================================================================
# 5. CSS e lista concordam
# ===========================================================================

def test_um_atraso_por_foto_no_ciclo_certo():
    total = len(FOTOS_DA_POUSADA)
    animacao = _valor_final(NORMAL, ".fotos-da-pousada__lista li", "animation")
    assert animacao == f"troca-de-foto {total * SEGUNDOS_POR_FOTO}s linear infinite"
    for n in range(1, total + 1):
        atraso = _valor_final(
            NORMAL, f".fotos-da-pousada__lista li:nth-child({n})", "animation-delay"
        )
        assert atraso == f"{(n - 1) * SEGUNDOS_POR_FOTO - 1}s", (n, atraso)
    assert _valor_final(
        NORMAL, f".fotos-da-pousada__lista li:nth-child({total + 1})", "animation-delay"
    ) is None


# ===========================================================================
# O banner só com o logo (22/11, escolha do Dimas)
#
# Cinco versões até aqui; ficou a mais simples: o logo, que já escreve o
# nome e o lema. Os testes das versões com texto (cores, contraste do
# lema, traço, centralização) saíram com o texto.
# ===========================================================================

_MEDIA = re.compile(r"@media[^{]*\{((?:[^{}]*\{[^{}]*\})*[^{}]*)\}")
SEM_MEDIA = _MEDIA.sub("", CSS)
CREME = "#f6e8d7"


def _cor(seletor, propriedade):
    from test_paleta import resolver

    return resolver(_valor_final(SEM_MEDIA, seletor, propriedade))


def _dimensoes_do_jpeg(arquivo):
    """Largura e altura lidas do marcador SOF, sem biblioteca de imagem."""
    dados = arquivo.read_bytes()
    i = 2
    while i < len(dados):
        marcador = dados[i + 1]
        tamanho = int.from_bytes(dados[i + 2:i + 4], "big")
        if marcador in (0xC0, 0xC1, 0xC2):
            altura = int.from_bytes(dados[i + 5:i + 7], "big")
            largura = int.from_bytes(dados[i + 7:i + 9], "big")
            return largura, altura
        i += 2 + tamanho
    raise AssertionError("JPEG sem SOF")


def test_o_banner_e_creme_como_o_fundo_do_logo():
    assert _cor(".banner", "background") == CREME
    assert _cor(".banner", "background") != _cor("body", "background")


def test_o_logo_fica_no_centro_e_cabe_em_tela_estreita():
    assert _valor_final(SEM_MEDIA, ".banner", "justify-content") == "center"
    assert _valor_final(SEM_MEDIA, ".banner__logo", "max-width") == "100%"
    assert _valor_final(SEM_MEDIA, ".banner__logo", "height") == "auto"


def test_o_logo_publicado_e_nitido_no_tamanho_mostrado():
    """Tela de alta densidade usa 2 pixels por ponto: o arquivo precisa ter
    o dobro da largura mostrada. A cópia do 18e tinha 400 px para 260 na
    tela, e aparecia borrada — foi o que o Dimas viu."""
    # A largura mostrada vem do CSS, não de uma constante do teste: quem
    # aumentar o logo na folha sem trocar o arquivo é avisado aqui.
    mostrada = int(_valor_final(SEM_MEDIA, ".banner__logo", "width").removesuffix("px"))
    largura, altura = _dimensoes_do_jpeg(IMAGENS / "marca" / "villaju-logo.jpg")
    assert largura == altura
    assert largura >= 2 * mostrada, (largura, mostrada)


def test_o_banner_tem_so_o_logo_e_o_titulo_escondido(inicial):
    from app.rotas.publico import HOSPEDARIA

    banner = inicial[inicial.rindex("<section", 0, inicial.index('class="banner"')):]
    banner = banner[:banner.index("</section>")]
    # O nome é o título da página, para o leitor de tela e o buscador,
    # fora da vista: o logo já o escreve.
    assert f'<h1 class="apenas-leitor">{HOSPEDARIA["nome"]}</h1>' in banner
    # O logo deixou de ser decoração: é o texto do banner.
    alt = html.unescape(re.search(r'<img\b[^>]*\balt="([^"]*)"', banner).group(1))
    for parte in (HOSPEDARIA["nome"], HOSPEDARIA["lema"], HOSPEDARIA["lema_complemento"]):
        assert parte in alt
    # Nenhum outro texto visível no banner.
    visivel = re.sub(r"<h1[^>]*>.*?</h1>|<[^>]+>|\{#.*?#\}", "", banner, flags=re.S)
    assert not visivel.strip(), visivel.strip()
    # A descrição para o buscador continua informativa.
    assert f'content="{HOSPEDARIA["chamada"]}"' in inicial
