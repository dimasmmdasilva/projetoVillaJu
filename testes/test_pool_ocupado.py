"""Relatório 2, achado R2-03 — pool de conexões esgotado.

Na análise dinâmica de 23/11, o rastreador do ZAP fez vários pedidos ao
mesmo tempo e 14 páginas responderam "erro inesperado" (500): o pool do
mysql-connector não espera, e o pedido além do tamanho do pool falhava
na hora. A correção: o pedido espera uma conexão ser devolvida, por até
`ESPERA_PELO_POOL_SEGUNDOS`; se nenhuma se liberar, a resposta é 503 —
"sistema ocupado, tente em instantes" —, não 500. E o pool passou de 5
para 10.

Os testes tomam TODAS as conexões do pool de propósito, como um pico.
"""

import threading
import time

import mysql.connector
import pytest

from app import create_app
from app.config import Configuracao
from app.dados import conexao



def _pedir(aplicacao):
    """Um login: vai ao banco e NÃO trata erro de banco por conta própria.

    A escolha importa. A vitrine (`/`) mostra a página sem a lista de
    chalés quando o banco falha, e a página do chalé já tinha o próprio
    503 — com qualquer uma das duas, os testes passavam sem o tratador
    novo, e duas sabotagens provaram isso. O login cai no tratador geral.
    """
    return aplicacao.test_client().post(
        "/login", data={"login": "admin", "senha": "Villa@Admin2026"})


@pytest.fixture(scope="module")
def aplicacao(conexao_banco):
    from conftest import (
        SCRIPT_DADOS,
        SCRIPT_SCHEMA,
        SCRIPT_TRIGGERS,
        aplicar_script,
        limpar_banco,
    )

    cursor = conexao_banco.cursor()
    limpar_banco(cursor)
    aplicar_script(cursor, SCRIPT_SCHEMA)
    aplicar_script(cursor, SCRIPT_TRIGGERS)
    aplicar_script(cursor, SCRIPT_DADOS)
    cursor.close()

    app = create_app(Configuracao(modo_teste=True))
    yield app
    conexao.encerrar_pool()


@pytest.fixture
def pool_tomado(aplicacao, monkeypatch):
    """Empresta todas as conexões do pool; devolve no fim do teste."""
    monkeypatch.setattr(conexao, "ESPERA_PELO_POOL_SEGUNDOS", 0.5)
    tomadas = []
    while True:
        try:
            tomadas.append(conexao._exigir_pool().get_connection())
        except mysql.connector.errors.PoolError:
            break
    assert tomadas, "o pool precisa ter tido conexões para o teste dizer algo"
    yield tomadas
    for c in tomadas:
        c.close()


def test_sem_pico_a_pagina_abre(aplicacao):
    """O controle: a página escolhida usa o banco e abre normalmente."""
    assert _pedir(aplicacao).status_code == 302


def test_pool_esgotado_responde_503_e_nao_500(aplicacao, pool_tomado):
    resposta = _pedir(aplicacao)
    assert resposta.status_code == 503
    assert "ocupado" in resposta.get_data(as_text=True)


def test_a_pagina_de_ocupado_nao_mostra_detalhe_interno(aplicacao, pool_tomado):
    corpo = _pedir(aplicacao).get_data(as_text=True)
    for interno in ("Traceback", "PoolError", "exhausted", "mysql", "conexao.py"):
        assert interno not in corpo, interno


def test_o_pedido_espera_uma_conexao_ser_devolvida(aplicacao, pool_tomado):
    """O coração da correção: antes, falhava NA HORA mesmo que uma conexão
    fosse devolvida um instante depois."""
    devolvida = pool_tomado.pop()
    threading.Timer(0.2, devolvida.close).start()

    assert _pedir(aplicacao).status_code == 302


def test_desiste_no_prazo_e_nao_fica_preso(aplicacao, pool_tomado):
    inicio = time.monotonic()
    _pedir(aplicacao)
    decorrido = time.monotonic() - inicio
    assert 0.5 <= decorrido < 3, decorrido


def test_o_pool_padrao_tem_dez_conexoes(monkeypatch):
    monkeypatch.delenv("VILLAJU_DB_POOL", raising=False)
    assert Configuracao(modo_teste=True).banco_tamanho_do_pool == 10


def test_banco_ocupado_e_um_erro_de_conexao():
    """Quem já trata ErroDeConexao continua tratando o novo caso."""
    assert issubclass(conexao.BancoOcupado, conexao.ErroDeConexao)
