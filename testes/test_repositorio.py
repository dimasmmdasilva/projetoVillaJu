"""Higiene do repositório: estrutura e proteção contra vazamento de segredo.

O .gitignore é a primeira barreira do projeto, e hoje nada impediria um
integrante de quebra-lo sem perceber - bastaria uma edicao distraída para
que o .env ou um certificado passassem a ser versionados. Segredo que
entra no histórico do Git não sai sem reescrever o histórico, então a
falha precisa ser detectada antes do commit, não depois.

Estes testes transformam essa barreira em verificação automática.
"""

import pathlib
import shutil
import subprocess

import pytest

RAIZ = pathlib.Path(__file__).resolve().parents[1]

DIRETORIOS = [
    "app/seguranca",
    "app/dados",
    "app/rotas",
    "app/templates/publico",
    "app/templates/painel",
    "app/static/css",
    "app/static/js",
    "app/static/img/chales",
    "banco",
    "testes",
    "docs",
    "scripts",
]

PACOTES = ["app", "app/seguranca", "app/dados", "app/rotas"]

# Caminhos que jamais podem ser versionados (seção 8.1 do plano).
SENSIVEIS = [
    ".env",
    ".env.local",
    ".env.produção",
    "certs/villaju.key",
    "certs/villaju.crt",
    "chave_privada.pem",
    "backups/villaju.sql.gz",
    "dump.sql.gz",
    "docs/contexto.md",
    "venv/Lib/site-packages/x.py",
    "app/__pycache__/cripto.cpython-312.pyc",
]

# Caminhos que precisam continuar versionáveis.
VERSIONAVEIS = [
    ".env.example",
    "README.md",
    "requirements.txt",
    "banco/01_schema.sql",
    "app/seguranca/cripto.py",
    "scripts/backup.sh",
    "docs/instalacao.md",
]

# Extensoes que denunciam segredo ou dado pessoal no repositório.
EXTENSOES_PROIBIDAS = (".key", ".crt", ".pem", ".pfx", ".p12", ".sql.gz", ".pyc")


def _git(*args):
    return subprocess.run(
        ["git", *args], cwd=RAIZ, capture_output=True, text=True
    )


pytestmark = pytest.mark.skipif(
    shutil.which("git") is None or not (RAIZ / ".git").exists(),
    reason="fora de um repositório Git",
)


@pytest.mark.parametrize("caminho", DIRETORIOS)
def test_diretorio_existe(caminho):
    assert (RAIZ / caminho).is_dir(), f"diretorio ausente: {caminho}"


@pytest.mark.parametrize("pacote", PACOTES)
def test_pacote_tem_init(pacote):
    assert (RAIZ / pacote / "__init__.py").is_file()


@pytest.mark.parametrize("caminho", SENSIVEIS)
def test_gitignore_bloqueia_arquivo_sensivel(caminho):
    """O caminho não precisa existir: check-ignore avalia só a regra."""
    r = _git("check-ignore", "--no-index", "-q", caminho)
    assert r.returncode == 0, (
        f"{caminho!r} NAO esta coberto pelo .gitignore e poderia ser commitado"
    )


@pytest.mark.parametrize("caminho", VERSIONAVEIS)
def test_gitignore_nao_bloqueia_arquivo_legitimo(caminho):
    r = _git("check-ignore", "--no-index", "-q", caminho)
    assert r.returncode != 0, (
        f"{caminho!r} esta sendo ignorado, mas precisa ser versionado"
    )


def test_nenhum_arquivo_sensivel_esta_rastreado():
    """Varre o que o Git realmente rastreia hoje, não apenas as regras."""
    rastreados = _git("ls-files").stdout.split()
    suspeitos = [
        c
        for c in rastreados
        if c.endswith(EXTENSOES_PROIBIDAS)
        or pathlib.PurePosixPath(c).name == ".env"
        or pathlib.PurePosixPath(c).name == "contexto.md"
    ]
    assert not suspeitos, f"arquivos sensiveis versionados: {suspeitos}"


def test_nenhum_segredo_no_historico_de_commits():
    """O arquivo pode ter sido removido depois e continuar no histórico."""
    nomes = set(_git("log", "--pretty=format:", "--name-only", "--all").stdout.split())
    suspeitos = [
        c
        for c in nomes
        if c.endswith(EXTENSOES_PROIBIDAS) or pathlib.PurePosixPath(c).name == ".env"
    ]
    assert not suspeitos, (
        f"arquivos sensiveis presentes no historico: {suspeitos}. "
        f"Remover do historico exige reescrita (git filter-repo)."
    )
