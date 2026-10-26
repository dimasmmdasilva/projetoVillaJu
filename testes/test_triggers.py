"""Triggers do banco de dados.

Aqui se verifica o argumento central do projeto: a regra de
não-sobreposição de reservas é imposta pelo BANCO, não pela aplicação.

Todos os testes gravam por SQL direto, sem passar por nenhuma camada
Python de validação. É exatamente o caminho que um atacante com acesso ao
banco usaria, e o que acontece quando alguém opera pelo MySQL Workbench.
Se a regra estivesse só na aplicação, todos estes testes passariam
gravando reservas conflitantes.

Os 13 casos obrigatórios da Etapa 1 estão marcados com o identificador
CASO n no docstring de cada teste.
"""

import datetime as dt

import mysql.connector
import pytest

pytestmark = pytest.mark.banco

# SIGNAL SQLSTATE '45000' chega ao driver como erro 1644.
ERRO_SIGNAL = 1644


# ---------------------------------------------------------------------------
# Apoio
# ---------------------------------------------------------------------------

def criar_chale(cursor, nome="Embaré", capacidade=4):
    cursor.execute(
        "INSERT INTO chale (nome, capacidade) VALUES (%s, %s)",
        (nome, capacidade),
    )
    return cursor.lastrowid


def criar_hospede(cursor, semente=b"a"):
    """Cria um hóspede com bytes de preenchimento.

    O conteúdo não importa para estes testes: nenhum deles decifra nada.
    O que importa é que as colunas aceitem bytes e que o id exista para
    servir de chave estrangeira.
    """
    cursor.execute(
        "INSERT INTO hospede "
        "(nome_cif, nome_nonce, nome_tag, documento_cif, documento_nonce, "
        " documento_tag) VALUES (%s, %s, %s, %s, %s, %s)",
        (semente * 32, semente * 12, semente * 16,
         semente * 24, semente * 12, semente * 16),
    )
    return cursor.lastrowid


def criar_usuario(cursor, login="recepcao", perfil="recepcionista"):
    cursor.execute(
        "INSERT INTO usuario (login, nome, senha_hash, perfil) "
        "VALUES (%s, %s, %s, %s)",
        (login, "Fulano de Tal", "$argon2id$v=19$m=65536,t=3,p=4$abc$def", perfil),
    )
    return cursor.lastrowid


def reservar(cursor, chale, hospede, entrada, saida,
             pessoas=2, situacao="confirmada"):
    cursor.execute(
        "INSERT INTO reserva "
        "(chale_id, hospede_id, data_entrada, data_saida, num_pessoas, situacao) "
        "VALUES (%s, %s, %s, %s, %s, %s)",
        (chale, hospede, entrada, saida, pessoas, situacao),
    )
    return cursor.lastrowid


@pytest.fixture
def cenario(banco_com_triggers):
    """Um chalé para 4 pessoas, um hóspede e uma reserva de 10 a 15/12.

    Todos os testes de sobreposição se posicionam em relação a essa
    reserva, o que torna os casos legíveis sem repetir o preparo.
    """
    cursor = banco_com_triggers
    chale = criar_chale(cursor, "Embaré", 4)
    hospede = criar_hospede(cursor)
    reserva = reservar(cursor, chale, hospede, "2026-12-10", "2026-12-15")
    return {
        "cursor": cursor,
        "chale": chale,
        "hospede": hospede,
        "reserva": reserva,
    }


def _conta_reservas(cursor):
    cursor.execute("SELECT COUNT(*) FROM reserva")
    return cursor.fetchone()[0]


# ---------------------------------------------------------------------------
# Não-sobreposição
# ---------------------------------------------------------------------------

def test_reserva_sem_conflito_e_aceita(cenario):
    """CASO 1 - período livre, em outro mês."""
    reservar(cenario["cursor"], cenario["chale"], cenario["hospede"],
             "2027-01-05", "2027-01-10")
    assert _conta_reservas(cenario["cursor"]) == 2


