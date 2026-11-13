"""Quem vai se hospedar junto com o titular — Etapa 5, Bloco 15.

O titular é o hóspede que pediu a reserva e conta como uma das pessoas:
uma reserva de 4 pessoas tem 3 acompanhantes. Cada um tem nome e, se for
adulto, documento. Menor de idade pode vir sem documento.

## Cifrado, e gravado numa fase só

Nome e documento são cifrados com AES-256-GCM, como em `hospede` e
`contato`. A etiqueta (dado adicional autenticado) é
`acompanhante:<reserva>:<ordem>:<campo>`, conhecida antes do INSERT. Por
isso não há a segunda fase com UPDATE que os outros dois repositórios
fazem: a aplicação tem só SELECT e INSERT nesta tabela.

## Na transação do pedido

`gravar` recebe o cursor de quem chama, e não abre conexão. Quem chama é
`reserva_repo.criar_pedido`: ou entram a reserva e todos os
acompanhantes, ou nada. Um pedido gravado sem a lista seria uma reserva
de 4 pessoas com só o titular conhecido.

## Quem lê

- o próprio titular, pelo card da reserva — o hóspede vai no WHERE, como
  em toda leitura do perfil hóspede;
- a equipe, pela reserva (o card da equipe é o Bloco 8).

**Nenhum nome nem documento vai para o log.** Quem registra o pedido
escreve "com N acompanhantes".
"""

from app.dados.conexao import obter_cursor
from app.dados.erros import executar
from app.seguranca import cripto

VERSAO_DA_CRIPTOGRAFIA = 1

# Limites em caracteres, impostos pelo serviço antes de cifrar. O
# ciphertext do GCM tem o tamanho do texto claro: 120 caracteres são no
# máximo 480 bytes em UTF-8, dentro do VARBINARY(512) do nome.
NOME_MINIMO = 2
NOME_MAXIMO = 120
DOCUMENTO_MINIMO = 5
DOCUMENTO_MAXIMO = 20

_SQL_INSERIR = (
    "INSERT INTO acompanhante "
    "(reserva_id, ordem, nome_cif, nome_nonce, nome_tag, "
    " documento_cif, documento_nonce, documento_tag, menor_de_idade, "
    " cripto_versao) "
    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
)

# Do titular: o dono da reserva vai no WHERE. A reserva de outra pessoa
# devolve lista vazia, igual a uma reserva sem acompanhantes.
_SQL_POR_HOSPEDE = (
    "SELECT a.reserva_id, a.ordem, a.nome_cif, a.nome_nonce, a.nome_tag, "
    "       a.documento_cif, a.documento_nonce, a.documento_tag, "
    "       a.menor_de_idade "
    "FROM acompanhante a "
    "JOIN reserva r ON r.id = a.reserva_id "
    "WHERE r.hospede_id = %s "
    "ORDER BY a.reserva_id, a.ordem"
)

# Da equipe: uma reserva, sem filtro de dono.
_SQL_POR_RESERVA = (
    "SELECT a.reserva_id, a.ordem, a.nome_cif, a.nome_nonce, a.nome_tag, "
    "       a.documento_cif, a.documento_nonce, a.documento_tag, "
    "       a.menor_de_idade "
    "FROM acompanhante a "
    "WHERE a.reserva_id = %s "
    "ORDER BY a.ordem"
)


# ---------------------------------------------------------------------------
# Escrita
# ---------------------------------------------------------------------------

def _parametros(reserva_id, ordem, acompanhante: dict) -> tuple:
    nome = cripto.cifrar(
        acompanhante["nome"],
        cripto.aad_acompanhante(reserva_id, ordem, "nome"),
    )

    documento = acompanhante.get("documento")
    if documento:
        cifrado = cripto.cifrar(
            documento,
            cripto.aad_acompanhante(reserva_id, ordem, "documento"),
        )
    else:
        cifrado = (None, None, None)

    return (
        reserva_id,
        ordem,
        *nome,
        *cifrado,
        bool(acompanhante.get("menor_de_idade")),
        VERSAO_DA_CRIPTOGRAFIA,
    )


def gravar(cursor, reserva_id, acompanhantes) -> int:
    """Grava a lista no cursor de quem chama. Devolve quantos gravou.

    A ordem vem da posição na lista, de 1 em diante. O gatilho recusa o
    que passar de `num_pessoas - 1`, e o `CHECK` recusa adulto sem
    documento — a validação do serviço vem antes, mas quem decide é o
    banco.
    """
    quantidade = 0
    for ordem, acompanhante in enumerate(acompanhantes, start=1):
        executar(
            cursor, _SQL_INSERIR, _parametros(reserva_id, ordem, acompanhante)
        )
        quantidade += 1
    return quantidade


# ---------------------------------------------------------------------------
# Leitura
# ---------------------------------------------------------------------------

def _decifrar(linha: dict) -> dict:
    reserva_id, ordem = linha["reserva_id"], linha["ordem"]

    documento = None
    if linha["documento_cif"] is not None:
        documento = cripto.decifrar(
            linha["documento_cif"],
            linha["documento_nonce"],
            linha["documento_tag"],
            cripto.aad_acompanhante(reserva_id, ordem, "documento"),
        )

    return {
        "reserva_id": reserva_id,
        "ordem": ordem,
        "nome": cripto.decifrar(
            linha["nome_cif"],
            linha["nome_nonce"],
            linha["nome_tag"],
            cripto.aad_acompanhante(reserva_id, ordem, "nome"),
        ),
        "documento": documento,
        "menor_de_idade": bool(linha["menor_de_idade"]),
    }


def listar_por_hospede(hospede_id, usuario_id=None) -> dict:
    """Acompanhantes de todas as reservas de um hóspede, por reserva.

    `{reserva_id: [acompanhante, ...]}`. Uma consulta para a tela inteira
    de "Minhas reservas", e o filtro de dono no SQL.
    """
    por_reserva = {}
    with obter_cursor(usuario_id=usuario_id, dicionario=True) as cursor:
        cursor.execute(_SQL_POR_HOSPEDE, (hospede_id,))
        for linha in cursor.fetchall():
            aberto = _decifrar(linha)
            por_reserva.setdefault(aberto["reserva_id"], []).append(aberto)
    return por_reserva


def listar_da_reserva(reserva_id, usuario_id=None) -> list:
    """Acompanhantes de uma reserva, para a equipe."""
    with obter_cursor(usuario_id=usuario_id, dicionario=True) as cursor:
        cursor.execute(_SQL_POR_RESERVA, (reserva_id,))
        return [_decifrar(linha) for linha in cursor.fetchall()]


__all__ = [
    "DOCUMENTO_MAXIMO",
    "DOCUMENTO_MINIMO",
    "NOME_MAXIMO",
    "NOME_MINIMO",
    "gravar",
    "listar_da_reserva",
    "listar_por_hospede",
]
