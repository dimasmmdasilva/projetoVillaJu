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
    assert "<h1>" in banner


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
    ".fotos-da-pousada:hover .fotos-da-pousada__lista li",
    ".fotos-da-pousada:focus-within .fotos-da-pousada__lista li",
    ".fotos-da-pousada__pausa:checked ~ .fotos-da-pousada__lista li",
])
def test_cursor_foco_e_caixa_param_as_fotos(seletor):
    assert _valor_final(NORMAL, seletor, "animation-play-state") == "paused"


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
# Blocos 18f e 18d — o banner: logo maior, cores e lema do logo
# ===========================================================================

_MEDIA = re.compile(r"@media[^{]*\{((?:[^{}]*\{[^{}]*\})*[^{}]*)\}")
SEM_MEDIA = _MEDIA.sub("", CSS)
ESTREITA = "\n".join(
    corpo for consulta, corpo in re.findall(
        r"@media\s*(\(max-width:[^)]*\))\s*\{((?:[^{}]*\{[^{}]*\})*[^{}]*)\}", CSS)
)


# 22/11, escolha do Dimas: o banner passou do branco do 18f às cores do
# próprio logo. Os testes do branco deram lugar a estes.
CREME, OLIVA, TERRACOTA = "#f6e8d7", "#5e5835", "#96582e"


def _contraste(cor_a, cor_b):
    """Razão de contraste da WCAG 2.x entre duas cores #rrggbb."""
    def luminancia(cor):
        canais = []
        for i in (1, 3, 5):
            c = int(cor[i:i + 2], 16) / 255
            canais.append(c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4)
        r, g, b = canais
        return 0.2126 * r + 0.7152 * g + 0.0722 * b
    claro, escuro = sorted((luminancia(cor_a), luminancia(cor_b)), reverse=True)
    return (claro + 0.05) / (escuro + 0.05)


@pytest.mark.parametrize("seletor,propriedade,valor", [
    (".banner", "background", CREME),
    (".banner h1", "color", OLIVA),
    (".banner .lema", "color", TERRACOTA),
    (".banner .lema-complemento", "color", OLIVA),
])
def test_o_banner_usa_as_cores_do_logo(seletor, propriedade, valor):
    assert _valor_final(SEM_MEDIA, seletor, propriedade) == valor


@pytest.mark.parametrize("seletor", [
    ".banner h1", ".banner .lema", ".banner .lema-complemento",
])
def test_o_texto_do_banner_passa_o_contraste(seletor):
    """WCAG AA para texto normal: 4,5:1. O terracota do logo (#c1723c) dá
    3:1 e não passaria — por isso o lema usa um tom mais escuro."""
    fundo = _valor_final(SEM_MEDIA, ".banner", "background")
    cor = _valor_final(SEM_MEDIA, seletor, "color")
    assert _contraste(cor, fundo) >= 4.5, (seletor, cor, round(_contraste(cor, fundo), 2))
    assert _contraste("#c1723c", CREME) < 4.5  # o motivo da troca continua valendo


def test_o_banner_se_separa_do_fundo_da_pagina_sem_borda():
    assert _valor_final(SEM_MEDIA, "body", "background") == "#fafafa"
    assert _valor_final(SEM_MEDIA, ".banner", "background") != "#fafafa"
    assert _valor_final(SEM_MEDIA, ".banner", "border") == "none"


def test_logo_e_letras_maiores_que_no_18e():
    def rem(seletor):
        return float(_valor_final(SEM_MEDIA, seletor, "font-size").removesuffix("rem"))

    assert int(_valor_final(SEM_MEDIA, ".banner__logo", "width").removesuffix("px")) >= 240
    assert _valor_final(SEM_MEDIA, ".banner__logo", "width") ==         _valor_final(SEM_MEDIA, ".banner__logo", "height")
    assert rem(".banner h1") > 2.2
    assert rem(".banner h1") > rem(".banner .lema")


def test_o_banner_mostra_o_lema_do_logo_em_ordem(inicial):
    from app.rotas.publico import HOSPEDARIA

    banner = inicial[inicial.index('class="banner"'):]
    banner = banner[:banner.index("</section>")]
    titulo = banner.index("<h1>")
    lema = banner.index(f'<p class="lema">{HOSPEDARIA["lema"]}</p>')
    traco = banner.index('<span class="banner__divisor" aria-hidden="true"></span>')
    complemento = banner.index(
        f'<p class="lema-complemento">{HOSPEDARIA["lema_complemento"]}</p>')
    assert titulo < lema < traco < complemento
    assert HOSPEDARIA["lema"] == "Sua casa na praia."
    assert HOSPEDARIA["lema_complemento"] == "Entre o mar e a mata da Juréia."
    # A descrição para o buscador continua informativa.
    assert f'content="{HOSPEDARIA["chamada"]}"' in inicial
    assert HOSPEDARIA["chamada"] not in banner


def test_a_regra_de_tela_estreita_vem_depois_do_titulo_grande():
    """Mesma especificidade: vale a que vem por último na folha."""
    grande = CSS.rindex("font-size: 3.4rem;")
    estreita = CSS.rindex("@media (max-width: 40rem)")
    assert estreita > grande
    assert _valor_final(ESTREITA, ".banner h1", "font-size") == "2.4rem"


def test_em_tela_estreita_o_logo_encolhe():
    assert _valor_final(ESTREITA, ".banner__logo", "width") == "180px"


# 22/11, pedido do Dimas: logo e texto centralizados, o texto alinhado ao
# traço, e o espaçamento das letras compensado para o centro ser o centro.

def test_o_conjunto_e_o_texto_do_banner_ficam_no_centro():
    assert _valor_final(SEM_MEDIA, ".banner", "justify-content") == "center"
    assert _valor_final(SEM_MEDIA, ".banner__texto", "text-align") == "center"
    assert _valor_final(SEM_MEDIA, ".banner__texto", "flex") == "0 1 auto"


def test_o_traco_tem_a_largura_do_texto_e_fica_no_centro():
    assert _valor_final(SEM_MEDIA, ".banner__divisor", "width") == "100%"
    assert _valor_final(SEM_MEDIA, ".banner__divisor", "margin").endswith("auto")


@pytest.mark.parametrize("folha", ["normal", "estreita"])
@pytest.mark.parametrize("seletor", [".banner h1", ".banner .lema"])
def test_o_espacamento_das_letras_e_compensado(folha, seletor):
    """O `letter-spacing` sobra depois da última letra; o recuo à esquerda
    do mesmo tamanho devolve o centro. Em tela estreita o espaçamento do
    lema muda, e o recuo precisa mudar junto."""
    css = SEM_MEDIA if folha == "normal" else ESTREITA
    espaco = _valor_final(css, seletor, "letter-spacing") or \
        _valor_final(SEM_MEDIA, seletor, "letter-spacing")
    recuo = _valor_final(css, seletor, "padding-left") or \
        _valor_final(SEM_MEDIA, seletor, "padding-left")
    assert espaco and espaco == recuo, (folha, seletor, espaco, recuo)
