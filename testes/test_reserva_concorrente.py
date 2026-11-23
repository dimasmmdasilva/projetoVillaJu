"""Achado R2-06 — reserva dupla por concorrência.

Em 23/11, ao revisar os gatilhos para o Relatório Técnico, um experimento
com duas conexões mostrou: duas reservas do mesmo chalé e período, de
hóspedes diferentes, gravadas AO MESMO TEMPO, passavam as duas. Uma depois
da outra, a segunda era recusada. O gatilho contava os conflitos com uma
leitura comum, que no isolamento padrão do MySQL (REPEATABLE READ) não
trava nada e não vê o que outra transação ainda não confirmou.

A correção, nos gatilhos de reserva: travar a linha do chalé (FOR UPDATE)
e contar com leitura travada (FOR SHARE). Na alteração, só quando a
ocupação muda — confirmar o Pix ou cancelar não esperam trava nenhuma.

Estes testes usam DUAS conexões de verdade, com transação manual, e uma
thread para o pedido que chega "ao mesmo tempo". Nenhum teste de uma
conexão só consegue mostrar o defeito.
"""

import threading
import time

import mysql.connector
import pytest

from app.dados import conexao as conexao_app
from test_triggers import criar_chale, criar_hospede, reservar

ESPERA_DA_TRAVA = 5  # segundos; só para o teste não ficar preso se der errado


def _conexao():
    from conftest import _configuracao

    cfg = _configuracao()
    conexao = mysql.connector.connect(
        host=cfg["VILLAJU_DB_HOST"],
        port=int(cfg["VILLAJU_DB_PORTA"]),
        user=cfg["VILLAJU_DB_USUARIO_TESTE"],
        password=cfg["VILLAJU_DB_SENHA_TESTE"],
        database=cfg["VILLAJU_DB_NOME_TESTE"],
        autocommit=False,
    )
    cursor = conexao.cursor()
    cursor.execute(f"SET SESSION innodb_lock_wait_timeout = {ESPERA_DA_TRAVA}")
    # O mesmo nível que a aplicação fixa em cada conexão do pool
    # (app/dados/conexao.py) — conferido no último teste deste arquivo.
    cursor.execute(conexao_app._SQL_ISOLAMENTO)
    cursor.close()
    return conexao


INSERIR = (
    "INSERT INTO reserva "
    "(chale_id, hospede_id, data_entrada, data_saida, num_pessoas, situacao) "
    "VALUES (%s, %s, %s, %s, 2, 'pendente')"
)


class _Pedido(threading.Thread):
    """O segundo pedido: roda numa conexão própria e guarda o resultado."""

    def __init__(self, comando, parametros):
        super().__init__(daemon=True)
        self.comando, self.parametros = comando, parametros
        self.erro = None
        self.conexao = _conexao()

    def run(self):
        cursor = self.conexao.cursor()
        try:
            cursor.execute(self.comando, self.parametros)
            self.conexao.commit()
        except mysql.connector.Error as erro:
            self.erro = erro
            self.conexao.rollback()
        finally:
            cursor.close()
            self.conexao.close()


@pytest.fixture
def cenario(banco_com_triggers):
    cursor = banco_com_triggers
    return {
        "cursor": cursor,
        "chale": criar_chale(cursor, "Embaré", 4),
        "outro_chale": criar_chale(cursor, "Dunas", 4),
        "hospedes": [criar_hospede(cursor, s) for s in (b"a", b"b", b"c")],
    }


def _reservas(cursor, chale):
    cursor.execute(
        "SELECT hospede_id FROM reserva WHERE chale_id = %s "
        "AND situacao IN ('pendente', 'confirmada')", (chale,))
    return [linha[0] for linha in cursor.fetchall()]


def test_duas_reservas_simultaneas_do_mesmo_chale_nao_passam_as_duas(cenario):
    """O defeito exato do experimento de 23/11."""
    chale, (h1, h2, _) = cenario["chale"], cenario["hospedes"]
    primeira = _conexao()
    try:
        primeira.cursor().execute(INSERIR, (chale, h1, "2031-05-10", "2031-05-15"))

        segunda = _Pedido(INSERIR, (chale, h2, "2031-05-12", "2031-05-14"))
        segunda.start()
        time.sleep(0.5)
        assert segunda.is_alive(), "a segunda precisa ESPERAR a trava do chalé"

        primeira.commit()
        segunda.join(ESPERA_DA_TRAVA + 2)
    finally:
        primeira.close()

    assert segunda.erro is not None and segunda.erro.errno == 1644, segunda.erro
    assert "Já existe reserva ativa" in segunda.erro.msg
    assert _reservas(cenario["cursor"], chale) == [h1]


