"""A paleta do logo no sistema inteiro — Etapa 5, Bloco 18g.

Decisão do Dimas em 22/11, depois do banner creme: o sistema passou do
verde frio (#2f3e34, escrito à mão 29 vezes) às cores do próprio logo —
verde-oliva, creme e terracota —, guardadas uma vez só, em variáveis no
`:root` de `villaju.css`.

O que estes testes guardam:

1. **Uma paleta, num lugar.** Toda variável usada está definida, e
   nenhuma cor da paleta antiga sobrou fora dela.
2. **Contraste calculado, não estimado** (WCAG 2.x): 4,5:1 para texto,
   3:1 para borda de campo e contorno de foco. O banner já provou que
   estimar de cabeça erra: #a85d2c "parecia" passar e dava 4,09.
3. **Cada par conferido é o par que a folha usa.** Contraste bom de um
   par que nenhuma regra usa não prova nada: o teste lê a regra e
   resolve a variável antes de medir.
4. **As cores de significado não mudaram**: vermelho de perigo e erro.
"""

import pathlib
import re

import pytest

RAIZ = pathlib.Path(__file__).resolve().parents[1]
CSS = (RAIZ / "app" / "static" / "css" / "villaju.css").read_text(encoding="utf-8")
SEM_COMENTARIO = re.sub(r"/\*.*?\*/", "", CSS, flags=re.S)
_MEDIA = re.compile(r"@media[^{]*\{((?:[^{}]*\{[^{}]*\})*[^{}]*)\}")
NORMAL = _MEDIA.sub("", SEM_COMENTARIO)

RAIZ_CSS = re.search(r":root\s*\{([^}]*)\}", SEM_COMENTARIO).group(1)
TOKENS = dict(re.findall(r"(--[a-z0-9-]+)\s*:\s*([^;]+);", RAIZ_CSS))

# A paleta anterior ao 18g: nenhuma destas pode sobrar numa regra.
PALETA_ANTIGA = {
    "#2f3e34", "#3d5245", "#4a6456", "#577667", "#648878", "#3f5546",
    "#243029", "#1b241e", "#e8ece9", "#eef2ec", "#e2e8df", "#d6dcd4",
    "#f4f6f3", "#fafafa", "#c99a2e", "#c58b2b", "#5b6a60", "#bbb", "#ddd",
    "#555", "#777", "#666", "#f0f0f0", "#c6d0c9", "#f6f6f6", "#e3e3e3",
    "#d6d6d6", "#8a958d", "#7a6a4f", "#8c6f55", "#6b5d7b", "#4f6d7a",
    "#3f5d6b", "#7d5a5a", "#5a6b4a",
}
BRANCO = "#ffffff"


def resolver(valor):
    """Troca `var(--x)` pelo valor do `:root`, quantas vezes precisar."""
    for _ in range(5):
        novo = re.sub(r"var\((--[a-z0-9-]+)\)", lambda m: TOKENS[m.group(1)].strip(), valor)
        if novo == valor:
            break
        valor = novo
    return valor.strip()


def _hex6(cor):
    cor = cor.strip().lower()
    if cor == "#fff":
        return BRANCO
    if re.fullmatch(r"#[0-9a-f]{3}", cor):
        return "#" + "".join(c * 2 for c in cor[1:])
    assert re.fullmatch(r"#[0-9a-f]{6}", cor), cor
    return cor


def contraste(a, b):
    def luminancia(cor):
        canais = []
        for i in (1, 3, 5):
            c = int(cor[i:i + 2], 16) / 255
            canais.append(c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4)
        return 0.2126 * canais[0] + 0.7152 * canais[1] + 0.0722 * canais[2]
    claro, escuro = sorted((luminancia(_hex6(a)), luminancia(_hex6(b))), reverse=True)
    return (claro + 0.05) / (escuro + 0.05)


