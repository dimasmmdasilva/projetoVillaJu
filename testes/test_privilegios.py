"""Privilégios do usuário de aplicação.

Ler o arquivo de GRANT não prova nada: ele descreve a intenção, não o
efeito. Estes testes conectam ao banco COMO a aplicação e tentam fazer o
que ela não deveria conseguir, confirmando que o servidor recusa.

A verificação mais importante é a do log de auditoria. Conceder apenas
SELECT e INSERT o torna append-only do ponto de vista da aplicação: ela
registra eventos novos, mas não apaga nem reescreve os antigos. Sem isso,
a primeira providência de um atacante seria limpar o próprio rastro.

Os testes rodam contra `villaju_teste`, nunca contra o banco real.
"""

import pathlib

import mysql.connector
import pytest

pytestmark = pytest.mark.banco

RAIZ = pathlib.Path(__file__).resolve().parents[1]
SCRIPT_USUARIO = RAIZ / "banco" / "03_usuario_app.sql"

# O MySQL recusa a operação sem privilégio com este código.
ERRO_SEM_PRIVILEGIO = 1142

TABELAS_DE_DADOS = ["chale", "tarifa", "hospede", "reserva", "usuario", "contato"]


@pytest.fixture(scope="module")
def conexao_app(banco_para_privilegios):
    """Conexão como `villaju_app` ao banco de testes.

    Pula quando a conta ainda não foi criada, o que acontece até alguém
    rodar `banco/03_usuario_app.sql` como root. A suíte permanece verde em
    vez de acusar falhas que não são defeito do código.
    """
    from dotenv import dotenv_values

    cfg = dotenv_values(RAIZ / ".env")
    try:
        conexao = mysql.connector.connect(
            host=cfg["VILLAJU_DB_HOST"],
            port=int(cfg["VILLAJU_DB_PORTA"]),
            user=cfg["VILLAJU_DB_USUARIO"],
            password=cfg["VILLAJU_DB_SENHA"],
            database=cfg["VILLAJU_DB_NOME_TESTE"],
            autocommit=True,
        )
    except mysql.connector.Error as erro:
        pytest.skip(
            f"conta villaju_app indisponível no banco de testes "
            f"({erro.errno}: {erro.msg}).\n"
            f"  Rode, como root:\n"
            f'    mysql -u root -p villaju_teste -e "SOURCE banco/03_usuario_app.sql"'
        )

    yield conexao
    conexao.close()


@pytest.fixture(scope="module")
def banco_para_privilegios(conexao_banco):
    """Schema e triggers aplicados uma vez, para toda a bateria.

    Os testes de privilégio não precisam de banco limpo a cada caso: ou a
    operação é recusada pelo servidor, ou é permitida e desfeita em
    seguida.
    """
    from conftest import (
        SCRIPT_SCHEMA,
        SCRIPT_TRIGGERS,
        aplicar_script,
        limpar_banco,
    )

    cursor = conexao_banco.cursor()
    limpar_banco(cursor)
    aplicar_script(cursor, SCRIPT_SCHEMA)
    aplicar_script(cursor, SCRIPT_TRIGGERS)
    yield cursor
    cursor.close()


@pytest.fixture
def app(conexao_app):
    cursor = conexao_app.cursor()
    yield cursor
    cursor.close()


def _recusado(cursor, comando, parametros=()):
    """Executa esperando recusa por falta de privilégio."""
    with pytest.raises(mysql.connector.Error) as erro:
        cursor.execute(comando, parametros)
    assert erro.value.errno == ERRO_SEM_PRIVILEGIO, (
        f"esperada recusa por privilégio (1142), veio {erro.value.errno}: "
        f"{erro.value.msg}"
    )


# ---------------------------------------------------------------------------
# O que a aplicação PRECISA conseguir
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("tabela", TABELAS_DE_DADOS)
def test_aplicacao_le_as_tabelas_de_dados(app, tabela):
    app.execute(f"SELECT COUNT(*) FROM `{tabela}`")
    assert app.fetchone() is not None


