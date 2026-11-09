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
from app.dados.reserva_repo import ConflitoDePeriodo
from app.seguranca.autorizacao import (
    ADMINISTRADOR,
    HOSPEDE,
    RECEPCIONISTA,
    requer_perfil,
    usuario_atual,
)

reservas = Blueprint("reservas", __name__, url_prefix="/reservas")


def _quem():
    return usuario_atual()["id"]


def _codigo(erro) -> int:
    """409 para falta de disponibilidade, 400 para dado inválido.

    A reserva conflitante não está mal escrita: está impedida pelo estado
    atual do calendário. Responder 400 a ela diria ao cliente que o pedido
    estava malformado, e repetir o mesmo pedido depois de um cancelamento
    funcionaria — o que não é comportamento de 400.
    """
    return 409 if isinstance(erro, ConflitoDePeriodo) else 400


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
        return render_template("painel/reserva_form.html", **contexto), _codigo(erro)

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
        return render_template(
            "painel/reserva_form.html", **contexto
        ), _codigo(erro)

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
    """Lê o formulário, com os identificadores como inteiros.

    `type=int` não é detalhe. O formulário devolve texto, e o template
    marca a opção escolhida comparando com o identificador do chalé, que
    é inteiro. Em Jinja2, "1" == 1 é falso — então, num envio recusado, o
    chalé e o hóspede voltavam ao campo vazio e a recepção precisava
    escolher os dois de novo, depois de já ter preenchido tudo.

    Valor ausente ou não numérico vira None, e o repositório responde
    "Selecione o chalé e o hóspede".
    """
    return {
        "chale_id": request.form.get("chale_id", type=int),
        "hospede_id": request.form.get("hospede_id", type=int),
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


# ---------------------------------------------------------------------------
# Área do hóspede
# ---------------------------------------------------------------------------

@reservas.route("/minhas")
@requer_perfil(HOSPEDE)
def minhas():
    """As reservas de quem está conectado. Somente as dele.

    **De onde vem o `hospede_id`.** Da linha do usuário na tabela
    `usuario`, lida do banco a cada requisição por `usuario_atual()`. Não
    vem da URL, não vem do formulário e não vem da sessão do navegador.
    Essa é a diferença entre esta tela e um furo de autorização: se o
    identificador fosse parâmetro, trocar o número no endereço mostraria
    as reservas de outro hóspede — a falha de controle de acesso mais
    comum que existe em sistema web.

    **Onde a filtragem acontece.** No `WHERE` do SQL, dentro de
    `listar_por_hospede`. Trazer todas as reservas e descartar as alheias
    em Python faria o banco entregar à aplicação dado que o solicitante
    não pode ver, e bastaria um esquecimento no laço para exibi-lo.

    O detalhe da reserva não é aberto aqui: `reservas.detalhe` exige
    administrador ou recepcionista, e a conta aberta da estadia aparece
    direto na linha.
    """
    usuario = usuario_atual()

    # A trigger de integridade de perfil exige o vínculo quando o perfil é
    # `hospede`. Se ele não existe, a conta está malformada: não se adivinha
    # a quem ela pertence, e mostrar tudo seria exatamente o erro oposto.
    if not usuario.get("hospede_id"):
        flash(
            "Sua conta não está vinculada a um cadastro de hóspede. "
            "Procure a recepção.",
            "erro",
        )
        return render_template(
            "painel/minhas_reservas.html",
            usuario=usuario,
            reservas=[],
            vinculada=False,
        )

    lista = reserva_repo.listar_por_hospede(
        usuario["hospede_id"], usuario_id=usuario["id"]
    )

    # A conta de cada estadia, calculada noite a noite. Lacuna de tarifa
    # devolve None e a linha mostra um traço.
    contas = {reserva["id"]: _conta_da_reserva(reserva) for reserva in lista}

    return render_template(
        "painel/minhas_reservas.html",
        usuario=usuario,
        reservas=lista,
        contas=contas,
        vinculada=True,
    )
