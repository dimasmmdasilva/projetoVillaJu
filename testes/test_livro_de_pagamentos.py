"""O esquema do pagamento por Pix — Etapa 5, Bloco 7.

Duas coisas entraram no banco:

1. **Colunas novas em `reserva`**: a modalidade (sinal ou integral), os
   prazos, as declarações do hóspede ("Fiz o Pix"), o aceite do
   regulamento e o motivo do cancelamento.
2. **A tabela `pagamento`**, o livro dos Pix que a equipe conferiu no
   extrato. **Só aceita acréscimo.**

Estes testes rodam como a conta de testes, que tem todos os privilégios —
é o que um administrador com o Workbench aberto teria. Por isso eles
provam o que **os gatilhos** garantem. O que **o privilégio** garante
contra a aplicação está em `test_privilegios.py`.
"""

import mysql.connector
import pytest

pytestmark = pytest.mark.banco

ERRO_DA_REGRA = 1644          # SIGNAL SQLSTATE '45000' de um gatilho
ERRO_DE_DUPLICIDADE = 1062
ERRO_DE_RESTRICAO = 3819      # CHECK violado
ERRO_DE_REFERENCIA = 1451     # apagar linha referenciada
ERRO_DE_NULO = 1048


# ---------------------------------------------------------------------------
# Apoio
# ---------------------------------------------------------------------------

def _chale(cursor, nome="Embaré", capacidade=4):
    cursor.execute(
        "INSERT INTO chale (nome, capacidade) VALUES (%s, %s)",
        (nome, capacidade),
    )
    return cursor.lastrowid


def _hospede(cursor, semente=b"a"):
    cursor.execute(
        "INSERT INTO hospede "
        "(nome_cif, nome_nonce, nome_tag, documento_cif, documento_nonce, "
        " documento_tag) VALUES (%s, %s, %s, %s, %s, %s)",
        (semente * 32, semente * 12, semente * 16,
         semente * 24, semente * 12, semente * 16),
    )
    return cursor.lastrowid


def _equipe(cursor, login="recepcao"):
    cursor.execute(
        "INSERT INTO usuario (login, nome, senha_hash, perfil) "
        "VALUES (%s, %s, %s, 'recepcionista')",
        (login, "Recepção", "$argon2id$v=19$m=65536,t=3,p=4$abc$def"),
    )
    return cursor.lastrowid


def _reserva(cursor, chale, hospede, entrada="2026-12-10",
             saida="2026-12-15", situacao="confirmada"):
    cursor.execute(
        "INSERT INTO reserva "
        "(chale_id, hospede_id, data_entrada, data_saida, num_pessoas, "
        " situacao) VALUES (%s, %s, %s, %s, 2, %s)",
        (chale, hospede, entrada, saida, situacao),
    )
    return cursor.lastrowid


def _lancar(cursor, reserva, autor, tipo="sinal", valor="650.00"):
    cursor.execute(
        "INSERT INTO pagamento (reserva_id, tipo, valor, registrado_por) "
        "VALUES (%s, %s, %s, %s)",
        (reserva, tipo, valor, autor),
    )
    return cursor.lastrowid


def _recusa(cursor, comando, parametros=()):
    with pytest.raises(mysql.connector.Error) as erro:
        cursor.execute(comando, parametros)
    return erro.value


@pytest.fixture
def livro(banco_com_triggers):
    cursor = banco_com_triggers
    chale = _chale(cursor)
    hospede = _hospede(cursor)
    reserva = _reserva(cursor, chale, hospede)
    autor = _equipe(cursor)
    return {
        "cursor": cursor,
        "chale": chale,
        "hospede": hospede,
        "reserva": reserva,
        "autor": autor,
    }


# ---------------------------------------------------------------------------
# O livro só aceita acréscimo
# ---------------------------------------------------------------------------

def test_o_lancamento_nao_se_altera(livro):
    """Nem por quem tem todos os privilégios.

    A conta da aplicação já não tem UPDATE sobre `pagamento`. O gatilho
    fecha o mesmo caminho para quem entra no banco com outra conta.
    """
    cursor = livro["cursor"]
    lancamento = _lancar(cursor, livro["reserva"], livro["autor"])

    erro = _recusa(
        cursor, "UPDATE pagamento SET valor = 1.00 WHERE id = %s",
        (lancamento,),
    )

    assert erro.errno == ERRO_DA_REGRA
    assert "não se altera" in erro.msg


def test_o_lancamento_nao_se_apaga(livro):
    cursor = livro["cursor"]
    lancamento = _lancar(cursor, livro["reserva"], livro["autor"])

    erro = _recusa(cursor, "DELETE FROM pagamento WHERE id = %s", (lancamento,))

    assert erro.errno == ERRO_DA_REGRA
    assert "não se apaga" in erro.msg


