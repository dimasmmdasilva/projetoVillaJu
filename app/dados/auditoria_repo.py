"""Registro de eventos no log de auditoria.

As operações sobre `reserva` e `hospede` são registradas pelas triggers do
banco, sem participação da aplicação. Este módulo cobre o que a trigger
não enxerga: eventos de sessão.

    LOGIN_OK        autenticação bem-sucedida
    LOGIN_FALHA     tentativa recusada
    LOGOUT          encerramento de sessão
    ACESSO_NEGADO   rota acessada sem o perfil exigido

Regra que vale para todo registro gravado aqui: o campo `detalhe` recebe
descrição genérica, nunca valores. Nada de nome, documento, telefone,
e-mail, senha tentada ou conteúdo de campo cifrado.

O motivo é direto. O log é legível por quem tem acesso de leitura ao
banco, e a aplicação tem permissão de escrita nele. Se ele guardasse
valores, viraria uma segunda via dos dados — fora da proteção da tabela
original e sem a cifragem que a protege.

A aplicação tem SELECT e INSERT nesta tabela, e não tem UPDATE nem DELETE.
Do ponto de vista dela, o log só cresce: nem comprometida ela apaga o
próprio rastro.
"""

from app.dados.conexao import obter_conexao, obter_cursor

LOGIN_OK = "LOGIN_OK"
LOGIN_FALHA = "LOGIN_FALHA"
LOGOUT = "LOGOUT"
ACESSO_NEGADO = "ACESSO_NEGADO"

# Teto da coluna `detalhe` no schema. Texto maior seria truncado pelo
# banco ou rejeitado, dependendo do modo estrito em vigor.
TAMANHO_DO_DETALHE = 255

# Consultas como constantes, pelo mesmo motivo do usuario_repo: nenhuma
# f-string chega ao `execute`, de modo que qualquer uma seja anomalia.
_SQL_LISTAR = (
    "SELECT l.id, l.usuario_id, u.login AS usuario_login, l.acao, "
    "       l.tabela_afetada, l.registro_id, l.data_hora, l.detalhe "
    "FROM log_auditoria l "
    "LEFT JOIN usuario u ON u.id = l.usuario_id "
    "ORDER BY l.id DESC LIMIT %s"
)


def registrar(
    acao: str,
    usuario_id=None,
    tabela: str = None,
    registro_id=None,
    detalhe: str = None,
) -> None:
    """Grava um evento no log.

    `usuario_id` fica nulo quando o evento não tem sessão associada — uma
    tentativa de login com usuário inexistente, por exemplo. O evento
    continua registrado; apenas sem o vínculo.
    """
    if detalhe is not None:
        detalhe = detalhe[:TAMANHO_DO_DETALHE]

    with obter_conexao(usuario_id=usuario_id) as conexao:
        cursor = conexao.cursor()
        try:
            cursor.execute(
                "INSERT INTO log_auditoria "
                "(usuario_id, acao, tabela_afetada, registro_id, data_hora, detalhe) "
                "VALUES (%s, %s, %s, %s, NOW(), %s)",
                (usuario_id, acao, tabela, registro_id, detalhe),
            )
        finally:
            cursor.close()


def listar(limite: int = 100, usuario_id=None) -> list:
    """Últimos eventos, do mais recente para o mais antigo.

    Consulta restrita ao perfil de administrador, aplicada na rota. O
    limite vai como parâmetro, não interpolado: o conector aceita
    parâmetro em LIMIT, e usá-lo mantém a regra de que nenhuma f-string
    chega ao `execute`.

    A conversão para inteiro e o teto de mil continuam, como segunda
    camada e para evitar que uma consulta devolva a tabela inteira.
    """
    limite = max(1, min(int(limite), 1000))

    with obter_cursor(usuario_id=usuario_id, dicionario=True) as cursor:
        cursor.execute(_SQL_LISTAR, (limite,))
        return cursor.fetchall()


def contar_por_acao(usuario_id=None) -> list:
    """Quantos eventos de cada tipo. Usado no painel e nos relatórios."""
    with obter_cursor(usuario_id=usuario_id, dicionario=True) as cursor:
        cursor.execute(
            "SELECT acao, COUNT(*) AS total FROM log_auditoria "
            "GROUP BY acao ORDER BY total DESC"
        )
        return cursor.fetchall()
