"""Os relatórios da equipe — Etapa 5, Bloco 18a.

RF06: ocupação e reservas por chalé e por período. RF12, como reescrito no
Bloco 17: o volume de mensagens da conversa. Administrador e recepção veem
os três (decisão do Dimas em 21/11).

**O período é escrito como se lê**: "de 01/11 a 30/11", os dois dias
incluídos. Por dentro vira `[01/11, 01/12)`, semiaberto como as reservas.
Sem período na URL, vale o mês corrente. No máximo um ano, para que a
consulta tenha tamanho previsível.

**Só números.** Nada de nome de hóspede nem de texto de mensagem: o
relatório diz quanto, nunca quem.
"""

import calendar
import datetime as dt
from decimal import ROUND_HALF_UP, Decimal

from app.dados import relatorio_repo
from app.dados.periodo import ErroDePeriodo, converter_data

MAXIMO_DE_DIAS = 366

ROTULOS_DA_SITUACAO = (
    ("pendente", "Aguardando pagamento"),
    ("confirmada", "Confirmadas"),
    ("concluida", "Concluídas"),
)

ROTULOS_DO_CANCELAMENTO = (
    ("pedido_do_hospede", "Canceladas a pedido do hóspede"),
    ("equipe", "Canceladas pela pousada"),
    ("prazo_do_pix", "Canceladas: Pix não informado no prazo"),
    ("falta_de_pagamento", "Canceladas: falta de pagamento"),
    (None, "Canceladas sem motivo registrado"),
)


class ErroDeRelatorio(Exception):
    """O período pedido não serve; a frase diz por quê."""


def periodo(de=None, ate=None, hoje: dt.date = None) -> tuple:
    """`(de, ate, fim_exclusivo)`. Sem datas, o mês corrente."""
    hoje = hoje or dt.date.today()
    if not de and not ate:
        de = hoje.replace(day=1)
        ate = hoje.replace(day=calendar.monthrange(hoje.year, hoje.month)[1])
    else:
        try:
            de = converter_data(de, "data inicial")
            ate = converter_data(ate, "data final")
        except ErroDePeriodo as erro:
            raise ErroDeRelatorio(str(erro)) from None
    if ate < de:
        raise ErroDeRelatorio("A data final precisa ser igual ou posterior à inicial.")
    dias = (ate - de).days + 1
    if dias > MAXIMO_DE_DIAS:
        raise ErroDeRelatorio(
            f"O período tem {dias} dias; o máximo é {MAXIMO_DE_DIAS}."
        )
    return de, ate, ate + dt.timedelta(days=1)


def _taxa(parte, todo) -> Decimal:
    if not todo:
        return Decimal("0.0")
    return (Decimal(parte) * 100 / Decimal(todo)).quantize(
        Decimal("0.1"), rounding=ROUND_HALF_UP
    )


def montar(de=None, ate=None, usuario_id=None, hoje: dt.date = None) -> dict:
    de, ate, fim = periodo(de, ate, hoje)
    possiveis = (fim - de).days

    chales = []
    for linha in relatorio_repo.ocupacao(de, fim, usuario_id=usuario_id):
        noites = int(linha["noites"])
        chales.append({
            "nome": linha["nome"],
            "noites": noites,
            "possiveis": possiveis,
            "taxa": _taxa(noites, possiveis),
        })
    total_noites = sum(c["noites"] for c in chales)
    total_possiveis = possiveis * len(chales)

    contagem = {}
    for situacao, motivo, total in relatorio_repo.reservas(de, fim, usuario_id=usuario_id):
        chave = ("cancelada", motivo) if situacao == "cancelada" else (situacao, None)
        contagem[chave] = contagem.get(chave, 0) + total
    reservas = [
        {"rotulo": rotulo, "total": contagem.get((situacao, None), 0)}
        for situacao, rotulo in ROTULOS_DA_SITUACAO
    ] + [
        {"rotulo": rotulo, "total": contagem.get(("cancelada", motivo), 0)}
        for motivo, rotulo in ROTULOS_DO_CANCELAMENTO
        # A linha "sem motivo" só aparece se houver: são as antigas, de
        # antes do Bloco 17e.
        if motivo is not None or contagem.get(("cancelada", None))
    ]

    mensagens = relatorio_repo.mensagens(de, fim, usuario_id=usuario_id)

    return {
        "de": de,
        "ate": ate,
        "dias": possiveis,
        "chales": chales,
        "total": {
            "noites": total_noites,
            "possiveis": total_possiveis,
            "taxa": _taxa(total_noites, total_possiveis),
        },
        "reservas": reservas,
        "total_de_reservas": sum(r["total"] for r in reservas),
        "mensagens": {
            "dos_hospedes": mensagens.get(False, 0),
            "da_equipe": mensagens.get(True, 0),
        },
    }
