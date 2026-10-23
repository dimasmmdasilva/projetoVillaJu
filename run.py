#!/usr/bin/env python3
"""Ponto de entrada da aplicacao VillaJu.

Nesta etapa a aplicacao e minima de proposito: sobe o Flask com TLS e
responde em /, apenas para provar que o ambiente funciona de ponta a
ponta nas cinco maquinas do grupo.

A fabrica da aplicacao (create_app), as rotas e o nucleo de seguranca
chegam na Etapa 2. Este arquivo passa a apenas chamar a fabrica.

    python run.py

O TLS e obrigatorio em qualquer ambiente, inclusive local (secao 6.5).
O certificado e autoassinado, entao o navegador vai alertar - isso e
esperado. O alerta diz que ninguem atesta a identidade do servidor, nao
que a conexao esta aberta: o trafego e cifrado do mesmo jeito.
"""

import pathlib
import ssl
import sys

from dotenv import load_dotenv
from flask import Flask

RAIZ = pathlib.Path(__file__).resolve().parent

load_dotenv(RAIZ / ".env")

import os  # noqa: E402  (apos load_dotenv, para ler o que ele carregou)

OBRIGATORIAS = [
    "VILLAJU_SECRET_KEY",
    "VILLAJU_TLS_CERT",
    "VILLAJU_TLS_CHAVE",
]


def conferir_ambiente() -> dict:
    """Falha ruidosamente se faltar configuracao.

    Preferimos parar aqui a subir com um padrao inseguro embutido. Uma
    chave de sessao ausente que vira valor fixo no codigo e exatamente o
    tipo de falha que passa despercebida ate virar incidente.
    """
    faltando = [v for v in OBRIGATORIAS if not os.environ.get(v)]
    if faltando:
        print("ERRO: variaveis ausentes no .env:", file=sys.stderr)
        for v in faltando:
            print(f"  - {v}", file=sys.stderr)
        print("\nCrie o .env a partir do .env.example e rode:", file=sys.stderr)
        print("  python scripts/gerar_chaves.py", file=sys.stderr)
        raise SystemExit(1)

    cert = RAIZ / os.environ["VILLAJU_TLS_CERT"]
    chave = RAIZ / os.environ["VILLAJU_TLS_CHAVE"]
    for caminho, rotulo in ((cert, "certificado"), (chave, "chave privada")):
        if not caminho.is_file():
            print(f"ERRO: {rotulo} nao encontrado em {caminho}", file=sys.stderr)
            print("\nGere o certificado local:", file=sys.stderr)
            print("  openssl req -x509 -newkey rsa:4096 -nodes \\", file=sys.stderr)
            print("    -keyout certs/villaju.key -out certs/villaju.crt \\", file=sys.stderr)
            print('    -days 365 -subj "/CN=localhost"', file=sys.stderr)
            raise SystemExit(1)

    return {"cert": cert, "chave": chave}


def criar_contexto_tls(cert: pathlib.Path, chave: pathlib.Path) -> ssl.SSLContext:
    """Contexto TLS restrito a TLS 1.2 e 1.3.

    Versoes anteriores (SSLv3, TLS 1.0 e 1.1) estao obsoletas e carregam
    fraquezas conhecidas. Restringir aqui ja atende a verificacao do
    testssl.sh prevista para a Etapa 6.
    """
    contexto = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    contexto.minimum_version = ssl.TLSVersion.TLSv1_2
    contexto.load_cert_chain(certfile=cert, keyfile=chave)
    return contexto


def criar_app() -> Flask:
    app = Flask(__name__)
    app.config["SECRET_KEY"] = os.environ["VILLAJU_SECRET_KEY"]

    # Cookie de sessao endurecido desde o inicio (secao 6.4).
    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,   # fora do alcance de JavaScript
        SESSION_COOKIE_SECURE=True,     # so trafega sobre HTTPS
        SESSION_COOKIE_SAMESITE="Lax",  # reduz exposicao a CSRF
    )

    @app.route("/")
    def inicio():
        return "ok", 200, {"Content-Type": "text/plain; charset=utf-8"}

    return app


def main() -> int:
    caminhos = conferir_ambiente()
    app = criar_app()

    host = os.environ.get("VILLAJU_HOST", "127.0.0.1")
    porta = int(os.environ.get("VILLAJU_PORTA", "5000"))

    print(f"  VillaJu em https://{host}:{porta}")
    print("  O aviso de certificado no navegador e esperado.")

    app.run(
        host=host,
        port=porta,
        ssl_context=criar_contexto_tls(caminhos["cert"], caminhos["chave"]),
        debug=os.environ.get("VILLAJU_DEBUG") == "1",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
