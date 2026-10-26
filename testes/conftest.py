"""Infraestrutura compartilhada dos testes de banco de dados.

Os testes rodam contra o banco `villaju_teste`, nunca contra o banco real.
O usuário `villaju_teste` tem privilégio total sobre esse banco e nenhum
sobre qualquer outro, de modo que mesmo um teste mal escrito, com um DROP
equivocado, não alcança dado de produção.

O schema é montado a partir dos MESMOS arquivos que criam o banco real.
Testar uma cópia do schema não provaria nada: a cópia poderia divergir do
original exatamente no ponto que contém o defeito.

Quando o MySQL não está acessível - porque o integrante ainda não rodou
`banco/00_bancos.sql`, ou porque o serviço está parado - os testes de banco
são PULADOS com uma mensagem que explica o que fazer. Eles não falham: uma
suíte com quarenta erros vermelhos esconde os problemas reais.
"""

import pathlib

import pytest

RAIZ = pathlib.Path(__file__).resolve().parents[1]
BANCO = RAIZ / "banco"

SCRIPT_SCHEMA = BANCO / "01_schema.sql"
SCRIPT_TRIGGERS = BANCO / "02_triggers.sql"
SCRIPT_DADOS = BANCO / "04_dados_ficticios.sql"

TABELAS = [
    "chale",
    "hospede",
    "usuario",
    "tarifa",
    "reserva",
    "log_auditoria",
    "contato",
]


# ---------------------------------------------------------------------------
# Leitura de scripts SQL
# ---------------------------------------------------------------------------

def _e_comentario(linha: str) -> bool:
    despida = linha.strip()
    return not despida or despida.startswith("--")


def _tem_conteudo(bloco: str) -> bool:
    """Verdadeiro se o bloco tem alguma instrução, e não só comentários."""
    return any(not _e_comentario(linha) for linha in bloco.splitlines())


def dividir_comandos(sql: str) -> list:
    """Separa um script SQL em comandos, respeitando DELIMITER.

    A instrução DELIMITER não é SQL: é um comando do cliente `mysql`, que o
    driver não reconhece. Sem interpretá-la aqui, o corpo de uma trigger
    seria partido em pedaços a cada ponto e vírgula interno, e nenhum deles
    seria SQL válido.
    """
    delimitador = ";"
    comandos = []
    atual = []

    for linha in sql.splitlines():
        despida = linha.strip()

        if despida.upper().startswith("DELIMITER "):
            bloco = "\n".join(atual)
            if _tem_conteudo(bloco):
                comandos.append(bloco)
            atual = []
            delimitador = despida.split(None, 1)[1].strip()
            continue

        atual.append(linha)

        if not despida.startswith("--") and despida.endswith(delimitador):
            bloco = "\n".join(atual).rstrip()
            bloco = bloco[: -len(delimitador)]
            if _tem_conteudo(bloco):
                comandos.append(bloco)
            atual = []

    resto = "\n".join(atual)
    if _tem_conteudo(resto):
        comandos.append(resto)

    return comandos


def _consumir_resultados(cursor) -> None:
    """Esvazia qualquer resultado pendente deixado pelo último comando.

    Scripts terminam com SELECT de conferência e SHOW GRANTS. O driver
    guarda essas linhas até que alguém as leia, e recusa a consulta
    seguinte com 'Unread result found' enquanto isso não acontecer.
    """
    try:
        if cursor.with_rows:
            cursor.fetchall()
        while cursor.nextset():
            if cursor.with_rows:
                cursor.fetchall()
    except Exception:  # noqa: BLE001 - não há mais resultado a consumir
        pass


def aplicar_script(cursor, caminho: pathlib.Path) -> int:
    """Executa um script SQL comando a comando. Devolve quantos rodaram."""
    comandos = dividir_comandos(caminho.read_text(encoding="utf-8"))
    for numero, comando in enumerate(comandos, start=1):
        try:
            cursor.execute(comando)
            _consumir_resultados(cursor)
        except Exception as erro:  # noqa: BLE001 - queremos o contexto na mensagem
            primeira = next(
                (l for l in comando.splitlines() if not _e_comentario(l)), ""
            )
            raise AssertionError(
                f"{caminho.name}: falha no comando {numero} de {len(comandos)}\n"
                f"  início: {primeira.strip()[:100]}\n"
                f"  erro:   {erro}"
            ) from erro
    return len(comandos)


def limpar_banco(cursor) -> None:
    """Apaga todas as tabelas do banco de teste.

    As verificações de chave estrangeira são suspensas durante a limpeza
    para que a ordem de exclusão não importe. Sem isso, apagar `chale`
    antes de `reserva` falharia.
    """
    cursor.execute("SET FOREIGN_KEY_CHECKS = 0")
    cursor.execute("SHOW TABLES")
    for (tabela,) in cursor.fetchall():
        cursor.execute(f"DROP TABLE IF EXISTS `{tabela}`")
    cursor.execute("SET FOREIGN_KEY_CHECKS = 1")


