#!/usr/bin/env python3
"""Gera todos os segredos do projeto VillaJu.

Cada integrante roda este script uma vez e cola a saída no seu próprio
.env local. Os valores NÃO são iguais entre as máquinas e NÃO vão para o
repositório - o .env está no .gitignore desde o primeiro commit.

    python scripts/gerar_chaves.py

São gerados dois tipos de segredo, com a mesma fonte de entropia:

  Chaves criptográficas   32 bytes (256 bits), em base64
  Senhas de banco         24 bytes (192 bits), em base64 seguro para URL

Por que chaves distintas e não uma só: usar a mesma chave para dois fins
criptográficos diferentes é má prática e impede rotação independente. Se
a chave de índice precisar ser trocada, a de cifragem não deveria ser
afetada - e vice-versa. Pelo mesmo motivo, o usuário de aplicação e o de
teste têm senhas separadas.

Nenhum segredo é escolhido à mão. Senha pensada por pessoa tem padrão, e
padrão é o que o ataque por dicionário explora.

AVISO: perder VILLAJU_CHAVE_CIFRAGEM torna os dados pessoais já gravados
irrecuperáveis. Não existe recuperação. Guarde cópia fora do repositório,
em local combinado pelo grupo.
"""

import base64
import secrets
import sys

# 32 bytes = 256 bits. Exigido pelo AES-256 e tamanho de bloco do SHA-256,
# que é o ideal para a chave do HMAC.
TAMANHO_CHAVE_BYTES = 32

# 24 bytes = 192 bits, bem acima do que qualquer ataque por força bruta
# alcança. O limite prático não é a entropia, é o tamanho máximo que o
# MySQL aceita, e 32 caracteres ficam folgados dentro dele.
TAMANHO_SENHA_BYTES = 24

CHAVES = [
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

SENHAS = [
    (
        "VILLAJU_DB_SENHA",
        "usuário villaju_app - privilégio mínimo sobre o banco villaju",
    ),
    (
        "VILLAJU_DB_SENHA_TESTE",
        "usuário villaju_teste - restrito ao banco descartável de testes",
    ),
]


def gerar_chave() -> str:
    """Devolve 32 bytes aleatórios em base64.

    Usa secrets, não random: o módulo random é um gerador
    pseudoaleatório previsível, impróprio para uso criptográfico.
    """
    return base64.b64encode(secrets.token_bytes(TAMANHO_CHAVE_BYTES)).decode("ascii")


def gerar_senha() -> str:
    """Devolve uma senha aleatória de 192 bits.

    O alfabeto de token_urlsafe é A-Z, a-z, 0-9, hífen e sublinhado.
    Nenhum desses caracteres precisa de escape dentro de uma string SQL
    nem confunde o leitor de arquivos .env, o que evita toda uma classe
    de erro na hora de criar o usuário no MySQL.
    """
    return secrets.token_urlsafe(TAMANHO_SENHA_BYTES)


def _imprimir_grupo(titulo: str, itens: list, valores: dict) -> None:
    print(f"  {titulo}")
    print("  " + "-" * 68)
    for nome, finalidade in itens:
        print(f"  # {finalidade}")
        print(f"  {nome}={valores[nome]}")
        print()


def main() -> int:
    valores = {nome: gerar_chave() for nome, _ in CHAVES}
    valores.update({nome: gerar_senha() for nome, _ in SENHAS})

    if len(set(valores.values())) != len(valores):
        print("ERRO: valores repetidos. Rode novamente.", file=sys.stderr)
        return 1

    print()
    print("  Cole as linhas abaixo no seu arquivo .env (na raiz do projeto).")
    print("  Não compartilhe e não versione.")
    print()
    _imprimir_grupo("CHAVES CRIPTOGRÁFICAS", CHAVES, valores)
    _imprimir_grupo("SENHAS DE BANCO DE DADOS", SENHAS, valores)
    print("  " + "-" * 68)
    print()
    print(f"  Chaves: {TAMANHO_CHAVE_BYTES} bytes ({TAMANHO_CHAVE_BYTES * 8} bits).")
    print(f"  Senhas: {TAMANHO_SENHA_BYTES} bytes ({TAMANHO_SENHA_BYTES * 8} bits).")
    print("  Tudo gerado com secrets, a fonte de entropia do sistema.")
    print()
    print("  As senhas acima também precisam ser usadas ao criar os")
    print("  usuários no MySQL, em banco/00_bancos.sql e banco/03_usuario_app.sql.")
    print("  Perder a chave de cifragem torna os dados do hóspede irrecuperáveis.")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
