"""Cadastro de hóspedes.

Lista, inclusão, edição, exclusão e busca por documento ou e-mail. A
cifragem é transparente para quem usa a tela: digita-se um nome e ele
chega ilegível ao banco; lê-se a lista e ele volta legível. A chave vive
no `.env` e nunca no repositório.

**Exclusão.** A chave estrangeira de `reserva` é `RESTRICT`, e a de
`usuario` também. Quem tem reserva ou conta de acesso não é apagado: o
banco recusa, e esta tela traduz a recusa numa frase que diz o que fazer.
Apagar em cascata destruiria o histórico de ocupação da hospedaria junto
com o cadastro, e esse histórico é justamente o que o sistema existe para
guardar.

Acesso restrito a administrador e recepcionista. O perfil `hospede`
consulta apenas as próprias reservas e não tem nada a fazer no cadastro de
terceiros.
"""

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for

from app.dados import erros, hospede_repo, reserva_repo
from app.seguranca.cripto import FalhaDeAutenticacao
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
        return render_template(
            "painel/hospede_form.html", usuario=usuario, dados={}, novo=True
        )

    dados = {campo: request.form.get(campo) for campo in hospede_repo.CAMPOS}

    try:
        identificador = hospede_repo.criar(dados, usuario_id=usuario["id"])
    except hospede_repo.HospedeDuplicado:
        flash("Já existe hóspede com este documento ou e-mail.", "erro")
        return render_template(
            "painel/hospede_form.html", usuario=usuario, dados=dados, novo=True
        ), 409
    except hospede_repo.ErroDeHospede as erro:
        # A mensagem destas exceções é escrita pelo próprio repositório e
        # fala de campo e tamanho, nunca de banco ou de biblioteca.
        flash(str(erro), "erro")
        return render_template(
            "painel/hospede_form.html", usuario=usuario, dados=dados, novo=True
        ), 400

    flash(f"Hóspede cadastrado com o número {identificador}.", "sucesso")
    return redirect(url_for("hospedes.listar"))


@hospedes.route("/<int:identificador>/editar", methods=["GET", "POST"])
@requer_perfil(ADMINISTRADOR, RECEPCIONISTA)
def editar(identificador):
    """Altera os dados de um hóspede já cadastrado.

    O GET decifra o registro para preencher o formulário. Se a decifragem
    falhar, a edição é recusada: regravar por cima de um registro que não
    se conseguiu ler apagaria a evidência do problema.
    """
    usuario = usuario_atual()

    try:
        hospede = hospede_repo.buscar_por_id(identificador, usuario_id=usuario["id"])
    except FalhaDeAutenticacao:
        # Só esta exceção, não `Exception`: um banco fora do ar também
        # interromperia a leitura, e dizer ao usuário que o registro está
        # corrompido quando o problema é a conexão manda consertar a coisa
        # errada. A lista marca esses registros como não legíveis; aqui a
        # edição simplesmente para.
        flash(
            "O registro deste hóspede não passou na verificação de "
            "integridade e não pode ser editado.",
            "erro",
        )
        return redirect(url_for("hospedes.listar"))

    if hospede is None:
        abort(404)

    if request.method == "GET":
        dados = {campo: hospede.get(campo) for campo in hospede_repo.CAMPOS}
        return render_template(
            "painel/hospede_form.html",
            usuario=usuario,
            dados=dados,
            hospede=hospede,
            novo=False,
        )

    dados = {campo: request.form.get(campo) for campo in hospede_repo.CAMPOS}

    try:
        hospede_repo.atualizar(identificador, dados, usuario_id=usuario["id"])
    except hospede_repo.HospedeDuplicado:
        flash("Já existe outro hóspede com este documento ou e-mail.", "erro")
        return render_template(
            "painel/hospede_form.html",
            usuario=usuario,
            dados=dados,
            hospede=hospede,
            novo=False,
        ), 409
    except hospede_repo.ErroDeHospede as erro:
        flash(str(erro), "erro")
        return render_template(
            "painel/hospede_form.html",
            usuario=usuario,
            dados=dados,
            hospede=hospede,
            novo=False,
        ), 400

    flash("Dados do hóspede atualizados.", "sucesso")
    return redirect(url_for("hospedes.listar"))


@hospedes.route("/<int:identificador>/excluir", methods=["POST"])
@requer_perfil(ADMINISTRADOR)
def excluir(identificador):
    """Apaga um hóspede. Só o administrador.

    Por POST, não por GET: um link de exclusão seria disparado por
    qualquer coisa que pré-carregue URLs — um robô de indexação, o
    navegador adiantando a próxima página. O token CSRF acompanha.

    A recepção cadastra e corrige; apagar cadastro é decisão de
    administrador, e é a única operação do sistema que destrói dado.
    """
    usuario = usuario_atual()

    reservas_do_hospede = reserva_repo.listar_por_hospede(
        identificador, usuario_id=usuario["id"]
    )
    if reservas_do_hospede:
        # A chave estrangeira recusaria de qualquer modo. Conferir antes
        # permite dizer quantas reservas existem, em vez de devolver o
        # erro genérico de integridade referencial.
        flash(
            f"Este hóspede tem {len(reservas_do_hospede)} reserva"
            f"{'s' if len(reservas_do_hospede) != 1 else ''} registrada"
            f"{'s' if len(reservas_do_hospede) != 1 else ''} e não pode ser "
            f"excluído. O histórico de ocupação depende do cadastro.",
            "erro",
        )
        return listar(), 409

    try:
        hospede_repo.excluir(identificador, usuario_id=usuario["id"])
    except Exception as erro:  # noqa: BLE001
        # Resta a conta de acesso: `usuario.hospede_id` também é RESTRICT.
        traduzido = erros.traduzir(erro)
        flash(str(traduzido), "erro")
        return listar(), 409

    flash(f"Hóspede {identificador} excluído.", "sucesso")
    return redirect(url_for("hospedes.listar"))
