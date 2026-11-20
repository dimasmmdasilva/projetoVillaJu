"""A equipe confere o Pix — Etapa 5, Bloco 8.

- `/pagamentos`: os cards, para administrador e recepção;
- `/pagamentos/<n>/<acao>`: os três botões, por POST;
- `/pagamentos/controle`: o ano em 12 meses, só do administrador. A
  recepção usa os cards, mas não vê a receita.

O número da reserva vai no endereço, e a ação é decidida pelo
repositório com a reserva travada. Uma recusa tem resposta única: a
reserva não aceita mais a operação, e o card mostra a situação atual.
"""

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for

from app.extensoes import limiter
from app.seguranca.autorizacao import (
    ADMINISTRADOR,
    RECEPCIONISTA,
    requer_perfil,
    usuario_atual,
)
from app.servicos import pagamentos as servico

pagamentos = Blueprint("pagamentos", __name__, url_prefix="/pagamentos")

_MENSAGENS = {
    servico.PRIMEIRO: "Pix conferido. A reserva está confirmada.",
    servico.RESTANTE: "Pix do restante conferido. O pagamento está completo.",
    servico.DEVOLVER: (
        "Devolução registrada. O valor sai do total do mês de hoje."
    ),
    servico.CANCELAR: (
        "Reserva cancelada por falta de pagamento. O chalé voltou a ficar "
        "disponível."
    ),
}


@pagamentos.route("")
@requer_perfil(ADMINISTRADOR, RECEPCIONISTA)
def cards():
    usuario = usuario_atual()
    return render_template(
        "painel/pagamentos.html",
        cards=servico.cards(usuario_id=usuario["id"]),
        devolucoes=servico.devolucoes(usuario_id=usuario["id"]),
    )


@pagamentos.route("/<int:identificador>/<acao>", methods=["POST"])
@requer_perfil(ADMINISTRADOR, RECEPCIONISTA)
@limiter.limit("120 per hour")
def agir(identificador, acao):
    if acao not in servico.ACOES:
        abort(404)

    if servico.executar(acao, identificador, usuario_atual()):
        flash(_MENSAGENS[acao], "sucesso")
    else:
        flash(
            "Esta reserva não aceita mais essa operação. Confira a situação "
            "atual.",
            "erro",
        )
    return redirect(url_for("pagamentos.cards"))


@pagamentos.route("/controle")
@requer_perfil(ADMINISTRADOR)
def controle():
    usuario = usuario_atual()
    return render_template(
        "painel/controle_pagamentos.html",
        controle=servico.controle(
            request.args.get("ano"),
            request.args.get("mes"),
            usuario_id=usuario["id"],
        ),
    )
