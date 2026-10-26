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

# Senhas de banco: geradas pelo mesmo script, com a mesma fonte de entropia.
SENHAS = ["VILLAJU_DB_SENHA", "VILLAJU_DB_SENHA_TESTE"]

SEGREDOS = CHAVES + SENHAS

OBRIGATORIAS = CHAVES + [
    "VILLAJU_DB_HOST",
    "VILLAJU_DB_PORTA",
    "VILLAJU_DB_NOME",
    "VILLAJU_DB_USUARIO",
    "VILLAJU_DB_SENHA",
    "VILLAJU_DB_NOME_TESTE",
    "VILLAJU_DB_USUARIO_TESTE",
    "VILLAJU_DB_SENHA_TESTE",
    "VILLAJU_TLS_CERT",
    "VILLAJU_TLS_CHAVE",
]

# Comprimento mínimo de uma senha vinda de secrets.token_urlsafe(24).
TAMANHO_SENHA_MINIMO = 30

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


def test_gerador_produz_todos_os_segredos():
    """Três chaves criptográficas e duas senhas de banco, nada a menos."""
    assert sorted(_gerar()) == sorted(SEGREDOS)


@pytest.mark.parametrize("variavel", CHAVES)
def test_chave_gerada_tem_256_bits(variavel):
    valor = _gerar()[variavel]
    assert len(base64.b64decode(valor, validate=True)) == TAMANHO_BYTES


def test_chaves_da_mesma_execucao_sao_distintas():
    """Uma chave por finalidade. Reuso impede rotação independente."""
    gerado = _gerar()
    valores = [gerado[nome] for nome in CHAVES]
    assert len(set(valores)) == len(CHAVES)


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


# --- Senhas de banco de dados ----------------------------------------------

def _parece_senha_real(valor: str) -> bool:
    """Senha gerada tem comprimento fixo e alfabeto de token_urlsafe."""
    import re

    return bool(
        len(valor) >= TAMANHO_SENHA_MINIMO
        and re.fullmatch(r"[A-Za-z0-9_-]+", valor)
        and "GERE_COM" not in valor
    )


@pytest.mark.parametrize("variavel", SENHAS)
def test_env_example_nao_contem_senha_real(variavel):
    """O .env.example é versionado e público. Senha real nele é vazamento."""
    valor = _ler(EXEMPLO)[variavel]
    assert not _parece_senha_real(valor), (
        f"{variavel} no .env.example parece uma senha real. "
        f"Substitua por um placeholder."
    )


def test_script_de_bancos_nao_contem_senha_real():
    """banco/00_bancos.sql é versionado e precisa de senha para executar.

    O risco é alguém colar a senha para rodar o script e commitar sem
    desfazer. Este teste barra exatamente esse caso.
    """
    import re

    arquivo = RAIZ / "banco" / "00_bancos.sql"
    if not arquivo.is_file():
        pytest.skip("banco/00_bancos.sql ainda não existe")

    texto = arquivo.read_text(encoding="utf-8")
    suspeitos = [
        achado
        for achado in re.findall(r"IDENTIFIED BY\s+'([^']*)'", texto, re.IGNORECASE)
        if _parece_senha_real(achado)
    ]
    assert not suspeitos, (
        "banco/00_bancos.sql contém o que parece ser uma senha real. "
        "Restaure o placeholder antes de commitar."
    )


def test_gerador_produz_as_duas_senhas():
    gerado = _gerar()
    assert all(nome in gerado for nome in SENHAS)


@pytest.mark.parametrize("variavel", SENHAS)
def test_senha_gerada_tem_entropia_suficiente(variavel):
    """24 bytes de secrets, muito além do alcance de força bruta."""
    valor = _gerar()[variavel]
    assert len(valor) >= TAMANHO_SENHA_MINIMO, (
        f"{variavel} tem apenas {len(valor)} caracteres"
    )


@pytest.mark.parametrize("variavel", SENHAS)
def test_senha_gerada_e_segura_para_sql_e_env(variavel):
    """Aspas ou cifrão quebrariam o CREATE USER ou o leitor do .env."""
    import re

    valor = _gerar()[variavel]
    assert re.fullmatch(r"[A-Za-z0-9_-]+", valor), (
        f"{variavel} contém caractere que exigiria escape: {valor!r}"
    )


def test_senhas_dos_dois_usuarios_sao_distintas():
    """O usuário de teste não pode compartilhar senha com o de produção."""
    gerado = _gerar()
    assert gerado["VILLAJU_DB_SENHA"] != gerado["VILLAJU_DB_SENHA_TESTE"]


def test_nenhum_segredo_se_repete_entre_chaves_e_senhas():
    gerado = _gerar()
    valores = [gerado[nome] for nome in SEGREDOS]
    assert len(set(valores)) == len(SEGREDOS)


@sem_env
@pytest.mark.parametrize("variavel", SENHAS)
def test_env_local_tem_senha_gerada(variavel):
    valor = _ler(ENV).get(variavel, "")
    assert "GERE_COM" not in valor, (
        f"{variavel} ainda está com o placeholder. "
        f"Rode: python scripts/gerar_chaves.py"
    )
    assert _parece_senha_real(valor), (
        f"{variavel} não parece uma senha gerada pelo script"
    )
