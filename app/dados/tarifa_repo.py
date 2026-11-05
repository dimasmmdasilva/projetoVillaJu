"""Acesso à tabela de tarifas: o valor da diária por chalé e por período.

É sobre estes registros que o cálculo do valor total de uma reserva
trabalha, percorrendo os períodos que a estadia atravessa.

Uma regra vive aqui e não no banco: **dois períodos de tarifa do mesmo
chalé não podem se sobrepor**. A trigger valida a coerência das datas e o
valor não negativo, mas não a sobreposição.

A razão de não estar no banco é a mesma pela qual a não-sobreposição de
reservas está: ela deveria estar. Acrescentá-la exigiria uma trigger nova
na Etapa 1, já documentada e entregue, e a consequência de ela faltar é
diferente — duas tarifas válidas na mesma data não corrompem dado, apenas
tornam o cálculo ambíguo. A verificação fica aqui, documentada como
divergência, e há teste cobrindo.
"""

import datetime as dt
from decimal import Decimal, InvalidOperation

from app.dados.conexao import obter_conexao, obter_cursor
from app.dados.erros import ErroDeDados, executar

LIMITE_DA_DESCRICAO = 100

# DECIMAL(10,2) comporta até 99.999.999,99. O teto aqui é de sanidade:
# uma diária de um milhão é erro de digitação, não preço.
VALOR_MINIMO = Decimal("0.00")
VALOR_MAXIMO = Decimal("100000.00")

_SQL_LISTAR = (
    "SELECT t.id, t.chale_id, c.nome AS chale_nome, t.data_inicio, "
    "       t.data_fim, t.valor_diaria, t.descricao, t.criado_em "
    "FROM tarifa t JOIN chale c ON c.id = t.chale_id "
    "ORDER BY c.nome, t.data_inicio"
)

_SQL_POR_CHALE = (
    "SELECT id, chale_id, data_inicio, data_fim, valor_diaria, descricao, "
    "       criado_em "
    "FROM tarifa WHERE chale_id = %s ORDER BY data_inicio"
)

_SQL_POR_ID = (
    "SELECT id, chale_id, data_inicio, data_fim, valor_diaria, descricao, "
    "       criado_em "
    "FROM tarifa WHERE id = %s LIMIT 1"
)

_SQL_CRIAR = (
    "INSERT INTO tarifa "
    "(chale_id, data_inicio, data_fim, valor_diaria, descricao) "
    "VALUES (%s, %s, %s, %s, %s)"
)

_SQL_ATUALIZAR = (
    "UPDATE tarifa SET data_inicio = %s, data_fim = %s, valor_diaria = %s, "
    "descricao = %s WHERE id = %s"
)

_SQL_EXCLUIR = "DELETE FROM tarifa WHERE id = %s"

# Sobreposição de períodos no mesmo chalé. Diferente da regra de reserva,
# aqui o intervalo é FECHADO nas duas pontas: uma tarifa vale de
# data_inicio até data_fim, ambas inclusive. Dois períodos se sobrepõem
# quando cada um começa antes de o outro terminar.
_SQL_SOBREPOSICAO = (
    "SELECT id, data_inicio, data_fim FROM tarifa "
    "WHERE chale_id = %s AND id <> %s "
    "  AND data_inicio <= %s AND data_fim >= %s "
    "LIMIT 1"
)

_SQL_VIGENTE = (
    "SELECT id, valor_diaria, descricao FROM tarifa "
    "WHERE chale_id = %s AND data_inicio <= %s AND data_fim >= %s LIMIT 1"
)


class ErroDeTarifa(ErroDeDados):
    """Dado de tarifa inválido, recusado antes de chegar ao banco."""


def _data(valor, rotulo: str) -> dt.date:
    if isinstance(valor, dt.date):
        return valor
    if not valor:
        raise ErroDeTarifa(f"A {rotulo} é obrigatória.")
    try:
        return dt.date.fromisoformat(str(valor).strip())
    except ValueError:
        raise ErroDeTarifa(
            f"A {rotulo} precisa estar no formato AAAA-MM-DD."
        ) from None


def _valor(bruto) -> Decimal:
    """Converte para Decimal, nunca para float.

    Ponto flutuante binário não representa valores decimais com exatidão,
    e erro de arredondamento em dinheiro é inaceitável. A coluna é
    DECIMAL, e o valor chega até ela como Decimal.
    """
    if bruto is None or str(bruto).strip() == "":
        raise ErroDeTarifa("O valor da diária é obrigatório.")

    try:
        valor = Decimal(str(bruto).strip().replace(",", "."))
    except InvalidOperation:
        raise ErroDeTarifa("O valor da diária precisa ser um número.") from None

    if valor < VALOR_MINIMO:
        raise ErroDeTarifa("O valor da diária não pode ser negativo.")
    if valor > VALOR_MAXIMO:
        raise ErroDeTarifa(
            f"O valor da diária não pode passar de {VALOR_MAXIMO}."
        )

    return valor.quantize(Decimal("0.01"))