def test_se_a_primeira_desiste_a_segunda_entra(cenario):
    """A trava não recusa ninguém por si: só faz esperar a vez."""
    chale, (h1, h2, _) = cenario["chale"], cenario["hospedes"]
    primeira = _conexao()
    try:
        primeira.cursor().execute(INSERIR, (chale, h1, "2031-05-10", "2031-05-15"))
        segunda = _Pedido(INSERIR, (chale, h2, "2031-05-10", "2031-05-15"))
        segunda.start()
        time.sleep(0.5)
        primeira.rollback()
        segunda.join(ESPERA_DA_TRAVA + 2)
    finally:
        primeira.close()

    assert segunda.erro is None, segunda.erro
    assert _reservas(cenario["cursor"], chale) == [h2]


def test_chales_diferentes_nao_esperam_um_pelo_outro(cenario):
    chale, outro, (h1, h2, _) = cenario["chale"], cenario["outro_chale"], cenario["hospedes"]
    primeira = _conexao()
    try:
        primeira.cursor().execute(INSERIR, (chale, h1, "2031-05-10", "2031-05-15"))
        inicio = time.monotonic()
        segunda = _Pedido(INSERIR, (outro, h2, "2031-05-10", "2031-05-15"))
        segunda.start()
        segunda.join(ESPERA_DA_TRAVA + 2)
        decorrido = time.monotonic() - inicio
        primeira.commit()
    finally:
        primeira.close()

    assert segunda.erro is None, segunda.erro
    assert decorrido < 1, f"esperou {decorrido:.1f} s por outro chalé"


def test_confirmar_o_pix_durante_uma_reserva_nova_do_chale_passa_sem_impasse(cenario):
    """Confirmar o Pix de uma reserva espera a reserva nova do MESMO chalé
    terminar — alguns milissegundos no uso real — e então passa.

    A espera não é dos gatilhos: o InnoDB confere a chave estrangeira
    reserva → chalé ao alterar a reserva, e o chalé está travado pela
    inclusão. Ela já existia antes do R2-06, para qualquer operação que
    segurasse o chalé. O que importa: a confirmação não pega a trava do
    chalé, então não forma impasse com a inclusão, e não falha.
    """
    cursor, chale, (h1, h2, _) = cenario["cursor"], cenario["chale"], cenario["hospedes"]
    existente = reservar(cursor, chale, h1, "2031-06-01", "2031-06-05", situacao="pendente")

    primeira = _conexao()
    try:
        primeira.cursor().execute(INSERIR, (chale, h2, "2031-07-01", "2031-07-05"))
        confirmar = _Pedido(
            "UPDATE reserva SET situacao = 'confirmada' WHERE id = %s", (existente,))
        confirmar.start()
        time.sleep(0.3)
        primeira.commit()
        confirmar.join(ESPERA_DA_TRAVA + 2)
    finally:
        primeira.close()

    assert confirmar.erro is None, confirmar.erro
    cursor.execute("SELECT situacao FROM reserva WHERE id = %s", (existente,))
    assert cursor.fetchone()[0] == "confirmada"
    assert sorted(_reservas(cursor, chale)) == sorted([h1, h2])


def test_quem_leu_antes_de_esperar_a_fila_ainda_ve_a_reserva_confirmada(cenario):
    """Por que READ COMMITTED é necessário, e não só a trava do chalé.

    A aplicação lê o banco antes de gravar a reserva (tarifas, o próprio
    hóspede). Em REPEATABLE READ, a primeira leitura fixa um retrato do
    banco para a transação inteira: depois de esperar a fila do chalé, a
    contagem do gatilho ainda usaria o retrato velho, sem a reserva que
    passou na frente — e a reserva dupla voltava. Em READ COMMITTED, cada
    leitura vê o que já foi confirmado.
    """
    chale, (h1, h2, _) = cenario["chale"], cenario["hospedes"]
    primeira, segunda = _conexao(), _conexao()
    try:
        leitura = segunda.cursor()
        leitura.execute("SELECT COUNT(*) FROM reserva")  # fixaria o retrato em REPEATABLE READ
        leitura.fetchall()

        primeira.cursor().execute(INSERIR, (chale, h1, "2031-09-10", "2031-09-15"))
        resultado = {}

        def gravar():
            try:
                segunda.cursor().execute(INSERIR, (chale, h2, "2031-09-11", "2031-09-13"))
                segunda.commit()
            except mysql.connector.Error as erro:
                resultado["erro"] = erro
                segunda.rollback()

        fio = threading.Thread(target=gravar, daemon=True)
        fio.start()
        time.sleep(0.5)
        primeira.commit()
        fio.join(ESPERA_DA_TRAVA + 2)
    finally:
        primeira.close()
        segunda.close()

    assert "erro" in resultado and resultado["erro"].errno == 1644, resultado
    assert _reservas(cenario["cursor"], chale) == [h1]


