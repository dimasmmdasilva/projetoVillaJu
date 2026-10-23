"""Chaves criptográficas e modelo de configuração.

Dois riscos são verificados aqui. O primeiro é o .env.example carregar
sem querer um valor real - ele é versionado e público. O segundo é uma
chave nascer fraca: curta, previsível, igual a outra ou deixada no
placeholder, o que derrubaria toda a cifragem dos dados do hóspede.

Nenhum teste imprime conteúdo de chave, nem em mensagem de falha.
"""

import base64
import pathlib
import subprocess
import sys

import pytest

RAIZ = pathlib.Path(__file__).resolve().parents[1]
EXEMPLO = RAIZ / ".env.example"
ENV = RAIZ / ".env"
GERADOR = RAIZ / "scripts" / "gerar_chaves.py"

CHAVES = ["VILLAJU_CHAVE_CIFRAGEM", "VILLAJU_CHAVE_INDICE", "VILLAJU_SECRET_KEY"]

OBRIGATORIAS = CHAVES + [
    "VILLAJU_DB_HOST",
    "VILLAJU_DB_PORTA",
    "VILLAJU_DB_NOME",
    "VILLAJU_DB_USUARIO",
    "VILLAJU_DB_SENHA",
    "VILLAJU_TLS_CERT",
    "VILLAJU_TLS_CHAVE",
]

TAMANHO_BYTES = 32


def _ler(caminho):
    valores = {}
    for linha in caminho.read_text(encoding="utf-8").splitlines():
        linha = linha.strip()
        if not linha or linha.startswith("#") or "=" not in linha:
            continue
        nome, _, valor = linha.partition("=")
        valores[nome.strip()] = valor.strip()
    return valores


def _parece_chave_real(valor: str) -> bool:
    """Valor que decodifica em exatamente 32 bytes é uma chave de verdade."""
    try:
        return len(base64.b64decode(valor, validate=True)) == TAMANHO_BYTES
    except Exception:
        return False


# --- O modelo versionado ---------------------------------------------------

def test_env_example_existe():
    assert EXEMPLO.is_file(), ".env.example é obrigatório: documenta o ambiente"


@pytest.mark.parametrize("variavel", OBRIGATORIAS)
def test_env_example_declara_variavel(variavel):
    assert variavel in _ler(EXEMPLO), (
        f"{variavel} nao esta no .env.example; quem clonar o repositorio "
        f"nao vai saber que precisa defini-la"
    )


@pytest.mark.parametrize("variavel", CHAVES)
def test_env_example_nao_contem_chave_real(variavel):
    """O .env.example é público. Chave real nele é vazamento."""
    valor = _ler(EXEMPLO)[variavel]
    assert not _parece_chave_real(valor), (
        f"{variavel} no .env.example parece uma chave real de "
        f"{TAMANHO_BYTES} bytes. Substitua por um placeholder."
    )


def test_env_example_esta_versionado():
    r = subprocess.run(
        ["git", "ls-files", "--error-unmatch", ".env.example"],
        cwd=RAIZ, capture_output=True, text=True,
    )
    assert r.returncode == 0, ".env.example precisa estar no repositório"


# --- O gerador de chaves ---------------------------------------------------

def _gerar():
    r = subprocess.run(
        [sys.executable, str(GERADOR)], cwd=RAIZ, capture_output=True, text=True
    )
    assert r.returncode == 0, f"gerar_chaves.py falhou: {r.stderr}"
    return _ler_saida(r.stdout)


def _ler_saida(texto):
    valores = {}
    for linha in texto.splitlines():
        linha = linha.strip()
        if linha.startswith("VILLAJU_") and "=" in linha:
            nome, _, valor = linha.partition("=")
            valores[nome] = valor
    return valores


def test_gerador_produz_as_tres_chaves():
    assert sorted(_gerar()) == sorted(CHAVES)


@pytest.mark.parametrize("variavel", CHAVES)
def test_chave_gerada_tem_256_bits(variavel):
    valor = _gerar()[variavel]
    assert len(base64.b64decode(valor, validate=True)) == TAMANHO_BYTES


def test_chaves_da_mesma_execucao_sao_distintas():
    """Uma chave por finalidade. Reuso impede rotação independente."""
    gerado = _gerar()
    assert len(set(gerado.values())) == len(CHAVES)


def test_execucoes_diferentes_produzem_chaves_diferentes():
    """Detecta semente fixa ou uso de random no lugar de secrets."""
    assert set(_gerar().values()).isdisjoint(set(_gerar().values()))


def test_gerador_nao_usa_modulo_random():
    """random é pseudoaleatório previsível: impróprio para uso criptográfico."""
    codigo = GERADOR.read_text(encoding="utf-8")
    assert "import random" not in codigo
    assert "random." not in codigo
    assert "secrets.token_bytes" in codigo


def test_gerador_nao_grava_arquivo():
    """Ele imprime para colar. Gravar sozinho arriscaria sobrescrever um .env."""
    codigo = GERADOR.read_text(encoding="utf-8")
    for proibido in ("open(", "write_text", ".write(", "Path("):
        assert proibido not in codigo, f"o gerador nao deve gravar arquivo ({proibido})"


# --- O .env local do integrante --------------------------------------------

sem_env = pytest.mark.skipif(not ENV.is_file(), reason=".env local ausente")


@sem_env
def test_env_local_esta_ignorado_pelo_git():
    r = subprocess.run(
        ["git", "check-ignore", "-q", ".env"], cwd=RAIZ, capture_output=True
    )
    assert r.returncode == 0, "o .env NÃO está sendo ignorado: risco de vazamento"


@sem_env
@pytest.mark.parametrize("variavel", OBRIGATORIAS)
def test_env_local_define_variavel(variavel):
    assert variavel in _ler(ENV), f"falta {variavel} no seu .env"


@sem_env
@pytest.mark.parametrize("variavel", CHAVES)
def test_env_local_tem_chave_valida(variavel):
    valor = _ler(ENV)[variavel]
    assert "GERE_COM" not in valor, (
        f"{variavel} ainda esta com o placeholder. "
        f"Rode: python scripts/gerar_chaves.py"
    )
    assert _parece_chave_real(valor), (
        f"{variavel} nao decodifica em {TAMANHO_BYTES} bytes de base64"
    )


@sem_env
def test_env_local_tem_tres_chaves_distintas():
    valores = _ler(ENV)
    assert len({valores[c] for c in CHAVES}) == len(CHAVES)
