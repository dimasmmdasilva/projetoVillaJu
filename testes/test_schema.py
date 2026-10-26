"""Estrutura do banco de dados.

Estes testes conferem o schema criado contra a especificação do plano, e
não contra si mesmo: as expectativas estão escritas aqui como dados, de
forma que uma alteração em `banco/01_schema.sql` que divirja do projeto
acuse falha em vez de passar despercebida.

A verificação é feita pela `information_schema`, que é o catálogo do
próprio MySQL. Ela descreve o que o servidor realmente criou, não o que o
script pediu - os dois podem diferir quando uma definição é aceita com
interpretação diferente da pretendida.
"""

import pytest

from conftest import SCRIPT_SCHEMA, TABELAS, dividir_comandos

pytestmark = pytest.mark.banco


# ---------------------------------------------------------------------------
# Expectativas, declaradas a partir da seção 5.1 do plano
# ---------------------------------------------------------------------------

# tabela -> coluna -> (tipo_de_dado, aceita_nulo)
COLUNAS = {
    "chale": {
        "id": ("int", False),
        "nome": ("varchar", False),
        "capacidade": ("tinyint", False),
        "configuracao_camas": ("varchar", True),
        "comodidades": ("text", True),
        "descricao": ("text", True),
        "ativo": ("tinyint", False),
        "criado_em": ("datetime", False),
        "alterado_em": ("datetime", False),
    },
    "hospede": {
        "id": ("int", False),
        "nome_cif": ("varbinary", False),
        "nome_nonce": ("varbinary", False),
        "nome_tag": ("varbinary", False),
        "documento_cif": ("varbinary", False),
        "documento_nonce": ("varbinary", False),
        "documento_tag": ("varbinary", False),
        "telefone_cif": ("varbinary", True),
        "email_cif": ("varbinary", True),
        "email_busca": ("varbinary", True),
        "documento_busca": ("varbinary", True),
        "cripto_versao": ("tinyint", False),
        "criado_em": ("datetime", False),
        "alterado_em": ("datetime", False),
    },
    "usuario": {
        "id": ("int", False),
        "login": ("varchar", False),
        "nome": ("varchar", False),
        "senha_hash": ("varchar", False),
        "perfil": ("enum", False),
        "hospede_id": ("int", True),
        "ativo": ("tinyint", False),
        "ultimo_acesso": ("datetime", True),
    },
    "tarifa": {
        "id": ("int", False),
        "chale_id": ("int", False),
        "data_inicio": ("date", False),
        "data_fim": ("date", False),
        "valor_diaria": ("decimal", False),
        "descricao": ("varchar", True),
    },
    "reserva": {
        "id": ("int", False),
        "chale_id": ("int", False),
        "hospede_id": ("int", False),
        "data_entrada": ("date", False),
        "data_saida": ("date", False),
        "num_pessoas": ("tinyint", False),
        "situacao": ("enum", False),
        "valor_total": ("decimal", True),
        "observacoes": ("text", True),
        "criado_por": ("int", True),
        "criado_em": ("datetime", False),
        "alterado_em": ("datetime", False),
    },
    "log_auditoria": {
        "id": ("bigint", False),
        "usuario_id": ("int", True),
        "acao": ("varchar", False),
        "tabela_afetada": ("varchar", True),
        "registro_id": ("int", True),
        "data_hora": ("datetime", False),
        "detalhe": ("varchar", True),
    },
    "contato": {
        "id": ("int", False),
        "nome": ("varchar", False),
        "email": ("varchar", False),
        "telefone": ("varchar", True),
        "mensagem": ("text", True),
        "chale_interesse_id": ("int", True),
        "ip_origem": ("varbinary", True),
        "situacao": ("enum", False),
        "criado_em": ("datetime", False),
    },
}

# (tabela, coluna) -> (tabela_referenciada, regra_ao_apagar)
CHAVES_ESTRANGEIRAS = {
    ("usuario", "hospede_id"): ("hospede", "RESTRICT"),
    ("tarifa", "chale_id"): ("chale", "RESTRICT"),
    ("reserva", "chale_id"): ("chale", "RESTRICT"),
    ("reserva", "hospede_id"): ("hospede", "RESTRICT"),
    ("reserva", "criado_por"): ("usuario", "SET NULL"),
    ("log_auditoria", "usuario_id"): ("usuario", "SET NULL"),
    ("contato", "chale_interesse_id"): ("chale", "SET NULL"),
}