def valor(seletor, propriedade):
    """O valor final da propriedade para o seletor (fora de @media),
    com as variáveis resolvidas."""
    achado = None
    for seletores, corpo in re.findall(r"([^{}]+)\{([^{}]*)\}", NORMAL):
        if seletor in [s.strip() for s in seletores.split(",")]:
            v = re.findall(rf"(?:^|;|\s){re.escape(propriedade)}\s*:\s*([^;]+);", corpo)
            if v:
                achado = v[-1]
    assert achado is not None, (seletor, propriedade)
    return resolver(achado)


def _cor_da_borda(seletor, propriedade="border"):
    return re.search(r"#[0-9a-fA-F]{3,6}\b", valor(seletor, propriedade)).group()


# ===========================================================================
# 1. Uma paleta, num lugar
# ===========================================================================

def test_toda_variavel_usada_esta_definida():
    usadas = set(re.findall(r"var\((--[a-z0-9-]+)\)", SEM_COMENTARIO))
    assert usadas and not usadas - set(TOKENS), sorted(usadas - set(TOKENS))


def test_os_tokens_sao_cores_validas_ou_outros_tokens():
    for nome, bruto in TOKENS.items():
        _hex6(resolver(bruto))


def test_nenhuma_cor_da_paleta_antiga_sobrou():
    regras = SEM_COMENTARIO.replace(RAIZ_CSS, "")
    sobras = {c.lower() for c in re.findall(r"#[0-9a-fA-F]{6}\b|#[0-9a-fA-F]{3}\b", regras)}
    assert not sobras & PALETA_ANTIGA, sorted(sobras & PALETA_ANTIGA)


def test_a_paleta_vem_do_logo():
    """As três cores medidas no arquivo do logo em 22/11."""
    assert TOKENS["--oliva-do-logo"].strip() == "#5e5835"
    assert TOKENS["--creme"].strip() == "#f6e8d7"
    assert TOKENS["--terracota"].strip() == "#c1723c"


def test_barra_e_botoes_tem_o_mesmo_tom_mais_escuro_que_o_logo():
    """22/11, pedido do Dimas: o mesmo tom na barra e nos botões, mais
    fechado que o oliva do logo. 23/11: o tom passou a ser a imbuia,
    marrom de madeira escolhido por ele numa prévia de quatro."""
    barra = valor(".barra", "background")
    assert barra == "#4a3222"
    assert barra == valor("button", "background") == valor("a.botao", "background")
    assert contraste(barra, BRANCO) > contraste(resolver("var(--oliva-do-logo)"), BRANCO)
    # O oliva do logo segue no primeiro tom dos chalés.
    assert resolver("var(--chale-1)") == resolver("var(--oliva-do-logo)")


def test_mouse_e_clique_continuam_visiveis():
    """Com barra e botão no mesmo tom, o clique não pode herdar o tom da
    barra: o botão pareceria não reagir."""
    base = valor("button", "background")
    em_cima = valor("button:not(.secundario):not(.sair):hover", "background")
    clique = valor("button:not(.secundario):not(.sair):active", "background")
    assert len({base, em_cima, clique}) == 3


def test_os_doze_tons_dos_chales_sao_diferentes():
    tons = [resolver(TOKENS[f"--chale-{n}"]) for n in range(1, 13)]
    assert len(set(tons)) == 12


# ===========================================================================
# 2 e 3. Contraste dos pares que a folha usa de fato
# ===========================================================================

