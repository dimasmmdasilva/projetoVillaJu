#!/usr/bin/env python3
"""Ponto de entrada da aplicação VillaJu.

Responsabilidades deste arquivo, e só estas três: carregar o .env, montar
o contexto TLS e subir o servidor. A montagem da aplicação mora em
`app/__init__.py`, e a configuração em `app/config.py`.

    python run.py

O TLS é obrigatório em qualquer ambiente, inclusive local (seção 6.5 do
plano). O certificado é autoassinado, então o navegador vai alertar - isso
é esperado. O alerta diz que ninguém atesta a identidade do servidor, não
que a conexão está aberta: o tráfego é cifrado do mesmo jeito.
"""

import pathlib
import ssl
import sys

from dotenv import load_dotenv

RAIZ = pathlib.Path(__file__).resolve().parent

# Carregado aqui, no ponto de entrada, e em nenhum outro lugar. Os módulos
# da aplicação leem apenas os.environ e não sabem que existe um arquivo.
load_dotenv(RAIZ / ".env")


def conferir_ambiente():
    """Para com mensagem útil se faltar configuração ou certificado.

    Preferimos parar aqui a subir com um padrão inseguro embutido. Uma
    chave de sessão ausente que virasse valor fixo no código é exatamente
    o tipo de falha que passa despercebida até virar incidente.
    """
    from app.config import Configuracao, conferir_ambiente as faltantes

    ausentes = faltantes()
    if ausentes:
        print("ERRO: variáveis ausentes no .env:", file=sys.stderr)
        for nome in ausentes:
            print(f"  - {nome}", file=sys.stderr)
        print("\nCrie o .env a partir do .env.example e rode:", file=sys.stderr)
        print("  python scripts/gerar_chaves.py", file=sys.stderr)
        raise SystemExit(1)

    configuracao = Configuracao()

    for caminho, rotulo in (
        (configuracao.TLS_CERT, "certificado"),
        (configuracao.TLS_CHAVE, "chave privada"),
    ):
        if not caminho.is_file():
            print(f"ERRO: {rotulo} não encontrado em {caminho}", file=sys.stderr)
            print("\nGere o certificado local:", file=sys.stderr)
            print("  openssl req -x509 -newkey rsa:4096 -nodes \\", file=sys.stderr)
            print("    -keyout certs/villaju.key -out certs/villaju.crt \\", file=sys.stderr)
            print('    -days 365 -subj "/CN=localhost" \\', file=sys.stderr)
            print('    -addext "subjectAltName=DNS:localhost,IP:127.0.0.1"',
                  file=sys.stderr)
            raise SystemExit(1)

    return configuracao


def criar_contexto_tls(cert: pathlib.Path, chave: pathlib.Path) -> ssl.SSLContext:
    """Contexto TLS restrito a TLS 1.2 e 1.3.

    Versões anteriores (SSLv3, TLS 1.0 e 1.1) estão obsoletas e carregam
    fraquezas conhecidas. Restringir aqui já atende à verificação do
    testssl.sh prevista para a Etapa 6.
    """
    contexto = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    contexto.minimum_version = ssl.TLSVersion.TLSv1_2
    contexto.load_cert_chain(certfile=cert, keyfile=chave)
    return contexto


def main() -> int:
    configuracao = conferir_ambiente()

    from app import create_app

    aplicacao = create_app(configuracao)

    print(f"  VillaJu em https://{configuracao.HOST}:{configuracao.PORTA}")
    print("  O aviso de certificado no navegador é esperado.")
    if configuracao.DEBUG:
        print("  ATENÇÃO: modo de depuração ligado. Desligue antes da")
        print("  varredura da Etapa 6 e da apresentação (VILLAJU_DEBUG=0).")

    aplicacao.run(
        host=configuracao.HOST,
        port=configuracao.PORTA,
        ssl_context=criar_contexto_tls(
            configuracao.TLS_CERT, configuracao.TLS_CHAVE
        ),
        debug=configuracao.DEBUG,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