def test_reserva_identica_e_bloqueada(cenario):
    """CASO 2 - mesmas datas, mesmo chalé."""
    with pytest.raises(mysql.connector.Error) as erro:
        reservar(cenario["cursor"], cenario["chale"], cenario["hospede"],
                 "2026-12-10", "2026-12-15")
    assert erro.value.errno == ERRO_SIGNAL
    assert "reserva ativa" in str(erro.value).lower()


def test_reserva_que_comeca_no_meio_de_outra_e_bloqueada(cenario):
    """CASO 3 - entra dia 12, enquanto a outra vai até 15."""
    with pytest.raises(mysql.connector.Error) as erro:
        reservar(cenario["cursor"], cenario["chale"], cenario["hospede"],
                 "2026-12-12", "2026-12-18")
    assert erro.value.errno == ERRO_SIGNAL


def test_reserva_que_termina_no_meio_de_outra_e_bloqueada(cenario):
    """Variante do CASO 3, pelo outro lado do intervalo."""
    with pytest.raises(mysql.connector.Error) as erro:
        reservar(cenario["cursor"], cenario["chale"], cenario["hospede"],
                 "2026-12-08", "2026-12-12")
    assert erro.value.errno == ERRO_SIGNAL


def test_reserva_que_engloba_outra_e_bloqueada(cenario):
    """CASO 4 - de 5 a 20 cobre inteiramente a reserva de 10 a 15."""
    with pytest.raises(mysql.connector.Error) as erro:
        reservar(cenario["cursor"], cenario["chale"], cenario["hospede"],
                 "2026-12-05", "2026-12-20")
    assert erro.value.errno == ERRO_SIGNAL


def test_reserva_contida_em_outra_e_bloqueada(cenario):
    """Variante do CASO 4 - inteiramente dentro da reserva existente."""
    with pytest.raises(mysql.connector.Error) as erro:
        reservar(cenario["cursor"], cenario["chale"], cenario["hospede"],
                 "2026-12-11", "2026-12-13")
    assert erro.value.errno == ERRO_SIGNAL


def test_entrada_no_dia_da_saida_de_outra_e_aceita(cenario):
    """CASO 5 - o teste decisivo do intervalo semiaberto.

    A reserva existente sai dia 15. Esta entra dia 15. Na hospedaria, o
    hóspede sai pela manhã e o próximo entra à tarde - o chalé não fica
    vago um dia inteiro à toa.

    Se a comparação usasse >= em vez de >, este teste falharia e o sistema
    bloquearia receita legítima.
    """
    reservar(cenario["cursor"], cenario["chale"], cenario["hospede"],
             "2026-12-15", "2026-12-20")
    assert _conta_reservas(cenario["cursor"]) == 2


def test_saida_no_dia_da_entrada_de_outra_e_aceita(cenario):
    """CASO 6 - o mesmo princípio, pelo outro lado."""
    reservar(cenario["cursor"], cenario["chale"], cenario["hospede"],
             "2026-12-05", "2026-12-10")
    assert _conta_reservas(cenario["cursor"]) == 2


def test_conflito_com_reserva_cancelada_e_aceito(cenario):
    """CASO 7 - reserva cancelada não ocupa o chalé."""
    cursor = cenario["cursor"]
    cursor.execute(
        "UPDATE reserva SET situacao = 'cancelada' WHERE id = %s",
        (cenario["reserva"],),
    )
    reservar(cursor, cenario["chale"], cenario["hospede"],
             "2026-12-10", "2026-12-15")
    assert _conta_reservas(cursor) == 2


def test_conflito_com_reserva_concluida_e_aceito(cenario):
    """Variante do CASO 7 - concluída também não ocupa."""
    cursor = cenario["cursor"]
    cursor.execute(
        "UPDATE reserva SET situacao = 'concluida' WHERE id = %s",
        (cenario["reserva"],),
    )
    reservar(cursor, cenario["chale"], cenario["hospede"],
             "2026-12-10", "2026-12-15")
    assert _conta_reservas(cursor) == 2


def test_reserva_cancelada_nao_e_barrada_por_conflito(cenario):
    """Registrar uma reserva já cancelada não deve ser impedido.

    A situação aparece ao importar histórico: o período conflita, mas a
    reserva não ocupa nada por já estar cancelada.
    """
    reservar(cenario["cursor"], cenario["chale"], cenario["hospede"],
             "2026-12-10", "2026-12-15", situacao="cancelada")
    assert _conta_reservas(cenario["cursor"]) == 2