PARES_DE_TEXTO = [
    # (o que é, cor do texto, cor do fundo)
    ("texto da página", lambda: valor("body", "color"), lambda: valor("body", "background")),
    ("barra: links do menu", lambda: valor(".barra nav a", "color"), lambda: valor(".barra", "background")),
    ("barra: texto", lambda: valor(".barra", "color"), lambda: valor(".barra", "background")),
    ("botão Sair", lambda: valor(".barra .sair", "color"), lambda: valor(".barra .sair", "background")),
    ("botão Entrar", lambda: valor(".cabecalho-publico .entrar", "color"),
     lambda: valor(".cabecalho-publico .entrar", "background")),
    ("botão principal", lambda: valor("button", "color"), lambda: valor("button", "background")),
    ("botão principal, mouse em cima", lambda: valor("button", "color"),
     lambda: valor("button:not(.secundario):not(.sair):hover", "background")),
    ("botão principal, clique", lambda: valor("button", "color"),
     lambda: valor("button:not(.secundario):not(.sair):active", "background")),
    ("botão-link", lambda: valor("a.botao", "color"), lambda: valor("a.botao", "background")),
    ("botão secundário", lambda: valor("button.secundario", "color"),
     lambda: valor("button.secundario", "background")),
    ("botão secundário, mouse em cima", lambda: valor("button.secundario", "color"),
     lambda: valor("button.secundario:hover", "background")),
    ("link de texto", lambda: valor("main a:not([class])", "color"), lambda: valor("body", "background")),
    ("link visitado", lambda: valor("main a:not([class]):visited", "color"),
     lambda: valor("body", "background")),
    ("nota de campo", lambda: valor(".nota-campo", "color"), lambda: valor("body", "background")),
    ("texto suave sobre branco", lambda: resolver("var(--texto-suave)"), lambda: BRANCO),
    ("texto de apoio esverdeado", lambda: resolver("var(--marca-suave)"), lambda: valor("body", "background")),
    ("selo", lambda: valor("body", "color"), lambda: valor(".selo", "background")),
] + [
    (f"nome do chalé no tom {n}", (lambda: BRANCO), (lambda n=n: resolver(f"var(--chale-{n})")))
    for n in range(1, 13)
]


@pytest.mark.parametrize("nome,texto,fundo", PARES_DE_TEXTO, ids=[p[0] for p in PARES_DE_TEXTO])
def test_texto_passa_45_para_1(nome, texto, fundo):
    razao = contraste(texto(), fundo())
    assert razao >= 4.5, f"{nome}: {texto()} sobre {fundo()} = {razao:.2f}"


@pytest.mark.parametrize("fundo", ["body", "button.secundario"])
def test_o_contorno_de_foco_passa_3_para_1(fundo):
    """WCAG 2.4.13: o indicador de foco precisa se destacar do que está ao
    redor. O dourado antigo (#c99a2e) tinha 2,47:1 sobre o fundo."""
    foco = _cor_da_borda("button:focus-visible", "outline")
    assert contraste(foco, valor(fundo, "background")) >= 3
    assert contraste("#c99a2e", "#fafafa") < 3  # o motivo da troca


@pytest.mark.parametrize("barra", [".barra", ".cabecalho-publico"])
def test_o_contorno_de_foco_na_barra_passa_3_para_1(barra):
    """23/11: o terracota do foco tinha 2,0:1 sobre a barra escura (e
    1,6:1 sobre a imbuia). Na barra, o contorno é creme."""
    foco = _cor_da_borda(f"{barra} :focus-visible", "outline")
    assert contraste(foco, valor(barra, "background")) >= 3


def test_a_borda_dos_campos_passa_3_para_1():
    """WCAG 1.4.11: a borda é o que mostra onde o campo está. A antiga
    (#bbb) tinha 1,9:1 sobre o branco."""
    borda = _cor_da_borda('input[type="text"]')
    assert contraste(borda, BRANCO) >= 3
    assert contraste(borda, valor("body", "background")) >= 3


# ===========================================================================
# 4. As cores de significado não mudaram
# ===========================================================================

def test_o_vermelho_de_perigo_nao_virou_marca():
    """Cor de estado não é cor de marca: o terracota é parecido demais com
    vermelho para servir de "cancelar"."""
    assert valor("button.perigo", "color") == "#8a2b22"
    assert "var(" not in re.search(r"button\.perigo\s*\{([^}]*)\}", NORMAL).group(1)
