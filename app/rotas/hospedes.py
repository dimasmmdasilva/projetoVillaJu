"""Cadastro de hóspedes.

Tela mínima: lista, formulário de inclusão e busca por documento ou
e-mail. O CRUD completo entra na Etapa 3; o que existe aqui serve para
demonstrar que a cifragem funciona de ponta a ponta — digita-se um nome na
tela e ele chega ilegível ao banco.

Acesso restrito a administrador e recepcionista. O perfil `hospede`
consulta apenas as próprias reservas e não tem nada a fazer no cadastro de
terceiros.
"""

from flask import Blueprint, flash, redirect, render_template, request, url_for

from app.dados import hospede_repo
from app.seguranca.autorizacao import (
    ADMINISTRADOR,
    RECEPCIONISTA,
    requer_perfil,
    usuario_atual,
)

hospedes = Blueprint("hospedes", __name__, url_prefix="/hospedes")


@hospedes.route("/")
@requer_perfil(ADMINISTRADOR, RECEPCIONISTA)
def listar():
    usuario = usuario_atual()
    procurado = (request.args.get("busca") or "").strip()

    if procurado:
        # Procura pelos dois índices: quem digita pode informar documento
        # ou e-mail, e não faz sentido exigir que diga qual é qual.
        achado = hospede_repo.buscar_por_documento(
            procurado, usuario_id=usuario["id"]
        ) or hospede_repo.buscar_por_email(procurado, usuario_id=usuario["id"])
        lista = [achado] if achado else []
    else:
        lista = hospede_repo.listar(usuario_id=usuario["id"])

    return render_template(
        "painel/hospedes.html",
        usuario=usuario,
        hospedes=lista,
        busca=procurado,
        total=hospede_repo.contar(usuario_id=usuario["id"]),
    )


@hospedes.route("/novo", methods=["GET", "POST"])
@requer_perfil(ADMINISTRADOR, RECEPCIONISTA)
def novo():
    usuario = usuario_atual()

    if request.method == "GET":
        return render_template("painel/hospede_form.html", usuario=usuario, dados={})

    dados = {campo: request.form.get(campo) for campo in hospede_repo.CAMPOS}

    try:
        identificador = hospede_repo.criar(dados, usuario_id=usuario["id"])
    except hospede_repo.HospedeDuplicado:
        flash("Já existe hóspede com este documento ou e-mail.", "erro")
        return render_template(
            "painel/hospede_form.html", usuario=usuario, dados=dados
        ), 409
    except hospede_repo.ErroDeHospede as erro:
        # A mensagem destas exceções é escrita pelo próprio repositório e
        # fala de campo e tamanho, nunca de banco ou de biblioteca.
        flash(str(erro), "erro")
        return render_template(
            "painel/hospede_form.html", usuario=usuario, dados=dados
        ), 400

    flash(f"Hóspede cadastrado com o número {identificador}.", "sucesso")
    return redirect(url_for("hospedes.listar"))