def test_o_momento_do_lancamento_vem_do_banco(livro):
    """A data decide em que mês o lançamento soma — e não pode ser
    escolhida por quem lança."""
    cursor = livro["cursor"]
    cursor.execute(
        "INSERT INTO pagamento "
        "(reserva_id, tipo, valor, registrado_por, registrado_em) "
        "VALUES (%s, 'sinal', 650.00, %s, '2001-01-01 00:00:00')",
        (livro["reserva"], livro["autor"]),
    )

    cursor.execute(
        "SELECT TIMESTAMPDIFF(SECOND, registrado_em, NOW()) "
        "FROM pagamento WHERE id = %s",
        (cursor.lastrowid,),
    )
    segundos = cursor.fetchone()[0]

    assert 0 <= segundos < 60, (
        "o lançamento aceitou a data enviada por quem lançou"
    )


# ---------------------------------------------------------------------------
# O que um lançamento pode ser
# ---------------------------------------------------------------------------

def test_um_lancamento_de_cada_tipo_por_reserva(livro):
    """Um clique duplo em "Pagou 50%" não conta o sinal duas vezes."""
    cursor = livro["cursor"]
    _lancar(cursor, livro["reserva"], livro["autor"], "sinal")

    erro = _recusa(
        cursor,
        "INSERT INTO pagamento (reserva_id, tipo, valor, registrado_por) "
        "VALUES (%s, 'sinal', 650.00, %s)",
        (livro["reserva"], livro["autor"]),
    )

    assert erro.errno == ERRO_DE_DUPLICIDADE


@pytest.mark.parametrize(
    "rotulo,tipo,valor",
    [
        ("entrada negativa", "sinal", "-650.00"),
        ("entrada zerada", "integral", "0.00"),
        ("devolução positiva", "devolucao", "650.00"),
    ],
)
def test_o_sinal_do_valor_acompanha_o_tipo(livro, rotulo, tipo, valor):
    """Entrada é positiva, devolução é negativa.

    É o que deixa a soma do mês ser a soma simples da coluna, sem regra
    de sinal espalhada pelas consultas — e uma devolução lançada como
    positiva **dobraria** o que ela deveria anular.
    """
    erro = _recusa(
        livro["cursor"],
        "INSERT INTO pagamento (reserva_id, tipo, valor, registrado_por) "
        "VALUES (%s, %s, %s, %s)",
        (livro["reserva"], tipo, valor, livro["autor"]),
    )

    assert erro.errno == ERRO_DE_RESTRICAO, rotulo


def test_a_devolucao_negativa_e_aceita(livro):
    """O par do teste acima: sem ele, um CHECK que recusasse tudo
    passaria."""
    cursor = livro["cursor"]
    _lancar(cursor, livro["reserva"], livro["autor"], "sinal", "650.00")
    _lancar(cursor, livro["reserva"], livro["autor"], "devolucao", "-650.00")

    cursor.execute(
        "SELECT SUM(valor) FROM pagamento WHERE reserva_id = %s",
        (livro["reserva"],),
    )
    assert cursor.fetchone()[0] == 0


def test_o_autor_e_obrigatorio(livro):
    """O autor é a parte do registro que lhe dá valor."""
    erro = _recusa(
        livro["cursor"],
        "INSERT INTO pagamento (reserva_id, tipo, valor, registrado_por) "
        "VALUES (%s, 'sinal', 650.00, NULL)",
        (livro["reserva"],),
    )

    assert erro.errno == ERRO_DE_NULO


def test_nao_se_apaga_reserva_com_pagamento(livro):
    cursor = livro["cursor"]
    _lancar(cursor, livro["reserva"], livro["autor"])

    erro = _recusa(
        cursor, "DELETE FROM reserva WHERE id = %s", (livro["reserva"],)
    )

    assert erro.errno == ERRO_DE_REFERENCIA


def test_nao_se_apaga_o_autor_de_um_lancamento(livro):
    """Conta de equipe se desativa, não se apaga."""
    cursor = livro["cursor"]
    _lancar(cursor, livro["reserva"], livro["autor"])

    erro = _recusa(
        cursor, "DELETE FROM usuario WHERE id = %s", (livro["autor"],)
    )

    assert erro.errno == ERRO_DE_REFERENCIA


# ---------------------------------------------------------------------------
# As colunas novas de `reserva`
# ---------------------------------------------------------------------------

