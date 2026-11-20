"""A lista de quem vai, editada pelo administrador — Etapa 5, Bloco 15b.

Todas as rotas são só do administrador. A recepção vê a lista completa
nos cards de pagamento, mas não edita (decisão de 13/11).

Uma recusa **não redireciona**: a tela volta com o erro e com o que foi
digitado, pelo mesmo motivo do pedido do hóspede — nome e documento não
vão parar no endereço.
"""

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for

from app.extensoes import limiter
from app.seguranca.autorizacao import ADMINISTRADOR, requer_perfil, usuario_atual
from app.servicos import quem_vai as servico

quem_vai = Blueprint("quem_vai", __name__, url_prefix="/reservas")

_LIMITE = "120 per hour"


def _tela(identificador, erro=None, digitado=None, ordem_com_erro=None,
          status=200):
    usuario = usuario_atual()
    dados = servico.tela(identificador, usuario["id"])
    if dados is None:
        abort(404)
    return render_template(
        "painel/quem_vai.html",
        erro=erro,
        digitado=digitado or {},
        ordem_com_erro=ordem_com_erro,
        **dados,
    ), status


def _do_formulario():
    return {
        "nome": request.form.get("nome", ""),
        "documento": request.form.get("documento", ""),
        "menor": request.form.get("menor", ""),
    }


@quem_vai.route("/<int:identificador>/quem-vai")
@requer_perfil(ADMINISTRADOR)
def editar(identificador):
    return _tela(identificador)


@quem_vai.route("/<int:identificador>/quem-vai/acrescentar", methods=["POST"])
@requer_perfil(ADMINISTRADOR)
@limiter.limit(_LIMITE)
def acrescentar(identificador):
    dados = _do_formulario()
    try:
        servico.acrescentar(identificador, dados, usuario_atual())
    except servico.ErroDeLista as erro:
        return _tela(identificador, str(erro), dados, "novo", 200)
    flash("Pessoa acrescentada à lista.", "sucesso")
    return redirect(url_for("quem_vai.editar", identificador=identificador))


@quem_vai.route("/<int:identificador>/quem-vai/<int:ordem>/corrigir",
                methods=["POST"])
@requer_perfil(ADMINISTRADOR)
@limiter.limit(_LIMITE)
def corrigir(identificador, ordem):
    dados = _do_formulario()
    try:
        valeu = servico.corrigir(identificador, ordem, dados, usuario_atual())
    except servico.ErroDeLista as erro:
        return _tela(identificador, str(erro), dados, ordem, 200)
    if valeu:
        flash("Lista atualizada.", "sucesso")
    else:
        flash("Este acompanhante não está mais na lista.", "erro")
    return redirect(url_for("quem_vai.editar", identificador=identificador))


@quem_vai.route("/<int:identificador>/quem-vai/<int:ordem>/remover",
                methods=["POST"])
@requer_perfil(ADMINISTRADOR)
@limiter.limit(_LIMITE)
def remover(identificador, ordem):
    try:
        valeu = servico.remover(identificador, ordem, usuario_atual())
    except servico.ErroDeLista as erro:
        return _tela(identificador, str(erro))
    if valeu:
        flash("Pessoa removida da lista. A reserva tem uma pessoa a menos.",
              "sucesso")
    else:
        flash("Este acompanhante não está mais na lista.", "erro")
    return redirect(url_for("quem_vai.editar", identificador=identificador))
