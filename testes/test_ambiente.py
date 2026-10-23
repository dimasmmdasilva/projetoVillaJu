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
