"""Os relatórios — Etapa 5, Bloco 18a (RF06 e RF12).

O cenário é março de 2030, longe de qualquer outra reserva, montado por SQL
para que cada número possa ser conferido à mão:

    Chalé A  confirmada  25/02 a 05/03  -> 4 noites dentro (01 a 04/03)
             concluída   10/03 a 15/03  -> 5 noites
             pendente    20/03 a 22/03  -> não ocupa (pode cair no Pix)
    Chalé B  cancelada pela pousada 01/03 a 08/03 -> não ocupa
             confirmada  28/03 a 03/04  -> 4 noites dentro (28 a 31/03)

Março tem 31 noites por chalé.
"""

import datetime as dt
from decimal import Decimal

import pytest

from app.dados import conexao
from app.servicos import relatorios

from test_pedido_do_hospede import (  # noqa: F401
    DADOS,
    SENHA,
    _entrar,
    _sql,
    aplicacao,
    conta,
)

pytestmark = pytest.mark.banco

ADMIN = ("admin", "Villa@Admin2026")
RECEPCAO = ("recepcao", "Villa@Recep2026")
MARCO = {"de": "2030-03-01", "ate": "2030-03-31"}


def _chales(aplicacao):
    with aplicacao.app_context():
        with conexao.obter_cursor() as cursor:
            cursor.execute("SELECT id, nome FROM chale WHERE ativo = TRUE ORDER BY nome")
            return cursor.fetchall()


def _reservar(aplicacao, chale_id, hospede_id, entrada, saida, situacao,
              motivo=None):
    _sql(aplicacao,
         "INSERT INTO reserva (chale_id, hospede_id, data_entrada, data_saida, "
         " num_pessoas, situacao, motivo_cancelamento, cancelado_em) "
         "VALUES (%s, %s, %s, %s, 1, %s, %s, %s)",
         (chale_id, hospede_id, entrada, saida, situacao, motivo,
          dt.datetime(2030, 2, 1) if situacao == "cancelada" else None))


@pytest.fixture
def cenario(aplicacao, conta):
    (a, nome_a), (b, nome_b) = _chales(aplicacao)[:2]
    h = conta["hospede_id"]
    _reservar(aplicacao, a, h, "2030-02-25", "2030-03-05", "confirmada")
    _reservar(aplicacao, a, h, "2030-03-10", "2030-03-15", "concluida")
    _reservar(aplicacao, a, h, "2030-03-20", "2030-03-22", "pendente")
    _reservar(aplicacao, b, h, "2030-03-01", "2030-03-08", "cancelada", "equipe")
    _reservar(aplicacao, b, h, "2030-03-28", "2030-04-03", "confirmada")
    return {"a": nome_a, "b": nome_b}


def _montar(aplicacao, **periodo):
    with aplicacao.app_context():
        return relatorios.montar(periodo.get("de"), periodo.get("ate"))


# ===========================================================================
# 1. O período
# ===========================================================================

def test_sem_periodo_vale_o_mes_corrente():
    de, ate, fim = relatorios.periodo(hoje=dt.date(2026, 2, 14))
    assert (de, ate, fim) == (dt.date(2026, 2, 1), dt.date(2026, 2, 28),
                              dt.date(2026, 3, 1))


def test_as_duas_datas_entram():
    de, ate, fim = relatorios.periodo("2030-03-01", "2030-03-01")
    assert (fim - de).days == 1


@pytest.mark.parametrize("de,ate,trecho", [
    ("2030-03-10", "2030-03-01", "igual ou posterior"),
    ("2030-01-01", "2031-01-02", "o máximo é 366"),
    ("ontem", "2030-03-01", "data inicial"),
])
def test_periodo_invalido_e_recusado(de, ate, trecho):
    with pytest.raises(relatorios.ErroDeRelatorio) as erro:
        relatorios.periodo(de, ate)
    assert trecho in str(erro.value)


def test_a_taxa_arredonda_meio_para_cima():
    assert relatorios._taxa(1, 8) == Decimal("12.5")
    assert relatorios._taxa(1, 3) == Decimal("33.3")
    assert relatorios._taxa(2, 3) == Decimal("66.7")
    assert relatorios._taxa(0, 0) == Decimal("0.0")


# ===========================================================================
# 2. Ocupação
# ===========================================================================

def test_ocupacao_conta_so_as_noites_dentro_do_periodo(aplicacao, cenario):
    dados = _montar(aplicacao, **MARCO)
    por_nome = {c["nome"]: c for c in dados["chales"]}
    assert por_nome[cenario["a"]]["noites"] == 9, "4 da que entra antes + 5"
    assert por_nome[cenario["b"]]["noites"] == 4, "só as que caem em março"
    assert por_nome[cenario["a"]]["possiveis"] == 31
    assert por_nome[cenario["a"]]["taxa"] == Decimal("29.0")


