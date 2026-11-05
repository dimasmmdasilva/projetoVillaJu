#!/usr/bin/env python3
"""Regera os hóspedes fictícios com cifragem de verdade.

O arquivo `banco/04_dados_ficticios.sql` grava bytes aleatórios nas
colunas cifradas, e avisa disso no cabeçalho. A razão é a ordem das
etapas: a massa de dados nasceu na Etapa 1, antes de existir o módulo de
criptografia, e gerar ciphertext verdadeiro exigiria a chave de cifragem —
que vive no .env de cada integrante e jamais entra num arquivo versionado.

Aqueles bytes servem ao que precisam servir: dar `hospede_id` válido às
reservas e permitir verificar que o log de auditoria não copia conteúdo
dessas colunas. Mas não decifram, e a tela de hóspedes os exibe como
"registro não legível".

Este script substitui cada um deles por um hóspede fictício de verdade,
cifrado com a chave do ambiente, usando o mesmo repositório que a
aplicação usa. Depois de rodá-lo, a lista fica inteira legível e a
demonstração fica coerente.

    python scripts/regerar_hospedes_ficticios.py            # banco villaju
    python scripts/regerar_hospedes_ficticios.py --teste    # banco de teste

AVISO: os dados abaixo são inventados. Nenhum corresponde a pessoa real, e
os documentos não são CPFs válidos — foram escolhidos com dígitos
repetidos justamente para que não coincidam com o de ninguém.
"""

import argparse
import pathlib
import sys

RAIZ = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(RAIZ / ".env")

from app.config import Configuracao  # noqa: E402
from app.dados import conexao, hospede_repo  # noqa: E402

HOSPEDES = [
    {
        "nome": "Mariana Alves Ferreira",
        "documento": "111.111.111-11",
        "telefone": "(11) 98888-1234",
        "email": "mariana.alves@exemplo.com",
    },
    {
        "nome": "Carlos Eduardo Pereira",
        "documento": "222.222.222-22",
        "telefone": "(12) 99777-5678",
        "email": "carlos.pereira@exemplo.com",
    },
    {
        "nome": "Fernanda Lima Rocha",
        "documento": "333.333.333-33",
        "telefone": "(12) 99666-4321",
        "email": "fernanda.lima@exemplo.com",
    },
    {
        "nome": "Roberto Nascimento",
        "documento": "444.444.444-44",
        "telefone": "(13) 98555-8765",
        "email": "roberto.nascimento@exemplo.com",
    },
    {
        # Sem telefone nem e-mail, para exercitar os campos opcionais.
        "nome": "Juliana Castro",
        "documento": "555.555.555-55",
        "telefone": None,
        "email": None,
    },
]


def regerar(usuario_id=None) -> int:
    """Reescreve os hóspedes existentes, na ordem do identificador.

    Usa `atualizar`, não `criar`: as reservas já apontam para esses
    identificadores por chave estrangeira, e recriá-los exigiria apagar as
    reservas antes.

    Se houver mais hóspedes no banco do que nesta lista, os excedentes
    ficam como estão — provavelmente foram cadastrados pela aplicação e já
    estão cifrados de verdade.
    """
    with conexao.obter_cursor() as cursor:
        cursor.execute("SELECT id FROM hospede ORDER BY id LIMIT %s", (len(HOSPEDES),))
        identificadores = [linha[0] for linha in cursor.fetchall()]

    if not identificadores:
        print("  nenhum hóspede no banco; rode banco/04_dados_ficticios.sql antes")
        return 0

    for identificador, dados in zip(identificadores, HOSPEDES):
        hospede_repo.atualizar(identificador, dados, usuario_id=usuario_id)
        print(f"  hóspede {identificador}: {dados['nome']}")

    return len(identificadores)


def main() -> int:
    analisador = argparse.ArgumentParser(description=__doc__)
    analisador.add_argument(
        "--teste",
        action="store_true",
        help="opera sobre o banco de testes em vez do banco real",
    )
    argumentos = analisador.parse_args()

    configuracao = Configuracao(modo_teste=argumentos.teste)
    conexao.iniciar_pool(configuracao)

    alvo = configuracao.banco["database"]
    print(f"\n  Regerando hóspedes fictícios no banco {alvo}\n")

    quantos = regerar()

    print(f"\n  {quantos} hóspede(s) regerado(s) com cifragem real.")
    print("  Confira na tela de hóspedes: a lista deve ficar toda legível.")
    print("  E no MySQL Workbench: nenhum campo pessoal deve ser legível lá.\n")

    conexao.encerrar_pool()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