UNICIDADE = [
    ("chale", "nome"),
    ("usuario", "login"),
    ("hospede", "email_busca"),
    ("hospede", "documento_busca"),
]

ENUMS = {
    ("reserva", "situacao"): {"pendente", "confirmada", "cancelada", "concluida"},
    ("usuario", "perfil"): {"administrador", "recepcionista", "hospede"},
    ("contato", "situacao"): {"novo", "em_atendimento", "respondido", "descartado"},
}

# Colunas da tabela hospede que guardam bytes, nunca texto.
COLUNAS_BINARIAS = [
    "nome_cif", "nome_nonce", "nome_tag",
    "documento_cif", "documento_nonce", "documento_tag",
    "telefone_cif", "telefone_nonce", "telefone_tag",
    "email_cif", "email_nonce", "email_tag",
    "email_busca", "documento_busca",
]


def _uma(cursor, sql, parametros=()):
    cursor.execute(sql, parametros)
    linha = cursor.fetchone()
    return linha[0] if linha else None


# ---------------------------------------------------------------------------
# Tabelas
# ---------------------------------------------------------------------------

def test_as_sete_tabelas_existem(estrutura):
    estrutura.execute(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema = DATABASE()"
    )
    existentes = {t[0] for t in estrutura.fetchall()}
    assert existentes == set(TABELAS), (
        f"faltando: {set(TABELAS) - existentes or 'nada'}; "
        f"sobrando: {existentes - set(TABELAS) or 'nada'}"
    )


@pytest.mark.parametrize("tabela", TABELAS)
def test_tabela_usa_innodb(estrutura, tabela):
    """Sem InnoDB não há chave estrangeira nem transação."""
    engine = _uma(
        estrutura,
        "SELECT engine FROM information_schema.tables "
        "WHERE table_schema = DATABASE() AND table_name = %s",
        (tabela,),
    )
    assert engine == "InnoDB", f"{tabela} usa {engine}"


@pytest.mark.parametrize("tabela", TABELAS)
def test_tabela_usa_utf8mb4(estrutura, tabela):
    """O utf8 antigo do MySQL trunca em 3 bytes e corrompe emoji."""
    collation = _uma(
        estrutura,
        "SELECT table_collation FROM information_schema.tables "
        "WHERE table_schema = DATABASE() AND table_name = %s",
        (tabela,),
    )
    assert collation == "utf8mb4_unicode_ci", f"{tabela} usa {collation}"


# ---------------------------------------------------------------------------
# Colunas
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "tabela,coluna,tipo,aceita_nulo",
    [
        (tabela, coluna, tipo, nulo)
        for tabela, colunas in COLUNAS.items()
        for coluna, (tipo, nulo) in colunas.items()
    ],
    ids=lambda v: str(v),
)
def test_coluna_tem_tipo_e_nulidade_corretos(estrutura, tabela, coluna, tipo, aceita_nulo):
    estrutura.execute(
        "SELECT data_type, is_nullable FROM information_schema.columns "
        "WHERE table_schema = DATABASE() AND table_name = %s AND column_name = %s",
        (tabela, coluna),
    )
    linha = estrutura.fetchone()
    assert linha is not None, f"coluna {tabela}.{coluna} não existe"

    tipo_real, nulo_real = linha
    assert tipo_real == tipo, f"{tabela}.{coluna} é {tipo_real}, esperado {tipo}"
    assert (nulo_real == "YES") == aceita_nulo, (
        f"{tabela}.{coluna} aceita nulo: {nulo_real == 'YES'}, "
        f"esperado {aceita_nulo}"
    )


