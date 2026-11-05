"""Cálculo do valor total da estadia.

O plano aponta esta como a parte mais sujeita a erro da Etapa 3. O método
escolhido é percorrer **uma noite por vez**, somando a tarifa vigente em
cada data — com isso a virada de temporada deixa de ser caso especial.

Os testes conferem cada resultado contra a conta feita à mão, escrita no
próprio teste. Um cálculo que só é comparado consigo mesmo não prova nada.

Tarifas da massa de dados usadas como cenário:

    Camburi   01/03/2026 a 14/12/2026   R$ 300,00   baixa
    Camburi   15/12/2026 a 28/02/2027   R$ 540,00   alta
    Embaré    01/03/2026 a 14/12/2026   R$ 320,00   baixa
    Embaré    15/12/2026 a 28/02/2027   R$ 580,00   alta
"""

import datetime as dt
from decimal import Decimal

import pytest

from app import create_app
from app.config import Configuracao
from app.dados import calculo, conexao, reserva_repo
from app.dados.calculo import TarifaAusente

pytestmark = pytest.mark.banco

CAMBURI = 2
BAIXA = Decimal("300.00")
ALTA = Decimal("540.00")

# A virada acontece em 15/12/2026: a baixa vai até 14, a alta começa no 15.
VIRADA = dt.date(2026, 12, 15)


@pytest.fixture
def banco(conexao_banco):
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

    conexao.iniciar_pool(Configuracao(modo_teste=True))
    yield cursor
    conexao.encerrar_pool()


# ---------------------------------------------------------------------------
# Quantas noites tem uma estadia
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "entrada,saida,esperado",
    [
        ("2026-06-01", "2026-06-02", 1),
        ("2026-06-01", "2026-06-06", 5),
        ("2026-06-01", "2026-07-01", 30),
    ],
)
def test_noites_usa_intervalo_semiaberto(entrada, saida, esperado):
    """Quem sai dia 18 não dorme na noite do dia 18.

    É o mesmo intervalo que a trigger usa para decidir sobreposição. Usar
    outro aqui produziria uma conta que não corresponde à ocupação.
    """
    inicio = dt.date.fromisoformat(entrada)
    fim = dt.date.fromisoformat(saida)
    assert len(calculo.noites(inicio, fim)) == esperado


def test_noites_comeca_na_entrada_e_termina_na_vespera_da_saida():
    datas = calculo.noites(dt.date(2026, 6, 1), dt.date(2026, 6, 4))
    assert datas[0] == dt.date(2026, 6, 1)
    assert datas[-1] == dt.date(2026, 6, 3)


# ---------------------------------------------------------------------------
# Caso obrigatório: período inteiramente em baixa temporada
# ---------------------------------------------------------------------------

def test_periodo_inteiro_em_baixa_temporada(banco):
    """Caso obrigatório da Etapa 3.

    Cinco noites a R$ 300,00. A conta é feita à mão no próprio teste.
    """
    conta = calculo.detalhar(CAMBURI, "2026-06-01", "2026-06-06")

    assert conta["noites"] == 5
    assert conta["total"] == BAIXA * 5
    assert conta["total"] == Decimal("1500.00")
    assert conta["atravessa_virada"] is False
    assert len(conta["trechos"]) == 1


def test_periodo_inteiro_em_alta_temporada(banco):
    conta = calculo.detalhar(CAMBURI, "2027-01-10", "2027-01-14")

    assert conta["noites"] == 4
    assert conta["total"] == ALTA * 4
    assert conta["total"] == Decimal("2160.00")
    assert conta["atravessa_virada"] is False


# ---------------------------------------------------------------------------
# Caso obrigatório: período que atravessa a virada
# ---------------------------------------------------------------------------

def test_periodo_que_atravessa_a_virada(banco):
    """Caso obrigatório da Etapa 3, e o mais sujeito a erro.

    De 10 a 18/12 são oito noites. As de 10 a 14 custam a baixa; as de 15
    a 17 custam a alta. A noite do dia 18 não existe: o hóspede sai.

        5 x 300,00 = 1.500,00
        3 x 540,00 = 1.620,00
                     3.120,00
    """
    conta = calculo.detalhar(CAMBURI, "2026-12-10", "2026-12-18")

    assert conta["noites"] == 8
    assert conta["atravessa_virada"] is True
    assert conta["total"] == BAIXA * 5 + ALTA * 3
    assert conta["total"] == Decimal("3120.00")

    assert len(conta["trechos"]) == 2

    primeiro, segundo = conta["trechos"]
    assert primeiro["noites"] == 5
    assert primeiro["valor_diaria"] == BAIXA
    assert primeiro["de"] == dt.date(2026, 12, 10)
    assert primeiro["ate"] == dt.date(2026, 12, 14)

    assert segundo["noites"] == 3
    assert segundo["valor_diaria"] == ALTA
    assert segundo["de"] == VIRADA
    assert segundo["ate"] == dt.date(2026, 12, 17)


