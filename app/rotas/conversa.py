"""A conversa da reserva — Etapa 5, Bloco 17.

O hóspede lê e escreve em `/reservas/<n>/conversa`; a equipe responde no
detalhe da reserva. Tudo por POST com token CSRF, e sem JavaScript.

Para o hóspede, uma recusa **não redireciona**: a tela volta com o erro e o texto
digitado. Para a equipe, o campo tem `maxlength` e a recusa (vazio ou
longo demais) volta ao detalhe com o aviso.
"""

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for

from app.extensoes import limiter
from app.seguranca.autorizacao import (
    ADMINISTRADOR,
    HOSPEDE,
    RECEPCIONISTA,
    requer_perfil,
    usuario_atual,
)
from app.servicos import conversa as servico

conversa = Blueprint("conversa", __name__, url_prefix="/reservas")

# Folgado para uma conversa de verdade, apertado para quem quisesse encher
# a caixa da equipe.
_LIMITE = "30 per hour"


def _hospede():
    usuario = usuario_atual()
    if not usuario.get("hospede_id"):
        abort(403)
    return usuario


def _tela_do_hospede(identificador, erro=None, digitado="", status=200):
    dados = servico.tela_do_hospede(_hospede(), identificador)
    if dados is None:
        abort(404)
    return render_template(
        "painel/conversa.html", erro=erro, digitado=digitado, **dados
    ), status


@conversa.route("/<int:identificador>/conversa")
@requer_perfil(HOSPEDE)
def do_hospede(identificador):
    return _tela_do_hospede(identificador)


@conversa.route("/<int:identificador>/conversa", methods=["POST"])
@requer_perfil(HOSPEDE)
@limiter.limit(_LIMITE)
def enviar_do_hospede(identificador):
    texto = request.form.get("texto", "")
    try:
        valeu = servico.enviar_do_hospede(_hospede(), identificador, texto)
    except servico.ErroDeMensagem as erro:
        return _tela_do_hospede(identificador, str(erro), texto, 400)
    if not valeu:
        abort(404)
    flash("Mensagem enviada. A pousada responde por aqui.", "sucesso")
    return redirect(url_for("conversa.do_hospede", identificador=identificador))


@conversa.route("/<int:identificador>/conversa/equipe", methods=["POST"])
@requer_perfil(ADMINISTRADOR, RECEPCIONISTA)
@limiter.limit("120 per hour")
def enviar_da_equipe(identificador):
    texto = request.form.get("texto", "")
    try:
        valeu = servico.enviar_da_equipe(usuario_atual(), identificador, texto)
    except servico.ErroDeMensagem as erro:
        flash(str(erro), "erro")
        valeu = None
    if valeu is False:
        abort(404)
    if valeu:
        flash("Resposta enviada ao hóspede.", "sucesso")
    return redirect(
        url_for("reservas.detalhe", identificador=identificador,
                _anchor="conversa")
    )


@conversa.route("/<int:identificador>/conversa/lidas", methods=["POST"])
@requer_perfil(ADMINISTRADOR, RECEPCIONISTA)
def marcar_como_lidas(identificador):
    servico.marcar_como_lidas(usuario_atual(), identificador)
    flash("Mensagens marcadas como lidas.", "sucesso")
    return redirect(
        url_for("reservas.detalhe", identificador=identificador,
                _anchor="conversa")
    )