def test_confirmar_o_pix_nao_pega_a_trava_exclusiva_do_chale(cenario):
    """Só a alteração que muda a ocupação entra na fila do chalé.

    Com a confirmação aberta (sem commit), outra conexão ainda consegue
    uma trava COMPARTILHADA no chalé — o que seria impossível se a
    confirmação tivesse pego a exclusiva. NOWAIT responde na hora.
    """
    cursor, chale, (h1, _, _) = cenario["cursor"], cenario["chale"], cenario["hospedes"]
    existente = reservar(cursor, chale, h1, "2031-06-01", "2031-06-05", situacao="pendente")

    confirmacao, outra = _conexao(), _conexao()
    try:
        confirmacao.cursor().execute(
            "UPDATE reserva SET situacao = 'confirmada' WHERE id = %s", (existente,))
        leitura = outra.cursor()
        leitura.execute("SELECT id FROM chale WHERE id = %s FOR SHARE NOWAIT", (chale,))
        assert leitura.fetchall()
    finally:
        confirmacao.rollback()
        outra.rollback()
        confirmacao.close()
        outra.close()


def test_mudar_as_datas_pega_a_trava_exclusiva_do_chale(cenario):
    """O contraste do teste acima: mudar as datas entra na fila."""
    cursor, chale, (h1, _, _) = cenario["cursor"], cenario["chale"], cenario["hospedes"]
    existente = reservar(cursor, chale, h1, "2031-06-01", "2031-06-05", situacao="pendente")

    mudanca, outra = _conexao(), _conexao()
    try:
        mudanca.cursor().execute(
            "UPDATE reserva SET data_saida = '2031-06-06' WHERE id = %s", (existente,))
        with pytest.raises(mysql.connector.Error) as erro:
            outra.cursor().execute(
                "SELECT id FROM chale WHERE id = %s FOR SHARE NOWAIT", (chale,))
        assert erro.value.errno == 3572  # "lock NOWAIT is set" — a trava existe
    finally:
        mudanca.rollback()
        outra.rollback()
        mudanca.close()
        outra.close()


def test_mudar_as_datas_para_cima_de_outra_simultanea_e_recusado(cenario):
    """A alteração que muda a ocupação entra na fila do chalé."""
    cursor, chale, (h1, h2, _) = cenario["cursor"], cenario["chale"], cenario["hospedes"]
    existente = reservar(cursor, chale, h1, "2031-06-01", "2031-06-05", situacao="pendente")

    primeira = _conexao()
    try:
        primeira.cursor().execute(INSERIR, (chale, h2, "2031-08-10", "2031-08-15"))
        mover = _Pedido(
            "UPDATE reserva SET data_entrada = '2031-08-11', data_saida = '2031-08-14' "
            "WHERE id = %s", (existente,))
        mover.start()
        time.sleep(0.5)
        assert mover.is_alive(), "mudar as datas precisa esperar a trava do chalé"
        primeira.commit()
        mover.join(ESPERA_DA_TRAVA + 2)
    finally:
        primeira.close()

    assert mover.erro is not None and mover.erro.errno == 1644, mover.erro


@pytest.mark.parametrize("numero", [1213, 1205])
def test_impasse_e_espera_viram_mensagem_clara(numero):
    from app.dados import erros

    falso = mysql.connector.errors.DatabaseError(msg="x", errno=numero)
    traduzido = erros.traduzir(falso)
    assert "tente de novo" in str(traduzido)
    assert "Nada foi gravado" in str(traduzido)


def test_a_aplicacao_usa_read_committed_em_toda_conexao(conexao_banco):
    """Sem isto, os testes acima passariam com as conexões DELES em READ
    COMMITTED, e a aplicação seguiria em REPEATABLE READ — esperando
    trava de outro chalé e da confirmação do Pix."""
    from app.config import Configuracao

    conexao_app.iniciar_pool(Configuracao(modo_teste=True))
    try:
        for _ in range(3):  # o pool zera a sessão a cada devolução
            with conexao_app.obter_cursor() as cursor:
                cursor.execute("SELECT @@transaction_isolation")
                assert cursor.fetchone()[0] == "READ-COMMITTED"
    finally:
        conexao_app.encerrar_pool()