# ---------------------------------------------------------------------------
# Conexão
# ---------------------------------------------------------------------------

def _configuracao():
    from dotenv import dotenv_values

    cfg = dotenv_values(RAIZ / ".env")
    faltando = [
        nome
        for nome in (
            "VILLAJU_DB_HOST",
            "VILLAJU_DB_PORTA",
            "VILLAJU_DB_NOME_TESTE",
            "VILLAJU_DB_USUARIO_TESTE",
            "VILLAJU_DB_SENHA_TESTE",
        )
        if not cfg.get(nome)
    ]
    if faltando:
        pytest.skip(
            "configuração de banco de teste ausente no .env: "
            + ", ".join(faltando)
            + ". Rode: python scripts/gerar_chaves.py",
            allow_module_level=False,
        )
    return cfg


@pytest.fixture(scope="session")
def conexao_banco():
    """Conexão única com o banco de teste, reaproveitada por toda a sessão.

    Abrir conexão por teste custaria caro sem nenhum ganho: os testes não
    competem entre si, porque o banco é zerado antes de cada um.
    """
    import mysql.connector

    cfg = _configuracao()
    try:
        conexao = mysql.connector.connect(
            host=cfg["VILLAJU_DB_HOST"],
            port=int(cfg["VILLAJU_DB_PORTA"]),
            user=cfg["VILLAJU_DB_USUARIO_TESTE"],
            password=cfg["VILLAJU_DB_SENHA_TESTE"],
            database=cfg["VILLAJU_DB_NOME_TESTE"],
            autocommit=True,
        )
    except mysql.connector.Error as erro:
        pytest.skip(
            f"MySQL inacessível ({erro.errno}: {erro.msg}).\n"
            f"  Verifique se o serviço está no ar e se você executou:\n"
            f'    mysql -u root -p -e "SOURCE banco/00_bancos.sql"'
        )

    yield conexao
    conexao.close()


@pytest.fixture
def banco(conexao_banco):
    """Banco zerado com o schema recém-aplicado, para um único teste.

    Cada teste recebe um banco limpo. Testes que dependem do estado
    deixado por outro são frágeis e falham em ordem diferente.
    """
    cursor = conexao_banco.cursor()
    limpar_banco(cursor)
    aplicar_script(cursor, SCRIPT_SCHEMA)
    yield cursor
    cursor.close()


@pytest.fixture(scope="module")
def estrutura(conexao_banco):
    """Schema aplicado uma única vez por arquivo de teste.

    Para testes que apenas consultam a information_schema, recriar as sete
    tabelas a cada caso é desperdício: eles não alteram nada e não podem
    interferir uns nos outros. A separação entre esta fixture e `banco`
    deixou a suíte de estrutura cerca de dez vezes mais rápida.

    Testes que gravam, apagam ou disparam trigger usam `banco`, que entrega
    um banco limpo a cada caso.
    """
    cursor = conexao_banco.cursor()
    limpar_banco(cursor)
    aplicar_script(cursor, SCRIPT_SCHEMA)
    yield cursor
    cursor.close()


@pytest.fixture
def banco_com_triggers(banco):
    """Banco com o schema e as triggers aplicados.

    Pula enquanto `02_triggers.sql` não existir, o que mantém a suíte
    verde entre os blocos de desenvolvimento.
    """
    if not SCRIPT_TRIGGERS.is_file():
        pytest.skip("banco/02_triggers.sql ainda não foi escrito")
    aplicar_script(banco, SCRIPT_TRIGGERS)
    return banco


@pytest.fixture(scope="module")
def banco_com_dados(conexao_banco):
    """Banco completo: schema, triggers e massa de dados fictícios.

    A carga roda uma vez por arquivo de teste. Ela é cara - dispara todas
    as triggers de validação e de auditoria - e os testes que a consomem
    apenas consultam o resultado.

    O fato de a carga COMPLETAR já é uma verificação: se qualquer um dos
    registros violasse as regras das triggers, o script falharia aqui.
    """
    if not SCRIPT_DADOS.is_file():
        pytest.skip("banco/04_dados_ficticios.sql ainda não foi escrito")

    cursor = conexao_banco.cursor()
    limpar_banco(cursor)
    aplicar_script(cursor, SCRIPT_SCHEMA)
    aplicar_script(cursor, SCRIPT_TRIGGERS)
    aplicar_script(cursor, SCRIPT_DADOS)
    yield cursor
    cursor.close()