def test_mesmo_periodo_em_chale_diferente_e_aceito(cenario):
    """A regra é por chalé, não global."""
    cursor = cenario["cursor"]
    outro = criar_chale(cursor, "Dunas", 4)
    reservar(cursor, outro, cenario["hospede"], "2026-12-10", "2026-12-15")
    assert _conta_reservas(cursor) == 2


# ---------------------------------------------------------------------------
# Capacidade e coerência de datas
# ---------------------------------------------------------------------------

def test_acima_da_capacidade_e_bloqueado(cenario):
    """CASO 8 - o chalé comporta 4 pessoas."""
    with pytest.raises(mysql.connector.Error) as erro:
        reservar(cenario["cursor"], cenario["chale"], cenario["hospede"],
                 "2027-02-01", "2027-02-05", pessoas=5)
    assert erro.value.errno == ERRO_SIGNAL
    assert "capacidade" in str(erro.value).lower()


def test_exatamente_na_capacidade_e_aceito(cenario):
    """O limite é inclusivo: 4 pessoas num chalé para 4 cabem."""
    reservar(cenario["cursor"], cenario["chale"], cenario["hospede"],
             "2027-02-01", "2027-02-05", pessoas=4)
    assert _conta_reservas(cenario["cursor"]) == 2


def test_zero_pessoas_e_bloqueado(cenario):
    """Reserva sem hóspede nenhum não é reserva."""
    with pytest.raises(mysql.connector.Error) as erro:
        reservar(cenario["cursor"], cenario["chale"], cenario["hospede"],
                 "2027-02-01", "2027-02-05", pessoas=0)
    assert erro.value.errno == ERRO_SIGNAL


def test_saida_anterior_a_entrada_e_bloqueada(cenario):
    """CASO 9, primeira metade."""
    with pytest.raises(mysql.connector.Error) as erro:
        reservar(cenario["cursor"], cenario["chale"], cenario["hospede"],
                 "2027-03-10", "2027-03-05")
    assert erro.value.errno == ERRO_SIGNAL
    assert "saída" in str(erro.value).lower()


def test_saida_igual_a_entrada_e_bloqueada(cenario):
    """CASO 9, segunda metade - reserva de zero noites não existe."""
    with pytest.raises(mysql.connector.Error) as erro:
        reservar(cenario["cursor"], cenario["chale"], cenario["hospede"],
                 "2027-03-10", "2027-03-10")
    assert erro.value.errno == ERRO_SIGNAL


# ---------------------------------------------------------------------------
# Alteração
# ---------------------------------------------------------------------------

def test_update_que_move_reserva_sobre_outra_e_bloqueado(cenario):
    """CASO 10 - a regra também vale na alteração.

    Sem trigger de UPDATE, bastaria criar uma reserva em período livre e
    depois movê-la para cima de outra.
    """
    cursor = cenario["cursor"]
    segunda = reservar(cursor, cenario["chale"], cenario["hospede"],
                       "2027-01-05", "2027-01-10")
    with pytest.raises(mysql.connector.Error) as erro:
        cursor.execute(
            "UPDATE reserva SET data_entrada = '2026-12-11', "
            "data_saida = '2026-12-14' WHERE id = %s",
            (segunda,),
        )
    assert erro.value.errno == ERRO_SIGNAL


def test_update_apenas_da_observacao_e_aceito(cenario):
    """CASO 11 - o risco registrado no plano do projeto.

    Sem a cláusula que exclui a própria linha da consulta de conflito, a
    reserva seria encontrada conflitando consigo mesma e qualquer alteração
    seria rejeitada - inclusive uma que não toca nas datas.
    """
    cursor = cenario["cursor"]
    cursor.execute(
        "UPDATE reserva SET observacoes = %s WHERE id = %s",
        ("Chegada após as 22h", cenario["reserva"]),
    )
    cursor.execute("SELECT observacoes FROM reserva WHERE id = %s",
                   (cenario["reserva"],))
    assert cursor.fetchone()[0] == "Chegada após as 22h"


