"""Entrada e saída do sistema.

Um formulário de login é a porta do sistema, e por isso concentra ataques.
As defesas aqui são cinco, e cada uma cobre um buraco que as outras
deixam:

1. **Mensagem de erro idêntica** para login inexistente e senha errada.
   Diferenciar as duas transforma o formulário numa ferramenta de
   descoberta de contas válidas.

2. **Tempo de resposta idêntico** nos dois casos. A mensagem igual não
   basta: responder em microssegundos para login inexistente e em
   setecentos milissegundos para senha errada revela a mesma informação
   pelo relógio.

3. **Limitação de taxa por origem.** Sem ela, as duas defesas acima só
   atrasam quem tenta uma senha por vez — e nada impede tentar milhares.

4. **Regeneração da sessão** após autenticar, contra fixação de sessão.

5. **Registro em auditoria** de todo acesso e de toda recusa.
"""

from flask import (
    Blueprint,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from app.dados import auditoria_repo, usuario_repo
from app.extensoes import limiter
from app.seguranca import senhas

auth = Blueprint("auth", __name__)

# Uma só mensagem para toda recusa, qualquer que seja o motivo: login
# inexistente, senha errada ou conta desativada. Quem está tentando
# descobrir contas válidas não aprende nada com ela.
MENSAGEM_DE_RECUSA = "Login ou senha inválidos."


def _limite_do_login():
    """Lê o limite da configuração da aplicação, no momento da requisição."""
    from flask import current_app

    return current_app.config.get("LIMITE_LOGIN", "5 per minute")


def _iniciar_sessao(usuario: dict) -> None:
    """Abre a sessão do usuário autenticado.

    `session.clear()` antes de gravar qualquer coisa é a defesa contra
    fixação de sessão: descarta todo conteúdo que existisse antes de
    autenticar, inclusive o que um atacante tivesse conseguido plantar.

    Na prática, com a sessão assinada do Flask, limpar e regravar produz
    um cookie novo — o identificador anterior deixa de valer.
    """
    session.clear()
    session["usuario_id"] = usuario["id"]
    session["login"] = usuario["login"]
    session["nome"] = usuario["nome"]
    session["perfil"] = usuario["perfil"]
    session.permanent = True


def usuario_da_sessao():
    """Dados do usuário autenticado, ou None."""
    if not session.get("usuario_id"):
        return None
    return {
        "id": session["usuario_id"],
        "login": session.get("login"),
        "nome": session.get("nome"),
        "perfil": session.get("perfil"),
    }


@auth.route("/login", methods=["GET", "POST"])
@limiter.limit(_limite_do_login, methods=["POST"])
def login():
    if session.get("usuario_id"):
        return redirect(url_for("painel.inicio"))

    if request.method == "GET":
        return render_template("login.html")

    login_informado = (request.form.get("login") or "").strip()
    senha_informada = request.form.get("senha") or ""

    usuario = usuario_repo.buscar_por_login(login_informado)

    if usuario is None:
        # Gasta o mesmo tempo de uma verificação real. Sem isso, a
        # ausência da conta seria detectável pelo tempo de resposta.
        senhas.consumir_tempo_equivalente()
        auditoria_repo.registrar(
            auditoria_repo.LOGIN_FALHA,
            detalhe="Tentativa com login inexistente",
        )
        flash(MENSAGEM_DE_RECUSA, "erro")
        return render_template("login.html"), 401

    senha_confere = senhas.verificar(usuario["senha_hash"], senha_informada)

    if not senha_confere:
        auditoria_repo.registrar(
            auditoria_repo.LOGIN_FALHA,
            usuario_id=usuario["id"],
            detalhe="Senha incorreta",
        )
        flash(MENSAGEM_DE_RECUSA, "erro")
        return render_template("login.html"), 401

    if not usuario["ativo"]:
        # Mesma mensagem das anteriores: uma conta desativada que
        # respondesse diferente revelaria que ela existe.
        auditoria_repo.registrar(
            auditoria_repo.LOGIN_FALHA,
            usuario_id=usuario["id"],
            detalhe="Conta desativada",
        )
        flash(MENSAGEM_DE_RECUSA, "erro")
        return render_template("login.html"), 401

    # Senha certa e conta ativa. É o único momento em que a senha em claro
    # está disponível para regerar o hash com a calibração atual.
    if senhas.precisa_recalibrar(usuario["senha_hash"]):
        usuario_repo.atualizar_hash(
            usuario["id"],
            senhas.gerar_hash(senha_informada),
            usuario_id=usuario["id"],
        )

    _iniciar_sessao(usuario)
    usuario_repo.registrar_acesso(usuario["id"], usuario_id=usuario["id"])
    auditoria_repo.registrar(
        auditoria_repo.LOGIN_OK,
        usuario_id=usuario["id"],
        detalhe="Acesso autorizado",
    )

    return redirect(url_for("painel.inicio"))


@auth.route("/logout", methods=["POST"])
def logout():
    """Encerra a sessão.

    Aceita apenas POST, e por isso exige token CSRF. Com um logout via
    GET, bastaria a um sítio hostil embutir uma imagem apontando para
    `/logout` para derrubar a sessão de quem visitasse a página.
    """
    identificador = session.get("usuario_id")

    if identificador:
        auditoria_repo.registrar(
            auditoria_repo.LOGOUT,
            usuario_id=identificador,
            detalhe="Sessão encerrada pelo usuário",
        )

    session.clear()
    flash("Sessão encerrada.", "sucesso")
    return redirect(url_for("auth.login"))
