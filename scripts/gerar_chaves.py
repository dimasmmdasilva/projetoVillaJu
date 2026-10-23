#!/usr/bin/env python3
"""Gera as três chaves criptográficas do projeto VillaJu.

Cada integrante roda este script uma vez e cola a saída no seu próprio
.env local. As chaves NÃO são iguais entre as máquinas e NÃO vão para o
repositório - o .env está no .gitignore desde o primeiro commit.

    python scripts/gerar_chaves.py

Por que três chaves distintas e não uma só: usar a mesma chave para dois
fins criptográficos diferentes é má prática e impede rotação independente.
Se a chave de índice precisar ser trocada, a de cifragem não deveria ser
afetada - e vice-versa.

AVISO: perder VILLAJU_CHAVE_CIFRAGEM torna os dados pessoais já gravados
irrecuperáveis. Não existe recuperação. Guarde cópia fora do repositório,
em local combinado pelo grupo.
"""

import base64
import secrets
import sys

# 32 bytes = 256 bits. Exigido pelo AES-256 e tamanho de bloco do SHA-256,
# que é o ideal para a chave do HMAC.
TAMANHO_BYTES = 32

VARIAVEIS = [
    (
        "VILLAJU_CHAVE_CIFRAGEM",
        "AES-256-GCM dos dados pessoais do hóspede (seção 6.2)",
    ),
    (
        "VILLAJU_CHAVE_INDICE",
        "HMAC-SHA256 dos campos _busca, que permitem consulta (seção 5.1)",
    ),
    (
        "VILLAJU_SECRET_KEY",
        "assinatura do cookie de sessão do Flask",
    ),
]


def gerar_chave() -> str:
    """Devolve 32 bytes aleatórios em base64.

    Usa secrets, não random: o módulo random é um gerador
    pseudoaleatório previsível, impróprio para uso criptográfico.
    """
    return base64.b64encode(secrets.token_bytes(TAMANHO_BYTES)).decode("ascii")


def main() -> int:
    chaves = {nome: gerar_chave() for nome, _ in VARIAVEIS}

    if len(set(chaves.values())) != len(chaves):
        print("ERRO: chaves repetidas. Rode novamente.", file=sys.stderr)
        return 1

    print()
    print("  Cole as linhas abaixo no seu arquivo .env (na raiz do projeto).")
    print("  Não compartilhe e não versione.")
    print()
    print("  " + "-" * 68)
    for nome, finalidade in VARIAVEIS:
        print(f"  # {finalidade}")
        print(f"  {nome}={chaves[nome]}")
        print()
    print("  " + "-" * 68)
    print()
    print(f"  {TAMANHO_BYTES} bytes por chave, geradas com secrets.token_bytes.")
    print("  Perder a chave de cifragem torna os dados do hóspede irrecuperáveis.")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