def test_update_que_mantem_as_mesmas_datas_e_aceito(cenario):
    """Reescrever a reserva com as próprias datas não é conflito."""
    cursor = cenario["cursor"]
    cursor.execute(
        "UPDATE reserva SET data_entrada = '2026-12-10', "
        "data_saida = '2026-12-15', num_pessoas = 3 WHERE id = %s",
        (cenario["reserva"],),
    )
    cursor.execute("SELECT num_pessoas FROM reserva WHERE id = %s",
                   (cenario["reserva"],))
    assert cursor.fetchone()[0] == 3


def test_update_acima_da_capacidade_e_bloqueado(cenario):
    with pytest.raises(mysql.connector.Error) as erro:
        cenario["cursor"].execute(
            "UPDATE reserva SET num_pessoas = 9 WHERE id = %s",
            (cenario["reserva"],),
        )
    assert erro.value.errno == ERRO_SIGNAL


def test_cancelar_libera_o_periodo(cenario):
    """Cancelamento devolve o chalé ao mercado."""
    cursor = cenario["cursor"]
    cursor.execute("UPDATE reserva SET situacao = 'cancelada' WHERE id = %s",
                   (cenario["reserva"],))
    reservar(cursor, cenario["chale"], cenario["hospede"],
             "2026-12-12", "2026-12-14")
    assert _conta_reservas(cursor) == 2


# ---------------------------------------------------------------------------
# Auditoria
# ---------------------------------------------------------------------------

def _log(cursor):
    cursor.execute(
        "SELECT acao, tabela_afetada, registro_id, detalhe, usuario_id "
        "FROM log_auditoria ORDER BY id"
    )
    return cursor.fetchall()


def test_insercao_de_reserva_gera_auditoria(cenario):
    """CASO 12, parte 1."""
    registros = [r for r in _log(cenario["cursor"]) if r[1] == "reserva"]
    assert len(registros) == 1
    acao, tabela, registro_id, _, _ = registros[0]
    assert (acao, tabela, registro_id) == ("INSERT", "reserva", cenario["reserva"])


def test_insercao_de_hospede_gera_auditoria(cenario):
    """CASO 12, parte 2."""
    registros = [r for r in _log(cenario["cursor"]) if r[1] == "hospede"]
    assert len(registros) == 1
    assert registros[0][0] == "INSERT"


def test_alteracao_e_exclusao_geram_auditoria(cenario):
    cursor = cenario["cursor"]
    cursor.execute("UPDATE reserva SET observacoes = 'x' WHERE id = %s",
                   (cenario["reserva"],))
    cursor.execute("DELETE FROM reserva WHERE id = %s", (cenario["reserva"],))

    acoes = [r[0] for r in _log(cursor) if r[1] == "reserva"]
    assert acoes == ["INSERT", "UPDATE", "DELETE"]


def test_mudanca_de_situacao_e_registrada_no_detalhe(cenario):
    """A situação é categoria fechada, não dado pessoal - pode ir ao log."""
    cursor = cenario["cursor"]
    cursor.execute("UPDATE reserva SET situacao = 'cancelada' WHERE id = %s",
                   (cenario["reserva"],))
    detalhes = [r[3] for r in _log(cursor) if r[0] == "UPDATE"]
    assert any("confirmada" in d and "cancelada" in d for d in detalhes)


