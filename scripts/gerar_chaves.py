#!/usr/bin/env python3
"""Gera as tres chaves criptograficas do projeto VillaJu.

Cada integrante roda este script uma vez e cola a saida no seu proprio
.env local. As chaves NAO sao iguais entre as maquinas e NAO vao para o
repositorio - o .env esta no .gitignore desde o primeiro commit.

    python scripts/gerar_chaves.py

Por que tres chaves distintas e nao uma so: usar a mesma chave para dois
fins criptograficos diferentes e ma pratica e impede rotacao independente.
Se a chave de indice precisar ser trocada, a de cifragem nao deveria ser
afetada - e vice-versa.

AVISO: perder VILLAJU_CHAVE_CIFRAGEM torna os dados pessoais ja gravados
irrecuperaveis. Nao existe recuperacao. Guarde copia fora do repositorio,
em local combinado pelo grupo.
"""

import base64
import secrets
import sys

# 32 bytes = 256 bits. Exigido pelo AES-256 e tamanho de bloco do SHA-256,
# que e o ideal para a chave do HMAC.
TAMANHO_BYTES = 32

VARIAVEIS = [
    (
        "VILLAJU_CHAVE_CIFRAGEM",
        "AES-256-GCM dos dados pessoais do hospede (secao 6.2)",
    ),
    (
        "VILLAJU_CHAVE_INDICE",
        "HMAC-SHA256 dos campos _busca, que permitem consulta (secao 5.1)",
    ),
    (
        "VILLAJU_SECRET_KEY",
        "assinatura do cookie de sessao do Flask",
    ),
]


def gerar_chave() -> str:
    """Devolve 32 bytes aleatorios em base64.

    Usa secrets, nao random: o modulo random e um gerador
    pseudoaleatorio previsivel, improprio para uso criptografico.
    """
    return base64.b64encode(secrets.token_bytes(TAMANHO_BYTES)).decode("ascii")


def main() -> int:
    chaves = {nome: gerar_chave() for nome, _ in VARIAVEIS}

    if len(set(chaves.values())) != len(chaves):
        print("ERRO: chaves repetidas. Rode novamente.", file=sys.stderr)
        return 1

    print()
    print("  Cole as linhas abaixo no seu arquivo .env (na raiz do projeto).")
    print("  Nao compartilhe e nao versione.")
    print()
    print("  " + "-" * 68)
    for nome, finalidade in VARIAVEIS:
        print(f"  # {finalidade}")
        print(f"  {nome}={chaves[nome]}")
        print()
    print("  " + "-" * 68)
    print()
    print(f"  {TAMANHO_BYTES} bytes por chave, geradas com secrets.token_bytes.")
    print("  Perder a chave de cifragem torna os dados do hospede irrecuperaveis.")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
