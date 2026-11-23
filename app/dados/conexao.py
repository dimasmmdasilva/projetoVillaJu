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
import time

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

# Relatório 2, achado R2-06 (23/11). Duas reservas simultâneas do mesmo
# chalé passavam as duas: o gatilho contava os conflitos com uma leitura
# que, no isolamento padrão do MySQL (REPEATABLE READ), usa o retrato do
# banco tirado no começo da transação e não vê o que outra acabou de
# confirmar. Agora o gatilho trava a linha do chalé — a fila — e só
# então conta. Em READ COMMITTED (o padrão do PostgreSQL e do Oracle),
# cada leitura vê tudo o que já foi confirmado até aquele instante, então a
# contagem feita depois da fila enxerga a reserva que passou antes. Sem
# isto, a trava do chalé não bastaria. O pool zera a sessão a cada
# devolução, por isso o nível é fixado a cada empréstimo.
_SQL_ISOLAMENTO = "SET SESSION TRANSACTION ISOLATION LEVEL READ COMMITTED"


class ErroDeConexao(Exception):
    """Falha ao obter ou configurar conexão com o banco."""


class BancoOcupado(ErroDeConexao):
    """Todas as conexões do pool seguiram ocupadas durante a espera.

    Vira resposta 503 ("sistema ocupado, tente em instantes"), não 500:
    não é defeito, é excesso de pedidos simultâneos (Relatório 2, R2-03).
    """


# Relatório 2, achado R2-03. O pool do mysql-connector não espera: com
# todas as conexões emprestadas, o pedido seguinte falhava NA HORA, e a
# pessoa via "erro inesperado" (500). Na análise dinâmica, o rastreador do
# ZAP, com vários pedidos simultâneos, produziu 14 desses. Agora o pedido
# espera uma conexão ser devolvida — o normal é questão de milissegundos —
# e só desiste depois deste prazo.
ESPERA_PELO_POOL_SEGUNDOS = 2.0
INTERVALO_DA_ESPERA_SEGUNDOS = 0.05


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
    """Prepara a sessão emprestada: o nível de isolamento (R2-06) e quem
    está operando, para as triggers de auditoria."""
    cursor = conexao.cursor()
    try:
        cursor.execute(_SQL_ISOLAMENTO)
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


def _emprestar_do_pool():
    """Pega uma conexão do pool, esperando até o prazo se todas estão em uso."""
    pool = _exigir_pool()
    prazo = time.monotonic() + ESPERA_PELO_POOL_SEGUNDOS
    while True:
        try:
            return pool.get_connection()
        except mysql.connector.errors.PoolError as erro:
            if time.monotonic() >= prazo:
                raise BancoOcupado(
                    "Todas as conexões com o banco seguiram ocupadas."
                ) from erro
            time.sleep(INTERVALO_DA_ESPERA_SEGUNDOS)


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
    conexao = _emprestar_do_pool()
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