def test_log_nunca_contem_conteudo_cifrado(banco_com_triggers):
    """CASO 13 - a verificação mais importante da auditoria.

    Um log que copiasse o ciphertext seria uma segunda via do dado
    protegido, fora do controle da tabela original. Copiar os campos
    _busca seria ainda pior: o HMAC é determinístico, então duas linhas
    iguais denunciariam tratar-se do mesmo hóspede.

    Aqui o hóspede é criado com bytes reconhecíveis, e depois se procura
    qualquer vestígio deles no log inteiro.
    """
    cursor = banco_com_triggers
    marcador = b"SEGREDO-DO-HOSPEDE-NAO-PODE-VAZAR"

    cursor.execute(
        "INSERT INTO hospede "
        "(nome_cif, nome_nonce, nome_tag, documento_cif, documento_nonce, "
        " documento_tag, email_busca, documento_busca) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
        (marcador, b"n" * 12, b"t" * 16, marcador, b"m" * 12, b"u" * 16,
         b"E" * 32, b"D" * 32),
    )
    hospede = cursor.lastrowid
    cursor.execute("UPDATE hospede SET cripto_versao = 2 WHERE id = %s", (hospede,))
    cursor.execute("DELETE FROM hospede WHERE id = %s", (hospede,))

    cursor.execute("SELECT * FROM log_auditoria")
    colunas = [d[0] for d in cursor.description]
    linhas = cursor.fetchall()
    assert linhas, "nenhum registro de auditoria foi gerado"

    proibidos = [marcador.decode(), "E" * 32, "D" * 32]
    for linha in linhas:
        texto = " ".join(str(v) for v in linha)
        for proibido in proibidos:
            assert proibido not in texto, (
                f"vestígio de campo protegido no log: {dict(zip(colunas, linha))}"
            )


def test_autor_da_operacao_vem_da_variavel_de_sessao(cenario):
    """A trigger não conhece o usuário da aplicação, só o do banco.

    A aplicação declara quem está operando com SET @app_usuario_id logo
    após abrir a conexão, e a trigger lê essa variável.
    """
    cursor = cenario["cursor"]
    usuario = criar_usuario(cursor)
    cursor.execute("SET @app_usuario_id = %s", (usuario,))
    reservar(cursor, cenario["chale"], cenario["hospede"],
             "2027-04-01", "2027-04-05")

    cursor.execute(
        "SELECT usuario_id FROM log_auditoria "
        "WHERE tabela_afetada = 'reserva' ORDER BY id DESC LIMIT 1"
    )
    assert cursor.fetchone()[0] == usuario
    cursor.execute("SET @app_usuario_id = NULL")


def test_sem_variavel_de_sessao_o_log_grava_nulo(cenario):
    """O evento fica registrado de qualquer forma, apenas sem o vínculo."""
    cursor = cenario["cursor"]
    cursor.execute("SET @app_usuario_id = NULL")
    reservar(cursor, cenario["chale"], cenario["hospede"],
             "2027-05-01", "2027-05-05")
    cursor.execute(
        "SELECT usuario_id FROM log_auditoria ORDER BY id DESC LIMIT 1"
    )
    assert cursor.fetchone()[0] is None


def test_usuario_inexistente_na_sessao_nao_derruba_a_operacao(cenario):
    """Auditoria não pode impedir o trabalho que ela observa.

    Sem a verificação de existência dentro da trigger, uma variável de
    sessão apontando para um usuário que não existe produziria violação de
    chave estrangeira e abortaria a reserva inteira.
    """
    cursor = cenario["cursor"]
    cursor.execute("SET @app_usuario_id = 999999")
    reservar(cursor, cenario["chale"], cenario["hospede"],
             "2027-06-01", "2027-06-05")
    cursor.execute("SELECT usuario_id FROM log_auditoria ORDER BY id DESC LIMIT 1")
    assert cursor.fetchone()[0] is None
    cursor.execute("SET @app_usuario_id = NULL")


# ---------------------------------------------------------------------------
# Carimbo de tempo
# ---------------------------------------------------------------------------

def test_carimbo_e_preenchido_na_insercao(banco_com_triggers):
    """As colunas são NOT NULL e o INSERT não as informa."""
    cursor = banco_com_triggers
    criar_chale(cursor, "Camburi", 4)
    cursor.execute("SELECT criado_em, alterado_em FROM chale")
    criado, alterado = cursor.fetchone()
    assert isinstance(criado, dt.datetime)
    assert isinstance(alterado, dt.datetime)


def test_data_de_criacao_e_imutavel(banco_com_triggers):
    """Reescrever quando o registro nasceu apagaria o rastro."""
    cursor = banco_com_triggers
    chale = criar_chale(cursor, "Asturias", 4)
    cursor.execute("SELECT criado_em FROM chale WHERE id = %s", (chale,))
    original = cursor.fetchone()[0]

    cursor.execute(
        "UPDATE chale SET criado_em = '2000-01-01 00:00:00', capacidade = 3 "
        "WHERE id = %s",
        (chale,),
    )
    cursor.execute("SELECT criado_em, capacidade FROM chale WHERE id = %s",
                   (chale,))
    depois, capacidade = cursor.fetchone()
    assert depois == original, "a trigger deixou reescrever a data de criação"
    assert capacidade == 3, "a alteração legítima não foi aplicada"