@pytest.mark.parametrize("coluna", COLUNAS_BINARIAS)
def test_campo_cifrado_e_binario(estrutura, coluna):
    """VARCHAR aplicaria conversão de charset sobre bytes cifrados.

    O MySQL interpretaria o ciphertext como texto na codificação da coluna
    e o reescreveria. A corrupção é silenciosa e só aparece ao decifrar.
    """
    tipo = _uma(
        estrutura,
        "SELECT data_type FROM information_schema.columns "
        "WHERE table_schema = DATABASE() AND table_name = 'hospede' "
        "AND column_name = %s",
        (coluna,),
    )
    assert tipo == "varbinary", f"hospede.{coluna} é {tipo}, deveria ser varbinary"


def test_nonce_tem_96_bits(estrutura):
    """O AES-GCM usa nonce de 96 bits por recomendação do NIST."""
    for coluna in ("nome_nonce", "documento_nonce", "telefone_nonce", "email_nonce"):
        tamanho = _uma(
        estrutura,
            "SELECT character_maximum_length FROM information_schema.columns "
            "WHERE table_schema = DATABASE() AND table_name = 'hospede' "
            "AND column_name = %s",
            (coluna,),
        )
        assert tamanho == 12, f"hospede.{coluna} tem {tamanho} bytes, esperado 12"


def test_tag_tem_128_bits(estrutura):
    """A tag de autenticação do GCM tem 16 bytes."""
    for coluna in ("nome_tag", "documento_tag", "telefone_tag", "email_tag"):
        tamanho = _uma(
        estrutura,
            "SELECT character_maximum_length FROM information_schema.columns "
            "WHERE table_schema = DATABASE() AND table_name = 'hospede' "
            "AND column_name = %s",
            (coluna,),
        )
        assert tamanho == 16, f"hospede.{coluna} tem {tamanho} bytes, esperado 16"


def test_campo_de_busca_tem_tamanho_do_sha256(estrutura):
    """O HMAC-SHA256 produz exatamente 32 bytes."""
    for coluna in ("email_busca", "documento_busca"):
        tamanho = _uma(
        estrutura,
            "SELECT character_maximum_length FROM information_schema.columns "
            "WHERE table_schema = DATABASE() AND table_name = 'hospede' "
            "AND column_name = %s",
            (coluna,),
        )
        assert tamanho == 32, f"hospede.{coluna} tem {tamanho} bytes, esperado 32"


@pytest.mark.parametrize("tabela,coluna", sorted(ENUMS), ids=lambda v: str(v))
def test_enum_tem_os_valores_previstos(estrutura, tabela, coluna):
    tipo = _uma(
        estrutura,
        "SELECT column_type FROM information_schema.columns "
        "WHERE table_schema = DATABASE() AND table_name = %s AND column_name = %s",
        (tabela, coluna),
    )
    valores = {v.strip("'") for v in tipo[len("enum("):-1].split(",")}
    assert valores == ENUMS[(tabela, coluna)]


# ---------------------------------------------------------------------------
# Chaves e índices
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("tabela", TABELAS)
def test_tabela_tem_chave_primaria(estrutura, tabela):
    quantas = _uma(
        estrutura,
        "SELECT COUNT(*) FROM information_schema.key_column_usage "
        "WHERE table_schema = DATABASE() AND table_name = %s "
        "AND constraint_name = 'PRIMARY'",
        (tabela,),
    )
    assert quantas == 1, f"{tabela} tem {quantas} colunas na chave primária"


@pytest.mark.parametrize(
    "tabela,coluna", sorted(CHAVES_ESTRANGEIRAS), ids=lambda v: str(v)
)
def test_chave_estrangeira_aponta_para_a_tabela_certa(estrutura, tabela, coluna):
    referenciada, _ = CHAVES_ESTRANGEIRAS[(tabela, coluna)]
    real = _uma(
        estrutura,
        "SELECT referenced_table_name FROM information_schema.key_column_usage "
        "WHERE table_schema = DATABASE() AND table_name = %s AND column_name = %s "
        "AND referenced_table_name IS NOT NULL",
        (tabela, coluna),
    )
    assert real == referenciada, (
        f"{tabela}.{coluna} aponta para {real}, esperado {referenciada}"
    )


