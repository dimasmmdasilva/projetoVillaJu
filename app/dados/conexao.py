"""Pool de conexões e identificação do usuário para a auditoria.

Duas responsabilidades.

A primeira é não abrir uma conexão nova a cada consulta. O custo de
estabelecer conexão com o MySQL — rede, autenticação, negociação — é alto
comparado ao de uma consulta simples. O pool mantém um punhado de conexões
prontas e as empresta.

A segunda é informar ao banco QUEM está operando. As triggers de auditoria
não têm como saber: do ponto de vista do MySQL, toda operação vem de
`villaju_app`. A aplicação declara o usuário real numa variável de sessão,
e as triggers a leem.

    with obter_conexao(usuario_id=7) as conexao:
        ...

O ponto delicado está no fim do bloco: a variável de sessão precisa ser
APAGADA antes de a conexão voltar ao pool. Sem isso, a próxima requisição
que receber essa mesma conexão herdaria o identificador anterior, e as
operações dela seriam registradas em nome de outra pessoa. Seria um defeito
silencioso e grave — a auditoria passaria a mentir.
"""

import contextlib
import itertools
import logging

import mysql.connector
from mysql.connector import pooling

_pool = None
_contador_de_pools = itertools.count(1)

# Registro de eventos do módulo. A configuração do destino — arquivo,
# console, nível — é responsabilidade de quem monta a aplicação, não deste
# módulo.
_registrador = logging.getLogger(__name__)

VARIAVEL_DE_SESSAO = "@app_usuario_id"

# Escritas por extenso, sem f-string, pela mesma regra dos repositórios:
# nenhuma f-string chega a um `execute`, de modo que qualquer uma que
# apareça seja anomalia visível na revisão e para o Bandit.
_SQL_DEFINIR_USUARIO = "SET @app_usuario_id = %s"
_SQL_LIMPAR_USUARIO = "SET @app_usuario_id = NULL"
_SQL_LER_USUARIO = "SELECT @app_usuario_id"


class ErroDeConexao(Exception):
    """Falha ao obter ou configurar conexão com o banco."""


def iniciar_pool(configuracao) -> None:
    """Cria o pool a partir da configuração da aplicação.

    Chamado por `create_app`. Chamar de novo descarta o pool anterior e
    cria outro, o que é o comportamento desejado quando os testes montam
    várias aplicações no mesmo processo.
    """
    global _pool

    encerrar_pool()

    try:
        _pool = pooling.MySQLConnectionPool(
            # O nome precisa ser único no processo: dois pools com o mesmo
            # nome fazem a biblioteca devolver o primeiro, silenciosamente,
            # ignorando a configuração nova.
            pool_name=f"villaju_{next(_contador_de_pools)}",
            pool_size=configuracao.banco_tamanho_do_pool,
            pool_reset_session=True,
            autocommit=False,
            charset="utf8mb4",
            collation="utf8mb4_unicode_ci",
            **configuracao.banco,
        )
    except mysql.connector.Error as erro:
        raise ErroDeConexao(
            f"não foi possível criar o pool de conexões "
            f"({erro.errno}: {erro.msg}).\n"
            f"  Verifique se o serviço MySQL está no ar e se as credenciais "
            f"do .env estão corretas."
        ) from erro


def encerrar_pool() -> None:
    """Descarta o pool. Usado entre testes e ao desmontar a aplicação."""
    global _pool
    _pool = None


def pool_iniciado() -> bool:
    return _pool is not None


def _exigir_pool():
    if _pool is None:
        raise ErroDeConexao(
            "o pool de conexões não foi iniciado. "
            "Chame iniciar_pool(configuracao) antes de usar o banco."
        )
    return _pool


def _definir_usuario(conexao, usuario_id) -> None:
    """Declara ao banco quem está operando, para as triggers de auditoria."""
    cursor = conexao.cursor()
    try:
        cursor.execute(_SQL_DEFINIR_USUARIO, (usuario_id,))
    finally:
        cursor.close()


def _limpar_usuario(conexao) -> None:
    """Apaga a variável de sessão antes de devolver a conexão ao pool.

    Esta função é a razão de o módulo existir como está. Uma conexão
    emprestada carrega consigo o estado de sessão de quem a usou antes; se
    o identificador permanecesse, a requisição seguinte gravaria auditoria
    em nome do usuário anterior.

    Falhas aqui não interrompem a execução: se a conexão já morreu, não há
    o que limpar, e levantar exceção dentro de um bloco `finally`
    esconderia o erro original que levou até ali.

    Mas também não são engolidas em silêncio. Uma falha repetida nesta
    limpeza indicaria que conexões estão voltando ao pool em estado
    inconsistente, e isso precisa aparecer em algum lugar.
    """
    try:
        cursor = conexao.cursor()
        try:
            cursor.execute(_SQL_LIMPAR_USUARIO)
        finally:
            cursor.close()
    except Exception as erro:  # noqa: BLE001 - conexão possivelmente inutilizável
        _registrador.warning(
            "falha ao limpar a variável de sessão da conexão: %s", erro
        )


@contextlib.contextmanager
def obter_conexao(usuario_id=None):
    """Empresta uma conexão do pool, já identificada e em transação.

    Confirma a transação ao sair sem erro e desfaz em caso de exceção. É
    o comportamento certo para uma operação de negócio: ou ela acontece
    inteira, ou não acontece.

    `usuario_id` é o identificador do usuário autenticado, ou None para
    operação sem sessão — uma tentativa de login que falhou antes de
    identificar a conta, por exemplo.
    """
    conexao = _exigir_pool().get_connection()
    try:
        _definir_usuario(conexao, usuario_id)
        yield conexao
        conexao.commit()
    except Exception:
        with contextlib.suppress(Exception):
            conexao.rollback()
        raise
    finally:
        _limpar_usuario(conexao)
        conexao.close()


@contextlib.contextmanager
def obter_cursor(usuario_id=None, dicionario: bool = False):
    """Atalho para quem só precisa de um cursor.

    Com `dicionario=True` as linhas vêm como mapeamentos de nome de coluna
    para valor, o que torna o código de leitura independente da ordem das
    colunas na consulta.
    """
    with obter_conexao(usuario_id) as conexao:
        cursor = conexao.cursor(dictionary=dicionario)
        try:
            yield cursor
        finally:
            cursor.close()


def usuario_da_sessao(conexao):
    """Lê a variável de sessão. Existe para os testes a verificarem."""
    cursor = conexao.cursor()
    try:
        cursor.execute(_SQL_LER_USUARIO)
        linha = cursor.fetchone()
        return linha[0] if linha else None
    finally:
        cursor.close()