def test_aplicacao_grava_e_apaga_dados(app):
    """O ciclo completo de uma operação de negócio."""
    app.execute(
        "INSERT INTO chale (nome, capacidade) VALUES ('Teste de Privilégio', 4)"
    )
    chale = app.lastrowid

    app.execute("UPDATE chale SET capacidade = 3 WHERE id = %s", (chale,))
    app.execute("SELECT capacidade FROM chale WHERE id = %s", (chale,))
    assert app.fetchone()[0] == 3

    app.execute("DELETE FROM chale WHERE id = %s", (chale,))
    app.execute("SELECT COUNT(*) FROM chale WHERE id = %s", (chale,))
    assert app.fetchone()[0] == 0


def test_aplicacao_le_o_log_de_auditoria(app):
    """Os relatórios de auditoria do administrador dependem disso."""
    app.execute("SELECT COUNT(*) FROM log_auditoria")
    assert app.fetchone() is not None


def test_aplicacao_registra_evento_no_log(app):
    """Login bem-sucedido, acesso negado e afins são gravados pela aplicação."""
    app.execute(
        "INSERT INTO log_auditoria (acao, tabela_afetada, data_hora, detalhe) "
        "VALUES ('LOGIN_OK', NULL, NOW(), 'Teste de privilégio')"
    )
    assert app.lastrowid > 0


def test_trigger_grava_no_log_apesar_da_restricao(app):
    """A trigger executa com o privilégio do DEFINER, não do invocador.

    É isso que permite à aplicação gerar auditoria sem ter permissão de
    escrita arbitrária na tabela: ela insere uma reserva, e a trigger -
    criada pelo root - grava o registro correspondente.
    """
    app.execute("INSERT INTO chale (nome, capacidade) VALUES ('Auditada', 4)")
    chale = app.lastrowid
    app.execute(
        "INSERT INTO hospede "
        "(nome_cif, nome_nonce, nome_tag, documento_cif, documento_nonce, "
        " documento_tag) VALUES (%s, %s, %s, %s, %s, %s)",
        (b"c" * 32, b"n" * 12, b"t" * 16, b"d" * 24, b"m" * 12, b"u" * 16),
    )
    hospede = app.lastrowid

    app.execute("SELECT COUNT(*) FROM log_auditoria WHERE tabela_afetada = 'reserva'")
    antes = app.fetchone()[0]

    app.execute(
        "INSERT INTO reserva "
        "(chale_id, hospede_id, data_entrada, data_saida, num_pessoas) "
        "VALUES (%s, %s, '2028-01-10', '2028-01-15', 2)",
        (chale, hospede),
    )
    reserva = app.lastrowid

    app.execute("SELECT COUNT(*) FROM log_auditoria WHERE tabela_afetada = 'reserva'")
    assert app.fetchone()[0] == antes + 1

    app.execute("DELETE FROM reserva WHERE id = %s", (reserva,))
    app.execute("DELETE FROM hospede WHERE id = %s", (hospede,))
    app.execute("DELETE FROM chale WHERE id = %s", (chale,))