@pytest.mark.parametrize(
    "tabela,coluna", sorted(CHAVES_ESTRANGEIRAS), ids=lambda v: str(v)
)
def test_chave_estrangeira_tem_a_regra_de_exclusao_certa(estrutura, tabela, coluna):
    """A regra define o que acontece com o filho quando o pai é apagado.

    Em log_auditoria a escolha é de segurança: com CASCADE, apagar um
    usuário apagaria o rastro das operações dele.
    """
    _, regra = CHAVES_ESTRANGEIRAS[(tabela, coluna)]
    real = _uma(
        estrutura,
        "SELECT rc.delete_rule "
        "FROM information_schema.referential_constraints rc "
        "JOIN information_schema.key_column_usage kcu "
        "  ON kcu.constraint_name = rc.constraint_name "
        " AND kcu.constraint_schema = rc.constraint_schema "
        "WHERE rc.constraint_schema = DATABASE() AND rc.table_name = %s "
        "AND kcu.column_name = %s",
        (tabela, coluna),
    )
    assert real == regra, f"{tabela}.{coluna} usa ON DELETE {real}, esperado {regra}"


@pytest.mark.parametrize("tabela,coluna", UNICIDADE, ids=lambda v: str(v))
def test_coluna_tem_restricao_de_unicidade(estrutura, tabela, coluna):
    nao_unico = _uma(
        estrutura,
        "SELECT MIN(non_unique) FROM information_schema.statistics "
        "WHERE table_schema = DATABASE() AND table_name = %s AND column_name = %s",
        (tabela, coluna),
    )
    assert nao_unico == 0, f"{tabela}.{coluna} não tem restrição de unicidade"


def test_indice_de_disponibilidade_existe_na_ordem_certa(estrutura):
    """A ordem das colunas define quais consultas o índice acelera.

    Um índice composto só é útil da esquerda para a direita. Invertida a
    ordem, a trigger de conflito e a consulta de disponibilidade deixariam
    de aproveitá-lo e passariam a varrer a tabela inteira.
    """
    estrutura.execute(
        "SELECT column_name FROM information_schema.statistics "
        "WHERE table_schema = DATABASE() AND table_name = 'reserva' "
        "AND index_name = 'idx_reserva_disponibilidade' "
        "ORDER BY seq_in_index"
    )
    colunas = [c[0] for c in estrutura.fetchall()]
    assert colunas == ["chale_id", "data_entrada", "data_saida", "situacao"], (
        f"índice de disponibilidade tem as colunas {colunas}"
    )


# ---------------------------------------------------------------------------
# Integridade em funcionamento
# ---------------------------------------------------------------------------

def test_chave_estrangeira_rejeita_referencia_inexistente(banco):
    """Declarar a chave não basta: ela precisa estar sendo aplicada."""
    import mysql.connector

    banco.execute(
        "INSERT INTO chale (nome, capacidade, criado_em, alterado_em) "
        "VALUES ('Teste', 4, NOW(), NOW())"
    )
    with pytest.raises(mysql.connector.Error) as erro:
        banco.execute(
            "INSERT INTO tarifa "
            "(chale_id, data_inicio, data_fim, valor_diaria, criado_em, alterado_em) "
            "VALUES (9999, '2026-01-01', '2026-01-31', 300.00, NOW(), NOW())"
        )
    assert erro.value.errno == 1452, "esperado erro de chave estrangeira"


def test_unicidade_do_nome_do_chale_e_aplicada(banco):
    import mysql.connector

    banco.execute(
        "INSERT INTO chale (nome, capacidade, criado_em, alterado_em) "
        "VALUES ('Embaré', 4, NOW(), NOW())"
    )
    with pytest.raises(mysql.connector.Error) as erro:
        banco.execute(
            "INSERT INTO chale (nome, capacidade, criado_em, alterado_em) "
            "VALUES ('Embaré', 2, NOW(), NOW())"
        )
    assert erro.value.errno == 1062, "esperado erro de entrada duplicada"