def test_reserva_da_equipe_continua_sem_modalidade(livro):
    """Nenhuma coluna nova é obrigatória: a reserva registrada pela
    recepção segue o fluxo manual de antes."""
    cursor = livro["cursor"]
    cursor.execute(
        "SELECT modalidade, prazo_pagamento, prazo_restante "
        "FROM reserva WHERE id = %s",
        (livro["reserva"],),
    )
    assert cursor.fetchone() == (None, None, None)


# Comandos inteiros, escritos por extenso: a regra do projeto de que
# nenhum texto montado chega a um `execute` vale também nos testes.
_MODALIDADE_SEM_PRAZO = (
    "INSERT INTO reserva "
    "(chale_id, hospede_id, data_entrada, data_saida, num_pessoas, "
    " situacao, modalidade, prazo_pagamento) VALUES "
    "(%s, %s, '2027-01-10', '2027-01-12', 2, 'pendente', 'sinal', NULL)"
)
_PRAZO_SEM_MODALIDADE = (
    "INSERT INTO reserva "
    "(chale_id, hospede_id, data_entrada, data_saida, num_pessoas, "
    " situacao, modalidade, prazo_pagamento) VALUES "
    "(%s, %s, '2027-01-10', '2027-01-12', 2, 'pendente', NULL, "
    " NOW() + INTERVAL 90 MINUTE)"
)


@pytest.mark.parametrize(
    "rotulo,comando",
    [
        ("modalidade sem prazo", _MODALIDADE_SEM_PRAZO),
        ("prazo sem modalidade", _PRAZO_SEM_MODALIDADE),
    ],
)
def test_modalidade_e_prazo_andam_juntos(livro, rotulo, comando):
    """Metade preenchida seria um pedido que nunca vence, ou um prazo sem
    regra que o explique."""
    cursor = livro["cursor"]
    hospede = _hospede(cursor, b"b")

    erro = _recusa(cursor, comando, (livro["chale"], hospede))

    assert erro.errno == ERRO_DE_RESTRICAO, rotulo


def test_prazo_do_restante_so_existe_com_sinal(livro):
    cursor = livro["cursor"]
    hospede = _hospede(cursor, b"c")

    erro = _recusa(
        cursor,
        "INSERT INTO reserva "
        "(chale_id, hospede_id, data_entrada, data_saida, num_pessoas, "
        " situacao, modalidade, prazo_pagamento, prazo_restante) VALUES "
        "(%s, %s, '2027-01-10', '2027-01-12', 2, 'pendente', 'integral', "
        " NOW() + INTERVAL 90 MINUTE, '2027-01-08 23:59:59')",
        (livro["chale"], hospede),
    )

    assert erro.errno == ERRO_DE_RESTRICAO


# ---------------------------------------------------------------------------
# A invariante que obriga a limpeza a rodar ANTES da gravação
# ---------------------------------------------------------------------------

def test_o_gatilho_trata_pedido_vencido_como_ocupacao(livro):
    """**Este teste prende um fato do banco, e não um defeito.**

    O gatilho de não-sobreposição considera ocupado o chalé de qualquer
    reserva `pendente` — inclusive um pedido cujo prazo do Pix já
    venceu e que a varredura ainda não cancelou. O gatilho não mudou no
    Bloco 7, e não vai mudar: é a parte mais defendida do projeto.

    A consequência é uma regra de **ordem**: a varredura dos pedidos
    vencidos precisa rodar antes de qualquer reserva nova ser gravada, na
    mesma transação. Se rodasse depois, a reserva nova seria recusada por
    causa de um pedido que já devia estar cancelado.

    Se um dia o gatilho passar a ignorar pedido vencido, este teste fica
    vermelho — e avisa que a ordem da varredura deixou de ser o que
    protege o caso.
    """
    cursor = livro["cursor"]
    hospede = _hospede(cursor, b"d")

    cursor.execute(
        "INSERT INTO reserva "
        "(chale_id, hospede_id, data_entrada, data_saida, num_pessoas, "
        " situacao, modalidade, prazo_pagamento) VALUES "
        "(%s, %s, '2027-02-10', '2027-02-12', 2, 'pendente', 'integral', "
        " NOW() - INTERVAL 1 MINUTE)",
        (livro["chale"], hospede),
    )

    outro = _hospede(cursor, b"e")
    erro = _recusa(
        cursor,
        "INSERT INTO reserva "
        "(chale_id, hospede_id, data_entrada, data_saida, num_pessoas, "
        " situacao) VALUES (%s, %s, '2027-02-10', '2027-02-12', 2, "
        " 'confirmada')",
        (livro["chale"], outro),
    )

    assert erro.errno == ERRO_DA_REGRA
    assert "Já existe reserva ativa" in erro.msg