def _limpar(dados: dict) -> dict:
    inicio = _data(dados.get("data_inicio"), "data inicial")
    fim = _data(dados.get("data_fim"), "data final")

    if fim < inicio:
        raise ErroDeTarifa(
            "A data final não pode ser anterior à data inicial."
        )

    descricao = dados.get("descricao")
    descricao = descricao.strip() if isinstance(descricao, str) else descricao
    if descricao and len(descricao) > LIMITE_DA_DESCRICAO:
        raise ErroDeTarifa(
            f"A descrição tem {len(descricao)} caracteres; "
            f"o limite é {LIMITE_DA_DESCRICAO}."
        )

    return {
        "data_inicio": inicio,
        "data_fim": fim,
        "valor_diaria": _valor(dados.get("valor_diaria")),
        "descricao": descricao or None,
    }


def _conferir_sobreposicao(cursor, chale_id, inicio, fim, ignorar_id=0) -> None:
    """Recusa período que se sobrepõe a outro do mesmo chalé.

    Com dois períodos válidos na mesma data, o cálculo do valor total
    ficaria ambíguo: qual das duas tarifas vale?
    """
    cursor.execute(_SQL_SOBREPOSICAO, (chale_id, ignorar_id or 0, fim, inicio))
    conflito = cursor.fetchone()

    if conflito:
        raise ErroDeTarifa(
            f"Já existe tarifa para este chalé entre "
            f"{conflito[1]:%d/%m/%Y} e {conflito[2]:%d/%m/%Y}. "
            f"Os períodos não podem se sobrepor."
        )


def listar(usuario_id=None) -> list:
    with obter_cursor(usuario_id=usuario_id, dicionario=True) as cursor:
        cursor.execute(_SQL_LISTAR)
        return cursor.fetchall()


def listar_por_chale(chale_id, usuario_id=None) -> list:
    with obter_cursor(usuario_id=usuario_id, dicionario=True) as cursor:
        cursor.execute(_SQL_POR_CHALE, (chale_id,))
        return cursor.fetchall()


def buscar_por_id(identificador, usuario_id=None):
    with obter_cursor(usuario_id=usuario_id, dicionario=True) as cursor:
        cursor.execute(_SQL_POR_ID, (identificador,))
        return cursor.fetchone()


def vigente_em(chale_id, data, usuario_id=None):
    """Tarifa que vale para o chalé numa data. Base do cálculo de valor."""
    data = _data(data, "data")
    with obter_cursor(usuario_id=usuario_id, dicionario=True) as cursor:
        cursor.execute(_SQL_VIGENTE, (chale_id, data, data))
        return cursor.fetchone()


def criar(chale_id, dados: dict, usuario_id=None) -> int:
    limpo = _limpar(dados)

    with obter_conexao(usuario_id=usuario_id) as conexao:
        cursor = conexao.cursor()
        try:
            _conferir_sobreposicao(
                cursor, chale_id, limpo["data_inicio"], limpo["data_fim"]
            )
            executar(
                cursor,
                _SQL_CRIAR,
                (
                    chale_id,
                    limpo["data_inicio"],
                    limpo["data_fim"],
                    limpo["valor_diaria"],
                    limpo["descricao"],
                ),
            )
            return cursor.lastrowid
        finally:
            cursor.close()


def atualizar(identificador, chale_id, dados: dict, usuario_id=None) -> None:
    limpo = _limpar(dados)

    with obter_conexao(usuario_id=usuario_id) as conexao:
        cursor = conexao.cursor()
        try:
            _conferir_sobreposicao(
                cursor,
                chale_id,
                limpo["data_inicio"],
                limpo["data_fim"],
                ignorar_id=identificador,
            )
            executar(
                cursor,
                _SQL_ATUALIZAR,
                (
                    limpo["data_inicio"],
                    limpo["data_fim"],
                    limpo["valor_diaria"],
                    limpo["descricao"],
                    identificador,
                ),
            )
        finally:
            cursor.close()


def excluir(identificador, usuario_id=None) -> None:
    with obter_conexao(usuario_id=usuario_id) as conexao:
        cursor = conexao.cursor()
        try:
            executar(cursor, _SQL_EXCLUIR, (identificador,))
        finally:
            cursor.close()
