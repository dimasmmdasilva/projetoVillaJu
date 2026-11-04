"""Certificado TLS local e aplicação mínima.

O TLS é obrigatório em qualquer ambiente, inclusive local (seção 6.5).
Estes testes verificam que o certificado existe, que o par chave/certificado
confere, que ele não está vencido e que a aplicação recusa subir sem
configuração em vez de cair num padrão inseguro.

Um certificado vencido não avisa: ele simplesmente deixa a aplicação de
subir. Melhor descobrir pelo pytest do que na hora da apresentação.
"""

import datetime as dt
import importlib.util
import os
import pathlib
import ssl
import subprocess
import sys

import pytest

RAIZ = pathlib.Path(__file__).resolve().parents[1]
CERT = RAIZ / "certs" / "villaju.crt"
CHAVE = RAIZ / "certs" / "villaju.key"
RUN = RAIZ / "run.py"

sem_cert = pytest.mark.skipif(
    not CERT.is_file() or not CHAVE.is_file(),
    reason="certificado local ausente; gere com openssl (seção 6.5)",
)


def _carregar_run():
    spec = importlib.util.spec_from_file_location("run_villaju", RUN)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def _certificado():
    from cryptography import x509

    return x509.load_pem_x509_certificate(CERT.read_bytes())


# --- O certificado ---------------------------------------------------------

@sem_cert
def test_certificado_e_chave_formam_par():
    """Certificado de um par e chave de outro sobem, mas não negociam."""
    contexto = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    contexto.load_cert_chain(certfile=CERT, keyfile=CHAVE)


@sem_cert
def test_certificado_nao_esta_vencido():
    cert = _certificado()
    agora = dt.datetime.now(dt.timezone.utc)
    assert cert.not_valid_before_utc <= agora, "certificado ainda não é válido"
    assert cert.not_valid_after_utc > agora, (
        f"certificado vencido em {cert.not_valid_after_utc:%d/%m/%Y}. "
        f"Gere outro com o comando da secao 6.5."
    )


@sem_cert
def test_certificado_nao_vence_durante_o_projeto():
    """A entrega final é em 25/11/2026 e a defesa logo depois."""
    restam = (_certificado().not_valid_after_utc - dt.datetime.now(dt.timezone.utc)).days
    assert restam > 60, f"o certificado vence em {restam} dias; gere um novo"


@sem_cert
def test_chave_tem_pelo_menos_2048_bits():
    from cryptography.hazmat.primitives.serialization import load_pem_private_key

    chave = load_pem_private_key(CHAVE.read_bytes(), password=None)
    assert chave.key_size >= 2048, f"chave de {chave.key_size} bits e fraca"


@sem_cert
def test_certificado_cobre_localhost():
    """Sem subjectAltName o navegador recusa, mesmo com o CN correto."""
    from cryptography import x509

    san = _certificado().extensions.get_extension_for_class(
        x509.SubjectAlternativeName
    ).value
    nomes = set(san.get_values_for_type(x509.DNSName))
    ips = {str(i) for i in san.get_values_for_type(x509.IPAddress)}
    assert "localhost" in nomes
    assert "127.0.0.1" in ips


@sem_cert
def test_certificado_nao_esta_versionado():
    r = subprocess.run(
        ["git", "ls-files", "certs/"], cwd=RAIZ, capture_output=True, text=True
    )
    assert not r.stdout.strip(), (
        f"ha arquivos de certs/ versionados: {r.stdout.split()}. "
        f"Chave privada no repositorio e vazamento."
    )


# --- A aplicação -----------------------------------------------------------

def test_contexto_tls_recusa_versoes_obsoletas():
    """TLS 1.0 e 1.1 estão obsoletos e têm fraquezas conhecidas."""
    contexto = _carregar_run().criar_contexto_tls(CERT, CHAVE)
    assert contexto.minimum_version >= ssl.TLSVersion.TLSv1_2


def test_chave_de_sessao_nao_esta_embutida_no_codigo():
    """Nenhum segredo no código-fonte (regra 2 do projeto).

    A chave de sessão é lida de variável de ambiente pelo app/config.py.
    Este teste varre os dois arquivos que poderiam embuti-la.
    """
    configuracao = (RAIZ / "app" / "config.py").read_text(encoding="utf-8")
    entrada = RUN.read_text(encoding="utf-8")

    assert 'os.environ.get("VILLAJU_SECRET_KEY")' in configuracao or            '_exigir("VILLAJU_SECRET_KEY")' in configuracao

    for fonte, nome in ((configuracao, "app/config.py"), (entrada, "run.py")):
        sem_espacos = fonte.replace(" ", "")
        assert 'SECRET_KEY="' not in sem_espacos, nome
        assert "SECRET_KEY='" not in sem_espacos, nome


def test_recusa_subir_sem_chave_de_sessao():
    """Sem configuração, parar é mais seguro que assumir um padrão."""
    r = subprocess.run(
        [sys.executable, "-c",
         "import os;os.environ.pop('VILLAJU_SECRET_KEY',None);"
         "import importlib.util;"
         f"spec=importlib.util.spec_from_file_location('r',r'{RUN}');"
         "m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);"
         "m.os.environ.pop('VILLAJU_SECRET_KEY',None) if hasattr(m,'os') else None;"
         "import os as o;o.environ.pop('VILLAJU_SECRET_KEY',None);"
         "m.conferir_ambiente()"],
        cwd=RAIZ, capture_output=True, text=True,
    )
    assert r.returncode != 0, "a aplicação subiu sem VILLAJU_SECRET_KEY"
    assert "VILLAJU_SECRET_KEY" in r.stderr
