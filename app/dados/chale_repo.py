"""Acesso à tabela de chalés.

Os cinco chalés da hospedaria. Diferente de `hospede`, aqui não há
cifragem: nome, capacidade e comodidades são informação pública, exibida
na vitrine do site.

Toda consulta é parametrizada e nenhuma f-string chega a um `execute` —
as consultas são constantes escritas por extenso. O motivo está
registrado em `app/dados/usuario_repo.py` e na documentação da Etapa 2.
"""

from app.dados.conexao import obter_conexao, obter_cursor
from app.dados.erros import ErroDeDados, executar

CAMPOS = ("nome", "capacidade", "configuracao_camas", "comodidades", "descricao")

LIMITES = {
    "nome": 50,
    "configuracao_camas": 255,
    "comodidades": 5000,
    "descricao": 5000,
}

# A coluna é TINYINT UNSIGNED. O teto de 20 não vem do banco: vem do bom
# senso sobre um chalé de hospedaria, e evita que um erro de digitação
# crie um chalé para 200 pessoas que a trigger de capacidade aceitaria.
CAPACIDADE_MINIMA = 1
CAPACIDADE_MAXIMA = 20

_SQL_LISTAR = (
    "SELECT id, nome, capacidade, configuracao_camas, comodidades, "
    "       descricao, ativo, criado_em, alterado_em "
    "FROM chale ORDER BY nome"
)

_SQL_LISTAR_ATIVOS = (
    "SELECT id, nome, capacidade, configuracao_camas, comodidades, "
    "       descricao, ativo, criado_em, alterado_em "
    "FROM chale WHERE ativo = TRUE ORDER BY nome"
)

_SQL_POR_ID = (
    "SELECT id, nome, capacidade, configuracao_camas, comodidades, "
    "       descricao, ativo, criado_em, alterado_em "
    "FROM chale WHERE id = %s LIMIT 1"
)

_SQL_CRIAR = (
    "INSERT INTO chale "
    "(nome, capacidade, configuracao_camas, comodidades, descricao) "
    "VALUES (%s, %s, %s, %s, %s)"
)

_SQL_ATUALIZAR = (
    "UPDATE chale SET nome = %s, capacidade = %s, configuracao_camas = %s, "
    "comodidades = %s, descricao = %s WHERE id = %s"
)

_SQL_DEFINIR_ATIVO = "UPDATE chale SET ativo = %s WHERE id = %s"
_SQL_CONTAR = "SELECT COUNT(*) FROM chale"
_SQL_CONTAR_ATIVOS = "SELECT COUNT(*) FROM chale WHERE ativo = TRUE"


class ErroDeChale(ErroDeDados):
    """Dado de chalé inválido, recusado antes de chegar ao banco."""


def _limpar(dados: dict) -> dict:
    """Valida e normaliza antes de gravar.

    A validação aqui não substitui a do banco: ela antecipa, para dar
    mensagem amigável em vez de deixar o erro de restrição subir. A
    garantia continua sendo do banco.
    """
    limpo = {}

    nome = (dados.get("nome") or "").strip()
    if not nome:
        raise ErroDeChale("O nome do chalé é obrigatório.")
    if len(nome) > LIMITES["nome"]:
        raise ErroDeChale(
            f"O nome tem {len(nome)} caracteres; o limite é {LIMITES['nome']}."
        )
    limpo["nome"] = nome

    bruta = dados.get("capacidade")
    try:
        capacidade = int(bruta)
    except (TypeError, ValueError):
        raise ErroDeChale("A capacidade precisa ser um número inteiro.") from None

    if not CAPACIDADE_MINIMA <= capacidade <= CAPACIDADE_MAXIMA:
        raise ErroDeChale(
            f"A capacidade precisa estar entre {CAPACIDADE_MINIMA} e "
            f"{CAPACIDADE_MAXIMA} pessoas."
        )
    limpo["capacidade"] = capacidade

    for campo in ("configuracao_camas", "comodidades", "descricao"):
        valor = dados.get(campo)
        valor = valor.strip() if isinstance(valor, str) else valor
        if valor and len(valor) > LIMITES[campo]:
            raise ErroDeChale(
                f"O campo {campo} tem {len(valor)} caracteres; "
                f"o limite é {LIMITES[campo]}."
            )
        limpo[campo] = valor or None

    return limpo


def listar(apenas_ativos: bool = False, usuario_id=None) -> list:
    with obter_cursor(usuario_id=usuario_id, dicionario=True) as cursor:
        cursor.execute(_SQL_LISTAR_ATIVOS if apenas_ativos else _SQL_LISTAR)
        return cursor.fetchall()


def buscar_por_id(identificador, usuario_id=None):
    with obter_cursor(usuario_id=usuario_id, dicionario=True) as cursor:
        cursor.execute(_SQL_POR_ID, (identificador,))
        return cursor.fetchone()


def criar(dados: dict, usuario_id=None) -> int:
    limpo = _limpar(dados)

    with obter_conexao(usuario_id=usuario_id) as conexao:
        cursor = conexao.cursor()
        try:
            executar(
                cursor,
                _SQL_CRIAR,
                (
                    limpo["nome"],
                    limpo["capacidade"],
                    limpo["configuracao_camas"],
                    limpo["comodidades"],
                    limpo["descricao"],
                ),
            )
            return cursor.lastrowid
        finally:
            cursor.close()


def atualizar(identificador, dados: dict, usuario_id=None) -> None:
    """Altera os dados de um chalé.

    Reduzir a capacidade abaixo do número de pessoas de uma reserva já
    gravada NÃO é impedido aqui nem pelo banco: a trigger só valida no
    momento da reserva. A tela avisa quando isso acontece, mas a decisão
    de reduzir é do administrador — um chalé pode mudar de configuração.
    """
    limpo = _limpar(dados)

    with obter_conexao(usuario_id=usuario_id) as conexao:
        cursor = conexao.cursor()
        try:
            executar(
                cursor,
                _SQL_ATUALIZAR,
                (
                    limpo["nome"],
                    limpo["capacidade"],
                    limpo["configuracao_camas"],
                    limpo["comodidades"],
                    limpo["descricao"],
                    identificador,
                ),
            )
        finally:
            cursor.close()


def definir_ativo(identificador, ativo: bool, usuario_id=None) -> None:
    """Desativa ou reativa um chalé, sem apagá-lo.

    Apagar perderia o histórico de reservas, e as chaves estrangeiras
    `RESTRICT` impediriam a exclusão de qualquer forma. Um chalé inativo
    some da vitrine e da consulta de disponibilidade, mas suas reservas
    passadas continuam nos relatórios.
    """
    with obter_conexao(usuario_id=usuario_id) as conexao:
        cursor = conexao.cursor()
        try:
            executar(cursor, _SQL_DEFINIR_ATIVO, (bool(ativo), identificador))
        finally:
            cursor.close()


def contar(apenas_ativos: bool = False, usuario_id=None) -> int:
    with obter_cursor(usuario_id=usuario_id) as cursor:
        cursor.execute(_SQL_CONTAR_ATIVOS if apenas_ativos else _SQL_CONTAR)
        return cursor.fetchone()[0]
