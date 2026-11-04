"""Acesso à tabela de usuários da equipe.

Toda consulta é parametrizada, sem exceção. Nenhuma montagem de SQL por
concatenação de texto, f-string ou operador de formatação — é a regra 3 do
projeto, e ela vale inclusive para consultas administrativas.

A diferença não é estilística. Com parâmetro, o valor viaja separado do
comando e o servidor nunca o interpreta como SQL: um login digitado como
`' OR '1'='1` é procurado literalmente, como se fosse um nome esquisito.
Com concatenação, ele viraria parte da consulta.
"""

from app.dados.conexao import obter_conexao, obter_cursor

# As consultas são constantes de módulo, escritas por extenso. Nenhuma
# f-string chega ao `execute` — nem para montar lista de colunas, que seria
# inofensivo em si.
#
# O motivo é tornar o perigo visível. Uma f-string num `execute` é o
# padrão exato da injeção de SQL, e misturar usos inofensivos com o
# perigoso treina o leitor a ignorá-lo. Com esta regra, qualquer f-string
# que apareça aqui é anomalia, e tanto o Bandit quanto a revisão por pares
# a apontam de imediato.

_SQL_POR_LOGIN = (
    "SELECT id, login, nome, senha_hash, perfil, hospede_id, ativo, "
    "       ultimo_acesso, criado_em, alterado_em "
    "FROM usuario WHERE login = %s LIMIT 1"
)

_SQL_POR_ID = (
    "SELECT id, login, nome, senha_hash, perfil, hospede_id, ativo, "
    "       ultimo_acesso, criado_em, alterado_em "
    "FROM usuario WHERE id = %s LIMIT 1"
)

_SQL_LISTAR = (
    "SELECT id, login, nome, senha_hash, perfil, hospede_id, ativo, "
    "       ultimo_acesso, criado_em, alterado_em "
    "FROM usuario ORDER BY nome"
)


def buscar_por_login(login: str, usuario_id=None):
    """Devolve o usuário com esse login, ou None.

    Inclui contas inativas de propósito: quem decide o que fazer com elas
    é a rota de login, que precisa recusar a entrada com a MESMA mensagem
    usada para senha errada. Filtrar aqui faria a conta desativada
    responder diferente da inexistente, e a diferença revelaria quais
    contas existem.
    """
    if not isinstance(login, str) or not login.strip():
        return None

    with obter_cursor(usuario_id=usuario_id, dicionario=True) as cursor:
        cursor.execute(_SQL_POR_LOGIN, (login.strip(),))
        return cursor.fetchone()


def buscar_por_id(identificador, usuario_id=None):
    with obter_cursor(usuario_id=usuario_id, dicionario=True) as cursor:
        cursor.execute(_SQL_POR_ID, (identificador,))
        return cursor.fetchone()


def listar(usuario_id=None) -> list:
    with obter_cursor(usuario_id=usuario_id, dicionario=True) as cursor:
        cursor.execute(_SQL_LISTAR)
        return cursor.fetchall()


def registrar_acesso(identificador, usuario_id=None) -> None:
    """Marca o momento do último acesso bem-sucedido."""
    with obter_conexao(usuario_id=usuario_id) as conexao:
        cursor = conexao.cursor()
        try:
            cursor.execute(
                "UPDATE usuario SET ultimo_acesso = NOW() WHERE id = %s",
                (identificador,),
            )
        finally:
            cursor.close()


def atualizar_hash(identificador, novo_hash: str, usuario_id=None) -> None:
    """Regrava a senha com os parâmetros de calibração atuais.

    Chamada no login bem-sucedido, quando o hash guardado foi gerado com
    parâmetros mais fracos. É o único momento em que a senha em claro está
    disponível para recalcular — fora dele, só restaria forçar todo mundo
    a trocar de senha.
    """
    with obter_conexao(usuario_id=usuario_id) as conexao:
        cursor = conexao.cursor()
        try:
            cursor.execute(
                "UPDATE usuario SET senha_hash = %s WHERE id = %s",
                (novo_hash, identificador),
            )
        finally:
            cursor.close()