def test_pendente_e_cancelada_nao_ocupam(aplicacao, cenario):
    dados = _montar(aplicacao, de="2030-03-01", ate="2030-03-07")
    por_nome = {c["nome"]: c for c in dados["chales"]}
    assert por_nome[cenario["b"]]["noites"] == 0
    dados = _montar(aplicacao, de="2030-03-20", ate="2030-03-21")
    assert sum(c["noites"] for c in dados["chales"]) == 0


def test_todo_chale_ativo_aparece_mesmo_vazio(aplicacao, cenario):
    dados = _montar(aplicacao, **MARCO)
    assert len(dados["chales"]) == len(_chales(aplicacao))
    assert dados["total"]["possiveis"] == 31 * len(_chales(aplicacao))
    assert dados["total"]["noites"] == 13


# ===========================================================================
# 3. Reservas e mensagens
# ===========================================================================

def test_reservas_por_situacao_e_motivo(aplicacao, cenario):
    linhas = {r["rotulo"]: r["total"] for r in _montar(aplicacao, **MARCO)["reservas"]}
    assert linhas["Confirmadas"] == 2
    assert linhas["Concluídas"] == 1
    assert linhas["Aguardando pagamento"] == 1
    assert linhas["Canceladas pela pousada"] == 1
    assert linhas["Canceladas a pedido do hóspede"] == 0
    assert "Canceladas sem motivo registrado" not in linhas
    assert _montar(aplicacao, **MARCO)["total_de_reservas"] == 5


def test_cancelada_antiga_sem_motivo_aparece_a_parte(aplicacao, conta):
    chale_id = _chales(aplicacao)[0][0]
    _reservar(aplicacao, chale_id, conta["hospede_id"], "2030-03-01",
              "2030-03-03", "cancelada")
    linhas = {r["rotulo"]: r["total"] for r in _montar(aplicacao, **MARCO)["reservas"]}
    assert linhas["Canceladas sem motivo registrado"] == 1


def test_mensagens_contadas_por_quem_escreveu(aplicacao, cenario):
    from app.dados import mensagem_repo

    with aplicacao.app_context():
        with conexao.obter_cursor() as cursor:
            cursor.execute("SELECT id, hospede_id FROM reserva LIMIT 1")
            reserva_id, hospede_id = cursor.fetchone()
            cursor.execute("SELECT id FROM usuario WHERE hospede_id = %s", (hospede_id,))
            autor = cursor.fetchone()[0]
        mensagem_repo.enviar_do_hospede(reserva_id, hospede_id, autor, "Olá")
        mensagem_repo.enviar_do_hospede(reserva_id, hospede_id, autor, "Tudo bem?")
        mensagem_repo.enviar_da_equipe(reserva_id, 1, "Tudo.")
        hoje = dt.date.today().isoformat()
        dados = relatorios.montar(hoje, hoje)
    assert dados["mensagens"] == {"dos_hospedes": 2, "da_equipe": 1}


# ===========================================================================
# 4. A tela
# ===========================================================================

@pytest.mark.parametrize("credenciais", [ADMIN, RECEPCAO])
def test_administrador_e_recepcao_veem(aplicacao, cenario, credenciais):
    cliente = _entrar(aplicacao, *credenciais)
    html = cliente.get("/relatorios?de=2030-03-01&ate=2030-03-31").data.decode()
    assert "Ocupação por chalé" in html and "29,0%" in html
    assert 'href="/relatorios"' in cliente.get("/painel").data.decode()


def test_o_relatorio_nao_mostra_quem(aplicacao, cenario):
    html = _entrar(aplicacao, *ADMIN).get(
        "/relatorios?de=2030-03-01&ate=2030-03-31").data.decode()
    assert DADOS["nome"] not in html


def test_hospede_e_visitante_nao_entram(aplicacao, conta):
    assert _entrar(aplicacao, DADOS["email"], SENHA).get("/relatorios").status_code == 403
    assert aplicacao.test_client().get("/relatorios").status_code in (302, 401)


def test_periodo_invalido_na_tela_devolve_400_com_a_frase(aplicacao):
    resposta = _entrar(aplicacao, *ADMIN).get("/relatorios?de=2030-03-10&ate=2030-03-01")
    assert resposta.status_code == 400
    assert "igual ou posterior" in resposta.data.decode()


def test_sem_periodo_abre_no_mes_corrente(aplicacao):
    html = _entrar(aplicacao, *RECEPCAO).get("/relatorios").data.decode()
    primeiro = dt.date.today().replace(day=1).isoformat()
    assert f'value="{primeiro}"' in html