def test_a_conta_nao_usa_so_uma_das_tarifas(banco):
    """A armadilha que o método noite a noite evita.

    Somar oito noites pela tarifa de baixa daria 2.400; pela de alta,
    4.320. Nenhum dos dois é o valor correto, e os dois são o que um
    cálculo descuidado produziria.
    """
    total = calculo.valor_total(CAMBURI, "2026-12-10", "2026-12-18")

    assert total != BAIXA * 8
    assert total != ALTA * 8
    assert total == Decimal("3120.00")


# ---------------------------------------------------------------------------
# As fronteiras da virada
# ---------------------------------------------------------------------------

def test_ultima_noite_de_baixa(banco):
    """Entrada 14, saída 15: uma noite, a do dia 14, ainda na baixa."""
    conta = calculo.detalhar(CAMBURI, "2026-12-14", "2026-12-15")
    assert conta["noites"] == 1
    assert conta["total"] == BAIXA


def test_primeira_noite_de_alta(banco):
    """Entrada 15, saída 16: a noite do dia 15, já na alta."""
    conta = calculo.detalhar(CAMBURI, "2026-12-15", "2026-12-16")
    assert conta["noites"] == 1
    assert conta["total"] == ALTA


def test_duas_noites_em_cima_da_virada(banco):
    """Entrada 14, saída 16: uma noite de cada lado."""
    conta = calculo.detalhar(CAMBURI, "2026-12-14", "2026-12-16")

    assert conta["noites"] == 2
    assert conta["total"] == BAIXA + ALTA
    assert conta["atravessa_virada"] is True


def test_saida_no_dia_da_virada_nao_cobra_alta(banco):
    """Sair dia 15 significa não dormir na noite do 15.

    Se o cálculo incluísse a data de saída, cobraria uma diária de alta
    temporada que o hóspede não usou.
    """
    conta = calculo.detalhar(CAMBURI, "2026-12-13", "2026-12-15")

    assert conta["noites"] == 2
    assert conta["total"] == BAIXA * 2
    assert conta["atravessa_virada"] is False


# ---------------------------------------------------------------------------
# Precisão
# ---------------------------------------------------------------------------

def test_o_total_e_decimal_nao_ponto_flutuante(banco):
    """Erro de arredondamento em dinheiro é defeito visível."""
    total = calculo.valor_total(CAMBURI, "2026-06-01", "2026-06-06")
    assert isinstance(total, Decimal)


def test_centavos_somam_sem_erro_acumulado(banco):
    """Uma tarifa com centavos, somada muitas vezes.

    Com ponto flutuante, trinta parcelas de 0,10 não dão exatamente 3,00.
    Com Decimal, dão.
    """
    from app.dados import tarifa_repo

    identificador = tarifa_repo.criar(
        3, {"data_inicio": "2028-01-01", "data_fim": "2028-12-31",
            "valor_diaria": "333.33", "descricao": "Centavos"}
    )
    try:
        # Fevereiro de 2028 é bissexto: de 01/02 a 01/03 são 29 noites.
        conta = calculo.detalhar(3, "2028-02-01", "2028-03-01")
        assert conta["noites"] == 29
        assert conta["total"] == Decimal("333.33") * 29
        assert conta["total"] == Decimal("9666.57")
    finally:
        tarifa_repo.excluir(identificador)


def test_total_tem_duas_casas(banco):
    total = calculo.valor_total(CAMBURI, "2026-06-01", "2026-06-03")
    assert total == total.quantize(Decimal("0.01"))


# ---------------------------------------------------------------------------
# Lacuna no cadastro de tarifas
# ---------------------------------------------------------------------------

def test_periodo_sem_tarifa_informa_a_data_descoberta(banco):
    """Não é defeito do cálculo: é lacuna no cadastro.

    A mensagem diz qual data ficou sem tarifa, para que o administrador
    saiba exatamente o que preencher.
    """
    with pytest.raises(TarifaAusente) as erro:
        calculo.detalhar(CAMBURI, "2030-01-01", "2030-01-05")

    assert "01/01/2030" in str(erro.value)


def test_periodo_parcialmente_sem_tarifa_tambem_falha(banco):
    """A tarifa de alta termina em 28/02/2027."""
    with pytest.raises(TarifaAusente):
        calculo.detalhar(CAMBURI, "2027-02-26", "2027-03-05")


