"""Consulta de disponibilidade e gestão de reservas.

Acessível a administrador e recepcionista. O perfil `hospede` consulta
apenas as próprias reservas, em rota separada.
"""

import datetime as dt

from flask import (
    Blueprint,
    abort,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)

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


# ---------------------------------------------------------------------------
# Gestão de reservas
# ---------------------------------------------------------------------------

def _hospedes_para_selecao():
    """Lista de hóspedes para o campo de seleção do formulário.

    Os nomes chegam decifrados pelo repositório. Um registro ilegível
    aparece com o identificador no lugar do nome, em vez de derrubar a
    tela — a recepção precisa poder trabalhar mesmo com um registro
    corrompido na base.
    """
    from app.dados import hospede_repo

    lista = []
    for hospede in hospede_repo.listar(usuario_id=_quem()):
        rotulo = (
            hospede["nome"]
            if hospede.get("legivel", True) and hospede.get("nome")
            else f"(registro {hospede['id']} não legível)"
        )
        lista.append({"id": hospede["id"], "rotulo": rotulo})
    return lista


@reservas.route("/")
@requer_perfil(ADMINISTRADOR, RECEPCIONISTA)
def listar():
    situacao = (request.args.get("situacao") or "").strip() or None

    try:
        lista = reserva_repo.listar(situacao=situacao, usuario_id=_quem())
    except ErroDeDados as erro:
        flash(str(erro), "erro")
        lista = reserva_repo.listar(usuario_id=_quem())
        situacao = None

    return render_template(
        "painel/reservas.html",
        usuario=usuario_atual(),
        reservas=lista,
        situacao=situacao,
        situacoes=reserva_repo.SITUACOES,
    )


@reservas.route("/nova", methods=["GET", "POST"])
@requer_perfil(ADMINISTRADOR, RECEPCIONISTA)
def nova():
    from app.dados import chale_repo

    contexto = {
        "usuario": usuario_atual(),
        "chales": chale_repo.listar(apenas_ativos=True, usuario_id=_quem()),
        "hospedes": _hospedes_para_selecao(),
        "novo": True,
    }

    if request.method == "GET":
        # A tela de disponibilidade encaminha para cá com chalé e período
        # já escolhidos, para não obrigar a redigitar o que foi consultado.
        contexto["reserva"] = {
            "chale_id": request.args.get("chale_id", type=int),
            "data_entrada": request.args.get("entrada", ""),
            "data_saida": request.args.get("saida", ""),
            "num_pessoas": request.args.get("pessoas", type=int),
            "situacao": "pendente",
        }
        return render_template("painel/reserva_form.html", **contexto)

    dados = _dados_do_formulario()

    try:
        identificador = reserva_repo.criar(dados, usuario_id=_quem())
    except ErroDeDados as erro:
        flash(str(erro), "erro")
        contexto["reserva"] = dados
        return render_template("painel/reserva_form.html", **contexto), 400

    flash(f"Reserva {identificador} criada.", "sucesso")
    return redirect(url_for("reservas.detalhe", identificador=identificador))


@reservas.route("/<int:identificador>")
@requer_perfil(ADMINISTRADOR, RECEPCIONISTA)
def detalhe(identificador):
    reserva = reserva_repo.buscar_por_id(identificador, usuario_id=_quem())
    if reserva is None:
        abort(404)

    return render_template(
        "painel/reserva_detalhe.html",
        usuario=usuario_atual(),
        reserva=reserva,
        transicoes=reserva_repo.TRANSICOES[reserva["situacao"]],
        hospede=_hospede_da_reserva(reserva),
        conta=_conta_da_reserva(reserva),
    )


@reservas.route("/<int:identificador>/editar", methods=["GET", "POST"])
@requer_perfil(ADMINISTRADOR, RECEPCIONISTA)
def editar(identificador):
    from app.dados import chale_repo

    reserva = reserva_repo.buscar_por_id(identificador, usuario_id=_quem())
    if reserva is None:
        abort(404)

    contexto = {
        "usuario": usuario_atual(),
        "chales": chale_repo.listar(apenas_ativos=True, usuario_id=_quem()),
        "hospedes": _hospedes_para_selecao(),
        "novo": False,
    }

    if request.method == "GET":
        contexto["reserva"] = reserva
        return render_template("painel/reserva_form.html", **contexto)

    dados = _dados_do_formulario()

    try:
        reserva_repo.atualizar(identificador, dados, usuario_id=_quem())
    except ErroDeDados as erro:
        flash(str(erro), "erro")
        contexto["reserva"] = dict(reserva, **dados)
        return render_template("painel/reserva_form.html", **contexto), 400

    flash("Reserva atualizada.", "sucesso")
    return redirect(url_for("reservas.detalhe", identificador=identificador))


@reservas.route("/<int:identificador>/situacao", methods=["POST"])
@requer_perfil(ADMINISTRADOR, RECEPCIONISTA)
def mudar_situacao(identificador):
    """Move a reserva no ciclo de vida.

    Aceita apenas POST, com token CSRF. A situação não vem do formulário
    de edição de propósito: confirmar, cancelar e concluir têm regra de
    transição própria, e misturá-las com a edição permitiria reabilitar
    uma reserva cancelada por um campo oculto.
    """
    if reserva_repo.buscar_por_id(identificador, usuario_id=_quem()) is None:
        abort(404)

    nova_situacao = (request.form.get("situacao") or "").strip()

    try:
        reserva_repo.mudar_situacao(
            identificador, nova_situacao, usuario_id=_quem()
        )
    except ErroDeDados as erro:
        flash(str(erro), "erro")
    else:
        flash(f"Reserva marcada como {nova_situacao}.", "sucesso")

    return redirect(url_for("reservas.detalhe", identificador=identificador))


def _dados_do_formulario() -> dict:
    return {
        "chale_id": request.form.get("chale_id"),
        "hospede_id": request.form.get("hospede_id"),
        "data_entrada": request.form.get("data_entrada"),
        "data_saida": request.form.get("data_saida"),
        "num_pessoas": request.form.get("num_pessoas"),
        "observacoes": request.form.get("observacoes"),
        "situacao": "pendente",
    }


def _hospede_da_reserva(reserva):
    """Nome do hóspede, decifrado, para a tela de detalhe.

    Um registro que não decifra não derruba a página: a tela mostra que
    ele existe e não pôde ser lido.
    """
    from app.dados import hospede_repo
    from app.seguranca.cripto import FalhaDeAutenticacao

    try:
        return hospede_repo.buscar_por_id(
            reserva["hospede_id"], usuario_id=_quem()
        )
    except FalhaDeAutenticacao:
        return None


def _conta_da_reserva(reserva):
    """Conta aberta da estadia, noite a noite.

    Mostrar só o total esconde de onde ele vem. Com os trechos à vista, a
    recepção explica ao hóspede por que oito noites custaram mais do que
    oito vezes a diária de baixa temporada — e confere a conta sem
    precisar confiar no sistema.

    Lacuna no cadastro de tarifas devolve None: a tela mostra um traço, e
    a reserva continua existindo.
    """
    from app.dados import calculo

    try:
        return calculo.detalhar(
            reserva["chale_id"],
            reserva["data_entrada"],
            reserva["data_saida"],
            usuario_id=_quem(),
        )
    except ErroDeDados:
        return None