# ---------------------------------------------------------------------------
# Regras das demais tabelas
# ---------------------------------------------------------------------------

def test_tarifa_com_periodo_invertido_e_bloqueada(banco_com_triggers):
    cursor = banco_com_triggers
    chale = criar_chale(cursor, "Boracéia", 4)
    with pytest.raises(mysql.connector.Error) as erro:
        cursor.execute(
            "INSERT INTO tarifa (chale_id, data_inicio, data_fim, valor_diaria) "
            "VALUES (%s, '2027-01-31', '2027-01-01', 300.00)",
            (chale,),
        )
    assert erro.value.errno == ERRO_SIGNAL


def test_tarifa_com_valor_negativo_e_bloqueada(banco_com_triggers):
    cursor = banco_com_triggers
    chale = criar_chale(cursor, "Boracéia", 4)
    with pytest.raises(mysql.connector.Error) as erro:
        cursor.execute(
            "INSERT INTO tarifa (chale_id, data_inicio, data_fim, valor_diaria) "
            "VALUES (%s, '2027-01-01', '2027-01-31', -50.00)",
            (chale,),
        )
    assert erro.value.errno == ERRO_SIGNAL


def test_usuario_hospede_sem_vinculo_e_bloqueado(banco_com_triggers):
    """Sem vínculo, a tela de "minhas reservas" não saberia o que mostrar."""
    with pytest.raises(mysql.connector.Error) as erro:
        criar_usuario(banco_com_triggers, "visitante", "hospede")
    assert erro.value.errno == ERRO_SIGNAL


def test_administrador_com_vinculo_de_hospede_e_bloqueado(banco_com_triggers):
    """O inverso é pior: daria ao administrador as reservas de outra pessoa."""
    cursor = banco_com_triggers
    hospede = criar_hospede(cursor)
    with pytest.raises(mysql.connector.Error) as erro:
        cursor.execute(
            "INSERT INTO usuario (login, nome, senha_hash, perfil, hospede_id) "
            "VALUES ('chefe', 'Chefe', '$argon2id$x', 'administrador', %s)",
            (hospede,),
        )
    assert erro.value.errno == ERRO_SIGNAL


def test_usuario_hospede_com_vinculo_e_aceito(banco_com_triggers):
    cursor = banco_com_triggers
    hospede = criar_hospede(cursor)
    cursor.execute(
        "INSERT INTO usuario (login, nome, senha_hash, perfil, hospede_id) "
        "VALUES ('joao', 'João', '$argon2id$x', 'hospede', %s)",
        (hospede,),
    )
    cursor.execute("SELECT COUNT(*) FROM usuario")
    assert cursor.fetchone()[0] == 1


# ---------------------------------------------------------------------------
# Critério de conclusão da Etapa 1
# ---------------------------------------------------------------------------

def test_insercao_direta_no_banco_e_rejeitada(cenario):
    """O teste que prova o argumento central do projeto.

    Este INSERT não passa por Flask, por repositório, por validação Python.
    É SQL cru, o mesmo que seria digitado no MySQL Workbench. A trigger o
    rejeita com mensagem explicativa.

    Se a regra morasse na aplicação, este teste gravaria uma reserva
    conflitante sem nenhum obstáculo.
    """
    cursor = cenario["cursor"]
    antes = _conta_reservas(cursor)

    with pytest.raises(mysql.connector.Error) as erro:
        cursor.execute(
            f"INSERT INTO reserva "
            f"(chale_id, hospede_id, data_entrada, data_saida, num_pessoas, situacao) "
            f"VALUES ({cenario['chale']}, {cenario['hospede']}, "
            f"'2026-12-12', '2026-12-13', 2, 'confirmada')"
        )

    assert erro.value.errno == ERRO_SIGNAL
    assert _conta_reservas(cursor) == antes, "a reserva conflitante foi gravada"
