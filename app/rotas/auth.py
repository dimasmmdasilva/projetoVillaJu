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
    session.permanent = True


# Por que só o identificador vai para a sessão
#
# A primeira versão gravava também login, nome e perfil, e a barra de
# navegação os exibia. O cookie virava uma cópia do registro do usuário,
# congelada no instante do login — e cópia congelada envelhece.
#
# O defeito apareceu na Etapa 3, visível na tela: o corpo da página
# mostrava "Recepção VillaJu" e a barra, no mesmo instante e na mesma
# página, mostrava a versão corrompida que estava no banco quando aquela
# sessão foi aberta. Dois valores para o mesmo dado, porque eram duas
# fontes.
#
# O caso do nome é cosmético. O do perfil não seria: a barra diria
# "recepcionista" para quem já tivesse sido rebaixado a hóspede. O
# controle de acesso nunca dependeu dessa cópia — `requer_perfil` e
# `tem_perfil` sempre releram do banco —, mas a tela mentiria.
#
# Com apenas o identificador na sessão, existe uma fonte só:
# `usuario_atual()`, que relê o registro a cada requisição, guarda o
# resultado no contexto para não repetir a consulta, e derruba a sessão se
# a conta sumiu ou foi desativada.


@auth.route("/login", methods=["GET", "POST"])
@limiter.limit(_limite_do_login, methods=["POST"])
def login():
    if session.get("usuario_id"):
        return redirect(url_for("painel.inicio"))

    if request.method == "GET":
        return render_template("login.html")

    login_informado = (request.form.get("login") or "").strip()
    senha_informada = request.form.get("senha") or ""

    # Busca que aceita os dois formatos: o apelido da equipe (`admin`,
    # `recepcao`) e o endereço de e-mail do hóspede.
    #
    # **Por que o hóspede entra pelo e-mail e a conta não guarda e-mail.**
    # O login dele é o HMAC-SHA256 do endereço, o mesmo índice que
    # `hospede.email_busca` usa — e a tradução acontece aqui. A coluna
    # `login` é texto claro com restrição de unicidade; guardar o endereço
    # nela desfaria a cifragem que a tabela `hospede` aplica ao mesmo
    # dado, e um UNIQUE sobre e-mail em claro é por si um oráculo de
    # enumeração.
    #
    # Os dois caminhos fazem exatamente uma consulta, de modo que o tempo
    # de resposta não distingue um apelido de equipe de um endereço de
    # hóspede.
    usuario = usuario_repo.buscar_por_login_ou_email(login_informado)

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
