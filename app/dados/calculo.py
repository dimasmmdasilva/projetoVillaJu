"""Cálculo do valor total de uma reserva.

O plano aponta esta como a parte mais sujeita a erro de toda a Etapa 3, e
o motivo é a virada de temporada: uma estadia de 10 a 18 de dezembro
atravessa o dia 15, quando a tarifa muda de baixa para alta. Somar
diárias pelo preço de um lado só erra em favor da hospedaria ou do
hóspede, e nenhum dos dois é aceitável.

A solução é não tentar ser esperto: **percorre-se uma noite por vez**.
Para cada noite da estadia, busca-se a tarifa vigente naquela data e
soma-se. Não há fórmula, não há interpolação, não há caso especial para a
virada — ela deixa de ser um caso especial porque cada noite é tratada
isoladamente.

    10/12  tarifa baixa  R$ 300
    11/12  tarifa baixa  R$ 300
    ...
    14/12  tarifa baixa  R$ 300
    15/12  tarifa ALTA   R$ 540   <- a virada acontece aqui, sem código próprio
    16/12  tarifa ALTA   R$ 540
    17/12  tarifa ALTA   R$ 540
                                  total: 5 x 300 + 3 x 540 = 3.120

**Quantas noites tem uma estadia.** De 10 a 18 são oito noites, não nove:
a reserva ocupa de `data_entrada` inclusive até `data_saida` exclusive —
o mesmo intervalo semiaberto da trigger. Quem sai dia 18 não dorme na
noite do dia 18.

**Por que Decimal e não float.** Ponto flutuante binário não representa
valores decimais com exatidão: `0.1 + 0.2` não é `0.3`. Numa soma de
noventa diárias, o erro acumulado aparece no centavo — e centavo errado
numa conta de hospedagem é defeito visível.
"""

import datetime as dt
from decimal import Decimal

from app.dados.conexao import obter_cursor
from app.dados.erros import ErroDeDados

# Uma diária por noite; a consulta é por data. Buscar as tarifas de uma
# vez e resolver em Python seria mais rápido, mas a estadia máxima é de
# noventa noites e a clareza vale mais que o microssegundo.
_SQL_TARIFA_NA_DATA = (
    "SELECT id, valor_diaria, descricao FROM tarifa "
    "WHERE chale_id = %s AND data_inicio <= %s AND data_fim >= %s "
    "ORDER BY data_inicio LIMIT 1"
)

_SQL_TARIFAS_DO_PERIODO = (
    "SELECT id, data_inicio, data_fim, valor_diaria, descricao "
    "FROM tarifa "
    "WHERE chale_id = %s AND data_inicio <= %s AND data_fim >= %s "
    "ORDER BY data_inicio"
)


class ErroDeCalculo(ErroDeDados):
    """Não foi possível calcular o valor da estadia."""


class TarifaAusente(ErroDeCalculo):
    """Alguma noite da estadia não tem tarifa cadastrada.

    Não é defeito do cálculo: é lacuna no cadastro. A mensagem diz qual
    data ficou descoberta, para que o administrador saiba o que preencher.
    """


def noites(entrada: dt.date, saida: dt.date) -> list:
    """As datas em que o hóspede dorme no chalé.

    Intervalo semiaberto: inclui a entrada, exclui a saída. É a mesma
    definição que a trigger usa para decidir sobreposição, e usar outra
    aqui produziria uma conta que não corresponde à ocupação.
    """
    return [
        entrada + dt.timedelta(days=numero)
        for numero in range((saida - entrada).days)
    ]


def detalhar(chale_id, entrada, saida, usuario_id=None) -> dict:
    """Percorre noite a noite e devolve a conta aberta.

    Devolve o total, a quantidade de noites e a lista de trechos — cada
    trecho é uma sequência de noites com a mesma tarifa. A tela mostra os
    trechos para que a virada de temporada fique visível ao hóspede, em
    vez de aparecer só um número que ele não consegue conferir.
    """
    from app.dados.reserva_repo import validar_periodo

    entrada, saida = validar_periodo(entrada, saida)
    datas = noites(entrada, saida)

    if not datas:
        raise ErroDeCalculo("A estadia precisa ter ao menos uma noite.")

    trechos = []
    total = Decimal("0.00")

    with obter_cursor(usuario_id=usuario_id, dicionario=True) as cursor:
        for data in datas:
            cursor.execute(_SQL_TARIFA_NA_DATA, (chale_id, data, data))
            tarifa = cursor.fetchone()

            if tarifa is None:
                raise TarifaAusente(
                    f"Não há tarifa cadastrada para "
                    f"{data:%d/%m/%Y}. Cadastre a tarifa do período antes "
                    f"de calcular o valor da reserva."
                )

            valor = Decimal(tarifa["valor_diaria"])
            total += valor

            # Noites consecutivas com a mesma tarifa viram um trecho só.
            if trechos and trechos[-1]["tarifa_id"] == tarifa["id"]:
                trechos[-1]["noites"] += 1
                trechos[-1]["ate"] = data
                trechos[-1]["subtotal"] += valor
            else:
                trechos.append(
                    {
                        "tarifa_id": tarifa["id"],
                        "descricao": tarifa["descricao"],
                        "valor_diaria": valor,
                        "de": data,
                        "ate": data,
                        "noites": 1,
                        "subtotal": valor,
                    }
                )

    return {
        "total": total.quantize(Decimal("0.01")),
        "noites": len(datas),
        "trechos": trechos,
        "atravessa_virada": len(trechos) > 1,
    }


def valor_total(chale_id, entrada, saida, usuario_id=None) -> Decimal:
    """Só o total. Atalho para quem não precisa da conta aberta."""
    return detalhar(chale_id, entrada, saida, usuario_id=usuario_id)["total"]


def tarifas_do_periodo(chale_id, entrada, saida, usuario_id=None) -> list:
    """Tarifas que tocam o período. Usado para avisar sobre lacunas."""
    from app.dados.reserva_repo import validar_periodo

    entrada, saida = validar_periodo(entrada, saida)

    with obter_cursor(usuario_id=usuario_id, dicionario=True) as cursor:
        # A última noite é a véspera da saída; é até ela que a cobertura
        # precisa alcançar.
        cursor.execute(
            _SQL_TARIFAS_DO_PERIODO,
            (chale_id, saida - dt.timedelta(days=1), entrada),
        )
        return cursor.fetchall()
