"""Validacao do ambiente de desenvolvimento.

Com cinco integrantes em maquinas distintas, a divergencia de versao e o
risco registrado na Etapa 0. Estes testes falham cedo e com mensagem
clara quando um ambiente nao confere, em vez de produzir erro obscuro
mais adiante.

Nao cobrem regra de negocio nem criptografia a fundo - isso e papel de
test_cripto.py e test_senhas.py, na Etapa 2.
"""

import importlib
import sys

import pytest

VERSAO_MINIMA = (3, 11)

DEPENDENCIAS = [
    ("flask", "Flask"),
    ("flask_wtf", "Flask-WTF"),
    ("flask_limiter", "Flask-Limiter"),
    ("mysql.connector", "mysql-connector-python"),
    ("cryptography", "cryptography"),
    ("argon2", "argon2-cffi"),
    ("dotenv", "python-dotenv"),
]


def test_versao_do_python():
    assert sys.version_info >= VERSAO_MINIMA, (
        f"Python {'.'.join(map(str, VERSAO_MINIMA))} ou superior e exigido; "
        f"este ambiente usa {sys.version.split()[0]}"
    )


@pytest.mark.parametrize("modulo,pacote", DEPENDENCIAS)
def test_dependencia_instalada(modulo, pacote):
    try:
        importlib.import_module(modulo)
    except ImportError:
        pytest.fail(
            f"'{pacote}' nao esta instalado. "
            f"Rode: pip install -r requirements.txt"
        )


def test_aes_gcm_disponivel():
    """A cifragem dos dados do hospede depende deste algoritmo (secao 6.2)."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    assert AESGCM.generate_key(bit_length=256)


def test_argon2id_e_o_tipo_padrao():
    """Argon2i e Argon2d tambem existem; o projeto exige a variante id."""
    from argon2 import PasswordHasher
    from argon2.low_level import Type

    assert PasswordHasher().type is Type.ID


def _pinos_do_requirements():
    """Le requirements.txt e devolve [(pacote, versao_exigida), ...]."""
    import pathlib
    import re

    arq = pathlib.Path(__file__).resolve().parents[1] / "requirements.txt"
    pinos = []
    for linha in arq.read_text(encoding="utf-8").splitlines():
        linha = linha.split("#")[0].strip()
        if not linha:
            continue
        m = re.fullmatch(r"([A-Za-z0-9_.\-]+)==([0-9][\w.\-]*)", linha)
        assert m, f"linha sem versao fixada em requirements.txt: {linha!r}"
        pinos.append((m.group(1), m.group(2)))
    return pinos


def test_requirements_tem_tudo_fixado():
    """Nenhuma dependencia pode ficar sem == : e o que garante maquinas iguais."""
    assert len(_pinos_do_requirements()) >= 10


@pytest.mark.parametrize("pacote,exigida", _pinos_do_requirements())
def test_versao_instalada_bate_com_requirements(pacote, exigida):
    """Detecta a divergencia de versao entre maquinas, risco da Etapa 0."""
    from importlib.metadata import PackageNotFoundError, version

    try:
        instalada = version(pacote)
    except PackageNotFoundError:
        pytest.fail(f"'{pacote}' nao esta instalado neste ambiente")

    assert instalada == exigida, (
        f"'{pacote}': o ambiente tem {instalada}, o requirements.txt exige "
        f"{exigida}. Rode: pip install -r requirements.txt"
    )
