"""Impede que acento entre em código.

O projeto escreve comentários, docstrings e mensagens em português
correto, com acentuação. O código em si — nomes de variável, de função,
caminhos de arquivo e chaves usadas em comparação — permanece em ASCII.

A separação não é estética. Acento em identificador ou em caminho quebra
de formas difíceis de diagnosticar: o nome do parâmetro de um
`parametrize` deixa de casar com o argumento da função, um caminho deixa
de existir no disco, uma chave de dicionário nunca encontra o valor.
Isso já aconteceu uma vez neste repositório e estes testes existem para
que não aconteça de novo.
"""

import ast
import io
import pathlib
import re
import tokenize

import pytest

RAIZ = pathlib.Path(__file__).resolve().parents[1]

ARQUIVOS_PY = sorted(
    p for p in list(RAIZ.glob("testes/*.py"))
    + [RAIZ / "run.py"]
    + list((RAIZ / "scripts").rglob("*.py"))
    + list((RAIZ / "app").rglob("*.py"))
    if p.is_file()
)

CONFIGURACAO = [
    RAIZ / ".gitignore",
    RAIZ / ".gitattributes",
    RAIZ / ".env.example",
    RAIZ / "requirements.txt",
    RAIZ / "pytest.ini",
]

# Uma string sem espaço, com barra ou extensão conhecida, é caminho.
# Exigir ausência de espaço evita confundir texto corrido que contenha
# uma data (25/11/2026) ou uma barra entre palavras (chave/certificado).
EXTENSOES = "py|sql|md|txt|ini|cfg|crt|key|pem|sh|html|css|js|json|yml|example"
PARECE_CAMINHO = re.compile(rf"^[^\s]*(/[^\s]*|\.({EXTENSOES}))$")

# No Python 3.12 uma f-string vira FSTRING_START/MIDDLE/END, nunca STRING.
# Sem incluí-los, o texto dentro de f-strings seria tratado como código.
TOKENS_DE_TEXTO = {tokenize.COMMENT, tokenize.STRING} | {
    getattr(tokenize, nome)
    for nome in ("FSTRING_START", "FSTRING_MIDDLE", "FSTRING_END")
    if hasattr(tokenize, nome)
}


def _rotulo(caminho: pathlib.Path) -> str:
    return caminho.relative_to(RAIZ).as_posix()


def _tem_acento(texto: str) -> bool:
    return any(ord(c) > 127 for c in texto)


@pytest.mark.parametrize("arquivo", ARQUIVOS_PY, ids=_rotulo)
def test_identificadores_sao_ascii(arquivo):
    """Nenhum acento fora de comentário e string."""
    origem = arquivo.read_text(encoding="utf-8")
    culpados = [
        f"linha {tok.start[0]}: {tok.string!r}"
        for tok in tokenize.generate_tokens(io.StringIO(origem).readline)
        if tok.type not in TOKENS_DE_TEXTO and _tem_acento(tok.string)
    ]
    assert not culpados, (
        f"{_rotulo(arquivo)} tem acento em código, não em texto:\n  "
        + "\n  ".join(culpados)
    )


@pytest.mark.parametrize("arquivo", ARQUIVOS_PY, ids=_rotulo)
def test_caminhos_de_arquivo_sao_ascii(arquivo):
    """Acento em caminho aponta para arquivo que não existe no disco."""
    arvore = ast.parse(arquivo.read_text(encoding="utf-8"))
    culpados = [
        f"linha {no.lineno}: {no.value!r}"
        for no in ast.walk(arvore)
        if isinstance(no, ast.Constant)
        and isinstance(no.value, str)
        and _tem_acento(no.value)
        and PARECE_CAMINHO.match(no.value)
    ]
    assert not culpados, (
        f"{_rotulo(arquivo)} tem acento em caminho de arquivo:\n  "
        + "\n  ".join(culpados)
    )


@pytest.mark.parametrize("arquivo", ARQUIVOS_PY, ids=_rotulo)
def test_nomes_de_parametrize_sao_ascii(arquivo):
    """O nome em parametrize precisa casar com o argumento da função."""
    origem = arquivo.read_text(encoding="utf-8")
    culpados = [
        f"linha {n}: {linha.strip()}"
        for n, linha in enumerate(origem.splitlines(), start=1)
        if "parametrize(" in linha and _tem_acento(linha.split("parametrize(", 1)[1].split(",")[0])
    ]
    assert not culpados, (
        f"{_rotulo(arquivo)} acentuou o nome do parâmetro:\n  " + "\n  ".join(culpados)
    )


@pytest.mark.parametrize("arquivo", CONFIGURACAO, ids=_rotulo)
def test_configuracao_so_tem_acento_em_comentario(arquivo):
    """Em arquivo de configuração, acento só é aceitável depois do '#'."""
    culpados = []
    for n, linha in enumerate(arquivo.read_text(encoding="utf-8").splitlines(), start=1):
        antes_do_comentario = linha.split("#", 1)[0]
        if _tem_acento(antes_do_comentario):
            culpados.append(f"linha {n}: {linha.strip()}")
    assert not culpados, (
        f"{_rotulo(arquivo)} tem acento em regra ou valor, não em comentário:\n  "
        + "\n  ".join(culpados)
    )


def test_todo_arquivo_e_utf8():
    """Acento gravado em outra codificação vira caractere quebrado."""
    for arquivo in ARQUIVOS_PY + CONFIGURACAO:
        try:
            arquivo.read_text(encoding="utf-8")
        except UnicodeDecodeError as erro:
            pytest.fail(f"{_rotulo(arquivo)} não está em UTF-8: {erro}")
