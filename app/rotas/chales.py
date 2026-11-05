"""Cadastro de chalés e das tarifas de cada um.

Restrito ao perfil administrador, conforme a seção 3.3 do plano: o
recepcionista opera reservas e hóspedes, mas não altera chalés nem
tarifas. A verificação é do decorador, no servidor, em cada rota.

As exceções de domínio vindas dos repositórios — `ErroDeChale`,
`ErroDeTarifa`, `RegraDoBanco` — carregam mensagem escrita para ser lida
por pessoas e são exibidas como estão. Qualquer outro erro vira página
genérica, com o detalhe só no log.
"""

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for

from app.dados import chale_repo, tarifa_repo
from app.dados.erros import ErroDeDados
from app.seguranca.autorizacao import ADMINISTRADOR, requer_perfil, usuario_atual

chales = Blueprint("chales", __name__, url_prefix="/chales")


def _quem():
    return usuario_atual()["id"]


# ---------------------------------------------------------------------------
# Chalés
# ---------------------------------------------------------------------------

@chales.route("/")
@requer_perfil(ADMINISTRADOR)
def listar():
    return render_template(
        "painel/chales.html",
        usuario=usuario_atual(),
        chales=chale_repo.listar(usuario_id=_quem()),
    )


@chales.route("/novo", methods=["GET", "POST"])
@requer_perfil(ADMINISTRADOR)
def novo():
    if request.method == "GET":
        return render_template(
            "painel/chale_form.html", usuario=usuario_atual(), chale={}, novo=True
        )

    dados = {campo: request.form.get(campo) for campo in chale_repo.CAMPOS}

    try:
        chale_repo.criar(dados, usuario_id=_quem())
    except ErroDeDados as erro:
        flash(str(erro), "erro")
        return render_template(
            "painel/chale_form.html", usuario=usuario_atual(), chale=dados, novo=True
        ), 400

    flash("Chalé cadastrado.", "sucesso")
    return redirect(url_for("chales.listar"))


@chales.route("/<int:identificador>", methods=["GET", "POST"])
@requer_perfil(ADMINISTRADOR)
def editar(identificador):
    chale = chale_repo.buscar_por_id(identificador, usuario_id=_quem())
    if chale is None:
        abort(404)

    if request.method == "GET":
        return render_template(
            "painel/chale_form.html",
            usuario=usuario_atual(),
            chale=chale,
            novo=False,
            tarifas=tarifa_repo.listar_por_chale(identificador, usuario_id=_quem()),
        )

    dados = {campo: request.form.get(campo) for campo in chale_repo.CAMPOS}

    try:
        chale_repo.atualizar(identificador, dados, usuario_id=_quem())
    except ErroDeDados as erro:
        flash(str(erro), "erro")
        return render_template(
            "painel/chale_form.html",
            usuario=usuario_atual(),
            chale=dict(chale, **dados),
            novo=False,
            tarifas=tarifa_repo.listar_por_chale(identificador, usuario_id=_quem()),
        ), 400

    flash("Chalé atualizado.", "sucesso")
    return redirect(url_for("chales.editar", identificador=identificador))


@chales.route("/<int:identificador>/situacao", methods=["POST"])
@requer_perfil(ADMINISTRADOR)
def alternar_situacao(identificador):
    """Desativa ou reativa o chalé.

    Aceita apenas POST, e por isso exige token CSRF: com GET, bastaria a
    um sítio hostil embutir uma imagem apontando para esta rota para
    desativar um chalé pelo navegador de um administrador autenticado.
    """
    chale = chale_repo.buscar_por_id(identificador, usuario_id=_quem())
    if chale is None:
        abort(404)

    novo_estado = not chale["ativo"]

    try:
        chale_repo.definir_ativo(identificador, novo_estado, usuario_id=_quem())
    except ErroDeDados as erro:
        flash(str(erro), "erro")
        return redirect(url_for("chales.listar"))

    flash(
        f"Chalé {'reativado' if novo_estado else 'desativado'}.", "sucesso"
    )
    return redirect(url_for("chales.listar"))


# ---------------------------------------------------------------------------
# Tarifas
# ---------------------------------------------------------------------------

@chales.route("/<int:chale_id>/tarifas/nova", methods=["GET", "POST"])
@requer_perfil(ADMINISTRADOR)
def nova_tarifa(chale_id):
    chale = chale_repo.buscar_por_id(chale_id, usuario_id=_quem())
    if chale is None:
        abort(404)

    if request.method == "GET":
        return render_template(
            "painel/tarifa_form.html",
            usuario=usuario_atual(),
            chale=chale,
            tarifa={},
            novo=True,
        )

    dados = {
        campo: request.form.get(campo)
        for campo in ("data_inicio", "data_fim", "valor_diaria", "descricao")
    }

    try:
        tarifa_repo.criar(chale_id, dados, usuario_id=_quem())
    except ErroDeDados as erro:
        flash(str(erro), "erro")
        return render_template(
            "painel/tarifa_form.html",
            usuario=usuario_atual(),
            chale=chale,
            tarifa=dados,
            novo=True,
        ), 400

    flash("Tarifa cadastrada.", "sucesso")
    return redirect(url_for("chales.editar", identificador=chale_id))


@chales.route("/<int:chale_id>/tarifas/<int:identificador>", methods=["GET", "POST"])
@requer_perfil(ADMINISTRADOR)
def editar_tarifa(chale_id, identificador):
    chale = chale_repo.buscar_por_id(chale_id, usuario_id=_quem())
    tarifa = tarifa_repo.buscar_por_id(identificador, usuario_id=_quem())

    # A tarifa precisa pertencer ao chalé da URL. Sem esta verificação,
    # trocar o número na barra do navegador permitiria editar a tarifa de
    # outro chalé — referência direta a objeto sem controle.
    if chale is None or tarifa is None or tarifa["chale_id"] != chale_id:
        abort(404)

    if request.method == "GET":
        return render_template(
            "painel/tarifa_form.html",
            usuario=usuario_atual(),
            chale=chale,
            tarifa=tarifa,
            novo=False,
        )

    dados = {
        campo: request.form.get(campo)
        for campo in ("data_inicio", "data_fim", "valor_diaria", "descricao")
    }

    try:
        tarifa_repo.atualizar(identificador, chale_id, dados, usuario_id=_quem())
    except ErroDeDados as erro:
        flash(str(erro), "erro")
        return render_template(
            "painel/tarifa_form.html",
            usuario=usuario_atual(),
            chale=chale,
            tarifa=dict(tarifa, **dados),
            novo=False,
        ), 400

    flash("Tarifa atualizada.", "sucesso")
    return redirect(url_for("chales.editar", identificador=chale_id))


@chales.route("/<int:chale_id>/tarifas/<int:identificador>/excluir", methods=["POST"])
@requer_perfil(ADMINISTRADOR)
def excluir_tarifa(chale_id, identificador):
    tarifa = tarifa_repo.buscar_por_id(identificador, usuario_id=_quem())
    if tarifa is None or tarifa["chale_id"] != chale_id:
        abort(404)

    try:
        tarifa_repo.excluir(identificador, usuario_id=_quem())
    except ErroDeDados as erro:
        flash(str(erro), "erro")
    else:
        flash("Tarifa removida.", "sucesso")

    return redirect(url_for("chales.editar", identificador=chale_id))
