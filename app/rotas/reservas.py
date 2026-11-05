"""Consulta de disponibilidade e gestão de reservas.

Acessível a administrador e recepcionista. O perfil `hospede` consulta
apenas as próprias reservas, em rota separada.
"""

import datetime as dt

from flask import Blueprint, render_template, request

from app.dados import reserva_repo
from app.dados.erros import ErroDeDados
from app.seguranca.autorizacao import (
    ADMINISTRADOR,
    RECEPCIONISTA,
    requer_perfil,
    usuario_atual,
)

reservas = Blueprint("reservas", __name__, url_prefix="/reservas")


def _quem():
    return usuario_atual()["id"]


@reservas.route("/disponibilidade")
@requer_perfil(ADMINISTRADOR, RECEPCIONISTA)
def disponibilidade():
    """Dado um período, quais chalés estão livres e quais estão ocupados.

    Sem período informado, a tela abre com o formulário vazio em vez de
    assumir uma data. Adivinhar o que a recepção quer consultar produziria
    uma resposta que parece informação e não é.
    """
    entrada = (request.args.get("entrada") or "").strip()
    saida = (request.args.get("saida") or "").strip()

    contexto = {
        "usuario": usuario_atual(),
        "entrada": entrada,
        "saida": saida,
        "livres": None,
        "ocupados": None,
        "diarias": None,
        "erro": None,
    }

    if not entrada and not saida:
        return render_template("painel/disponibilidade.html", **contexto)

    try:
        inicio, fim = reserva_repo.validar_periodo(entrada, saida)
        contexto["livres"] = reserva_repo.chales_disponiveis(
            inicio, fim, usuario_id=_quem()
        )
        contexto["ocupados"] = reserva_repo.chales_ocupados(
            inicio, fim, usuario_id=_quem()
        )
        contexto["diarias"] = (fim - inicio).days
    except ErroDeDados as erro:
        contexto["erro"] = str(erro)
        return render_template("painel/disponibilidade.html", **contexto), 400

    return render_template("painel/disponibilidade.html", **contexto)