def test_restrict_impede_apagar_chale_com_reserva(banco):
    """Apagar um chalé não pode fazer sumir o histórico de reservas."""
    import mysql.connector

    banco.execute(
        "INSERT INTO chale (nome, capacidade, criado_em, alterado_em) "
        "VALUES ('Dunas', 4, NOW(), NOW())"
    )
    banco.execute(
        "INSERT INTO hospede "
        "(nome_cif, nome_nonce, nome_tag, documento_cif, documento_nonce, "
        " documento_tag, criado_em, alterado_em) "
        "VALUES (%s, %s, %s, %s, %s, %s, NOW(), NOW())",
        (b"x" * 32, b"n" * 12, b"t" * 16, b"y" * 32, b"m" * 12, b"u" * 16),
    )
    banco.execute(
        "INSERT INTO reserva "
        "(chale_id, hospede_id, data_entrada, data_saida, num_pessoas, "
        " criado_em, alterado_em) "
        "VALUES (1, 1, '2026-12-01', '2026-12-05', 2, NOW(), NOW())"
    )
    with pytest.raises(mysql.connector.Error) as erro:
        banco.execute("DELETE FROM chale WHERE id = 1")
    assert erro.value.errno == 1451, "esperado bloqueio por chave estrangeira"


def test_bytes_cifrados_voltam_identicos(banco):
    """Prova que VARBINARY não altera os bytes gravados.

    Usa uma sequência que seria corrompida se a coluna fosse VARCHAR: o
    byte 0x00, bytes isolados acima de 0x7F e uma sequência UTF-8 inválida.
    """
    bytes_dificeis = bytes([0x00, 0xFF, 0xC3, 0x28, 0x80, 0x1B, 0x7F, 0xFE])

    banco.execute(
        "INSERT INTO hospede "
        "(nome_cif, nome_nonce, nome_tag, documento_cif, documento_nonce, "
        " documento_tag, criado_em, alterado_em) "
        "VALUES (%s, %s, %s, %s, %s, %s, NOW(), NOW())",
        (bytes_dificeis, b"n" * 12, b"t" * 16, b"y" * 32, b"m" * 12, b"u" * 16),
    )
    banco.execute("SELECT nome_cif FROM hospede WHERE id = LAST_INSERT_ID()")
    devolvido = banco.fetchone()[0]
    assert devolvido == bytes_dificeis, (
        f"os bytes mudaram: gravado {bytes_dificeis!r}, lido {devolvido!r}"
    )


# ---------------------------------------------------------------------------
# O leitor de scripts SQL
# ---------------------------------------------------------------------------

def test_schema_tem_sete_comandos():
    """Um comando CREATE TABLE por tabela, nem mais nem menos."""
    comandos = dividir_comandos(SCRIPT_SCHEMA.read_text(encoding="utf-8"))
    criacoes = [c for c in comandos if "CREATE TABLE" in c.upper()]
    assert len(criacoes) == len(TABELAS)


def test_schema_nao_seleciona_banco():
    """Com USE ou CREATE DATABASE, o script não serviria ao banco de teste."""
    texto = SCRIPT_SCHEMA.read_text(encoding="utf-8")
    instrucoes = [
        linha.strip()
        for linha in texto.splitlines()
        if not linha.strip().startswith("--")
        and linha.strip().upper().startswith(("USE ", "CREATE DATABASE"))
    ]
    assert not instrucoes, f"o schema seleciona banco: {instrucoes}"


def test_leitor_respeita_delimiter():
    """Sem interpretar DELIMITER, o corpo de uma trigger seria partido.

    Este teste não depende do MySQL: verifica apenas o separador que o
    conftest usa para carregar `02_triggers.sql` no bloco seguinte.
    """
    sql = """
CREATE TABLE a (id INT);

DELIMITER $$

CREATE TRIGGER t BEFORE INSERT ON a
FOR EACH ROW
BEGIN
    SET @x = 1;
    SET @y = 2;
END$$

DELIMITER ;

CREATE TABLE b (id INT);
"""
    comandos = dividir_comandos(sql)
    assert len(comandos) == 3, f"esperados 3 comandos, obtidos {len(comandos)}"
    assert "CREATE TABLE a" in comandos[0]
    assert "CREATE TRIGGER" in comandos[1]
    assert "SET @y = 2;" in comandos[1], "o corpo da trigger foi partido"
    assert "CREATE TABLE b" in comandos[2]