def test_regra_de_nao_sobreposicao_vale_para_a_aplicacao(app):
    """A trigger não distingue quem dispara: vale para todo mundo."""
    app.execute("INSERT INTO chale (nome, capacidade) VALUES ('Conflito', 4)")
    chale = app.lastrowid
    app.execute(
        "INSERT INTO hospede "
        "(nome_cif, nome_nonce, nome_tag, documento_cif, documento_nonce, "
        " documento_tag) VALUES (%s, %s, %s, %s, %s, %s)",
        (b"e" * 32, b"n" * 12, b"t" * 16, b"f" * 24, b"m" * 12, b"u" * 16),
    )
    hospede = app.lastrowid

    app.execute(
        "INSERT INTO reserva "
        "(chale_id, hospede_id, data_entrada, data_saida, num_pessoas, situacao) "
        "VALUES (%s, %s, '2028-02-10', '2028-02-15', 2, 'confirmada')",
        (chale, hospede),
    )
    primeira = app.lastrowid

    with pytest.raises(mysql.connector.Error) as erro:
        app.execute(
            "INSERT INTO reserva "
            "(chale_id, hospede_id, data_entrada, data_saida, num_pessoas, situacao) "
            "VALUES (%s, %s, '2028-02-12', '2028-02-18', 2, 'confirmada')",
            (chale, hospede),
        )
    assert erro.value.errno == 1644

    app.execute("DELETE FROM reserva WHERE id = %s", (primeira,))
    app.execute("DELETE FROM hospede WHERE id = %s", (hospede,))
    app.execute("DELETE FROM chale WHERE id = %s", (chale,))


# ---------------------------------------------------------------------------
# O log de auditoria é append-only
# ---------------------------------------------------------------------------

def test_aplicacao_nao_altera_o_log(app):
    """Reescrever um registro de auditoria falsificaria o histórico."""
    _recusado(app, "UPDATE log_auditoria SET detalhe = 'adulterado' WHERE id > 0")


def test_aplicacao_nao_apaga_do_log(app):
    """Apagar o rastro é a primeira providência de quem invade."""
    _recusado(app, "DELETE FROM log_auditoria WHERE id > 0")


def test_aplicacao_nao_esvazia_o_log(app):
    """TRUNCATE contorna o DELETE e exige privilégio de DROP."""
    _recusado(app, "TRUNCATE TABLE log_auditoria")


# ---------------------------------------------------------------------------
# A aplicação não altera a estrutura do banco
# ---------------------------------------------------------------------------

def test_aplicacao_nao_cria_tabela(app):
    _recusado(app, "CREATE TABLE intrusa (id INT PRIMARY KEY)")


def test_aplicacao_nao_apaga_tabela(app):
    """Sem isso, uma injeção de SQL poderia destruir o sistema inteiro."""
    _recusado(app, "DROP TABLE reserva")


def test_aplicacao_nao_altera_tabela(app):
    """ALTER permitiria remover a restrição de unicidade, por exemplo."""
    _recusado(app, "ALTER TABLE chale ADD COLUMN intruso INT")


def test_aplicacao_nao_cria_trigger(app):
    """Criar trigger permitiria contornar ou substituir as regras do banco."""
    with pytest.raises(mysql.connector.Error):
        app.execute(
            "CREATE TRIGGER trg_intrusa BEFORE INSERT ON chale "
            "FOR EACH ROW SET NEW.capacidade = 99"
        )


def test_aplicacao_nao_apaga_trigger(app):
    """Apagar a trigger de não-sobreposição derrubaria a regra central."""
    with pytest.raises(mysql.connector.Error):
        app.execute("DROP TRIGGER trg_reserva_antes_inserir")


def test_aplicacao_nao_cria_indice(app):
    _recusado(app, "CREATE INDEX idx_intruso ON chale (capacidade)")


# ---------------------------------------------------------------------------
# A aplicação não administra o servidor
# ---------------------------------------------------------------------------

def test_aplicacao_nao_cria_usuario(app):
    """Criar conta própria seria escalar privilégio."""
    with pytest.raises(mysql.connector.Error):
        app.execute("CREATE USER 'intruso'@'localhost' IDENTIFIED BY 'x'")


def test_aplicacao_nao_concede_privilegio(app):
    """GRANT a si mesma anularia todo o resto desta configuração."""
    with pytest.raises(mysql.connector.Error):
        app.execute("GRANT ALL PRIVILEGES ON *.* TO 'villaju_app'@'localhost'")


def test_aplicacao_nao_le_o_banco_de_contas_do_mysql(app):
    """A tabela mysql.user guarda os hashes de senha de todas as contas."""
    with pytest.raises(mysql.connector.Error):
        app.execute("SELECT user, authentication_string FROM mysql.user")