def test_reserva_sem_tarifa_grava_com_valor_nulo(banco):
    """Falta de preço não bloqueia a operação do negócio.

    A recepção precisa poder reservar mesmo quando o administrador ainda
    não cadastrou a tarifa do período. A reserva entra com valor nulo e a
    conta é acertada depois.
    """
    identificador = reserva_repo.criar(
        {
            "chale_id": CAMBURI,
            "hospede_id": 1,
            "data_entrada": "2030-05-01",
            "data_saida": "2030-05-05",
            "num_pessoas": 2,
            "situacao": "pendente",
        }
    )

    gravada = reserva_repo.buscar_por_id(identificador)
    assert gravada is not None
    assert gravada["valor_total"] is None


# ---------------------------------------------------------------------------
# Integração com a reserva
# ---------------------------------------------------------------------------

def test_reserva_grava_o_valor_calculado(banco):
    identificador = reserva_repo.criar(
        {
            "chale_id": CAMBURI,
            "hospede_id": 1,
            "data_entrada": "2026-06-01",
            "data_saida": "2026-06-06",
            "num_pessoas": 2,
            "situacao": "pendente",
        }
    )

    assert reserva_repo.buscar_por_id(identificador)["valor_total"] == Decimal(
        "1500.00"
    )


def test_reserva_que_atravessa_a_virada_grava_o_valor_correto(banco):
    """A reserva do Camburi na massa de dados é este caso.

    Ela foi gravada com 3.480,00 pelo script SQL, um valor estimado à
    mão. Recriá-la pela aplicação produz o valor calculado noite a noite.
    """
    identificador = reserva_repo.criar(
        {
            "chale_id": CAMBURI,
            "hospede_id": 2,
            "data_entrada": "2027-01-05",
            "data_saida": "2027-01-10",
            "num_pessoas": 2,
            "situacao": "pendente",
        }
    )

    assert reserva_repo.buscar_por_id(identificador)["valor_total"] == ALTA * 5


def test_alterar_as_datas_recalcula_o_valor(banco):
    identificador = reserva_repo.criar(
        {
            "chale_id": CAMBURI,
            "hospede_id": 1,
            "data_entrada": "2026-06-01",
            "data_saida": "2026-06-06",
            "num_pessoas": 2,
            "situacao": "pendente",
        }
    )
    assert reserva_repo.buscar_por_id(identificador)["valor_total"] == BAIXA * 5

    reserva_repo.atualizar(
        identificador,
        {
            "chale_id": CAMBURI,
            "hospede_id": 1,
            "data_entrada": "2026-06-01",
            "data_saida": "2026-06-04",
            "num_pessoas": 2,
            "situacao": "pendente",
        },
    )
    assert reserva_repo.buscar_por_id(identificador)["valor_total"] == BAIXA * 3


def test_tela_mostra_a_conta_aberta(banco):
    """A virada precisa ficar visível, não escondida num número só.

    A massa de dados já traz a reserva do Camburi de 10 a 18/12, que
    existe justamente para exercitar a virada. Criar outra no mesmo
    período seria recusado pela regra de não-sobreposição — corretamente.
    """
    with conexao.obter_cursor() as cursor:
        cursor.execute(
            "SELECT id FROM reserva WHERE chale_id = %s "
            "AND data_entrada = '2026-12-10'",
            (CAMBURI,),
        )
        identificador = cursor.fetchone()[0]

    app = create_app(Configuracao(modo_teste=True))
    sessao = app.test_client()
    sessao.post("/login", data={"login": "recepcao", "senha": "Villa@Recep2026"})

    corpo = sessao.get(f"/reservas/{identificador}").data.decode()

    assert "Baixa temporada" in corpo
    assert "Alta temporada" in corpo
    assert "3120.00" in corpo
    assert "atravessa uma virada de temporada" in corpo


# ---------------------------------------------------------------------------
# Validação
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "entrada,saida",
    [("2026-06-10", "2026-06-05"), ("2026-06-10", "2026-06-10")],
)
def test_periodo_invalido_e_recusado(banco, entrada, saida):
    from app.dados.reserva_repo import ErroDeReserva

    with pytest.raises(ErroDeReserva):
        calculo.detalhar(CAMBURI, entrada, saida)


def test_tarifas_do_periodo_cobre_ate_a_ultima_noite(banco):
    """A cobertura precisa alcançar a véspera da saída, não a saída."""
    achadas = calculo.tarifas_do_periodo(CAMBURI, "2026-12-10", "2026-12-18")
    assert len(achadas) == 2

    uma_so = calculo.tarifas_do_periodo(CAMBURI, "2026-12-14", "2026-12-15")
    assert len(uma_so) == 1
    assert uma_so[0]["valor_diaria"] == BAIXA