def test_aplicacao_nao_altera_variavel_do_servidor(app):
    """Desligar verificações globais é um caminho conhecido de escalada."""
    with pytest.raises(mysql.connector.Error):
        app.execute("SET GLOBAL log_bin_trust_function_creators = 0")


def test_aplicacao_nao_enxerga_os_bancos_administrativos(app):
    """SHOW DATABASES devolve apenas aquilo a que a conta tem acesso.

    O `performance_schema` não entra nesta lista de propósito: o MySQL 8.0
    o expõe a toda conta conectada, e isso não depende dos nossos GRANT.
    O que importa é o que se consegue LER lá dentro, verificado no teste
    seguinte.
    """
    app.execute("SHOW DATABASES")
    bancos = {b[0] for b in app.fetchall()}
    proibidos = bancos & {"mysql", "sys"}
    assert not proibidos, f"a aplicação enxerga bancos do servidor: {proibidos}"


@pytest.mark.parametrize(
    "tabela",
    [
        # Guarda o TEXTO das consultas executadas no servidor, inclusive as
        # de outras sessões. Seria um vazamento de dado por via indireta.
        "events_statements_history",
        "events_statements_current",
        # Enumeram contas e conexões do servidor.
        "users",
        "accounts",
        "threads",
    ],
)
def test_aplicacao_nao_le_tabela_sensivel_do_performance_schema(app, tabela):
    """O banco aparece na listagem, mas seu conteúdo útil é inacessível.

    A distinção importa: ver o nome de um banco não revela nada. Ler o
    histórico de instruções revelaria consultas de outras sessões, com os
    valores que elas carregam.
    """
    _recusado(app, f"SELECT * FROM performance_schema.{tabela} LIMIT 1")


# ---------------------------------------------------------------------------
# O arquivo de privilégios
# ---------------------------------------------------------------------------

def test_script_nao_concede_privilegio_amplo():
    """GRANT ALL ou ON banco.* destruiria a separação entre as tabelas."""
    texto = SCRIPT_USUARIO.read_text(encoding="utf-8")
    instrucoes = [
        linha.strip()
        for linha in texto.splitlines()
        if "GRANT" in linha.upper() and not linha.strip().startswith("--")
    ]
    for instrucao in instrucoes:
        maiuscula = instrucao.upper()
        assert "GRANT ALL" not in maiuscula, f"privilégio amplo demais: {instrucao}"
        assert "ON *.*" not in maiuscula, f"privilégio global: {instrucao}"
        assert "`.*" not in instrucao, (
            f"privilégio no banco inteiro daria UPDATE no log: {instrucao}"
        )


def test_script_nao_concede_escrita_no_log():
    """A linha do log_auditoria precisa listar apenas SELECT e INSERT."""
    texto = SCRIPT_USUARIO.read_text(encoding="utf-8")
    linhas = [
        linha
        for linha in texto.splitlines()
        if "log_auditoria" in linha and "GRANT" in linha.upper()
    ]
    assert linhas, "nenhum GRANT sobre log_auditoria encontrado"
    for linha in linhas:
        maiuscula = linha.upper()
        assert "UPDATE" not in maiuscula, f"log deixaria de ser append-only: {linha}"
        assert "DELETE" not in maiuscula, f"log deixaria de ser append-only: {linha}"


def test_script_nao_concede_mudanca_de_estrutura():
    texto = SCRIPT_USUARIO.read_text(encoding="utf-8").upper()
    instrucoes = [
        linha
        for linha in texto.splitlines()
        if "GRANT" in linha and not linha.strip().startswith("--")
    ]
    for perigoso in ("CREATE", "ALTER", "DROP", "GRANT OPTION", "SUPER", "FILE"):
        culpadas = [i for i in instrucoes if perigoso in i.replace("GRANT ", "", 1)]
        assert not culpadas, f"privilégio {perigoso} concedido: {culpadas}"
