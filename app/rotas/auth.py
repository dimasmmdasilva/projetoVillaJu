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

import logging

from flask import (
    Blueprint,
    current_app,
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
from app.servicos import recuperacao

auth = Blueprint("auth", __name__)

_registrador = logging.getLogger(__name__)

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


# ---------------------------------------------------------------------------
# Recuperação de senha — Bloco 14 da Etapa 5
#
# A Etapa 5 passou a criar contas com senha escolhida pela própria pessoa,
# e isso tem uma consequência que não é opcional: **ninguém mais conhece a
# senha de ninguém.** Nem a recepção, nem o administrador, nem o banco — só
# o hash Argon2id, que não volta.
#
# Sem estas duas rotas, quem esquece a senha perde a conta.
# ---------------------------------------------------------------------------

@auth.route("/esqueci-senha", methods=["GET", "POST"])
@limiter.limit(_limite_do_login, methods=["POST"])
def esqueci_senha():
    """Pede o link de redefinição por e-mail.

    **Limitada por taxa, e aqui por uma razão a mais que a do login.**
    Esta rota manda e-mail para um endereço que o visitante digitou: sem
    limite, alguém a usaria para encher a caixa de entrada de outra
    pessoa, com o nome da VillaJu no remetente. O limite do login serve,
    porque o perfil de uso é o mesmo — uma pessoa, poucas tentativas.

    **A resposta é sempre a mesma**, exista ou não a conta. Se
    distinguisse, a tela viraria um levantamento de quem é cliente da
    hospedaria — exatamente o que o cadastro se esforça para evitar, e
    seria inútil evitá-lo lá e abri-lo aqui.
    """
    if request.method == "GET":
        return render_template("esqueci_senha.html")

    email = request.form.get("email") or ""

    try:
        recuperacao.solicitar(email, current_app.villaju)
    except recuperacao.ErroDeRecuperacao as erro:
        # Mensagem sobre o que a pessoa digitou — campo em branco,
        # endereço malformado. Nunca sobre a conta existir.
        flash(str(erro), "erro")
        return render_template("esqueci_senha.html", email=email), 400
    except Exception:  # noqa: BLE001
        _registrador.exception("falha ao processar pedido de redefinição")
        flash(
            "Não foi possível processar o pedido agora. "
            "Tente novamente em alguns minutos.",
            "erro",
        )
        return render_template("esqueci_senha.html", email=email), 503

    return redirect(url_for("auth.esqueci_senha_enviado"))


@auth.route("/esqueci-senha/enviado")
def esqueci_senha_enviado():
    """Confirmação, em página própria e alcançada por redirecionamento.

    A redação é condicional de propósito, e é verdadeira nos dois casos:
    *"se este endereço tiver conta, você receberá um link"*.
    """
    return render_template("esqueci_senha_enviado.html")


@auth.route("/redefinir/<token>", methods=["GET", "POST"])
@limiter.limit(_limite_do_login, methods=["POST"])
def redefinir_senha(token):
    """Escolhe a senha nova, pelo link que chegou por e-mail.

    **A abertura da página NÃO consome o token.** Se consumisse, o link
    morreria antes de servir para alguma coisa, e quem fechasse a aba sem
    querer teria de pedir outro. O consumo acontece no envio, e é ele que
    autoriza — é uma atualização condicional, e duas aberturas
    simultâneas do mesmo link não podem ambas ter sucesso.

    **Não entra logado depois de redefinir.** A pessoa escolhe a senha e
    entra com ela. Entrar automaticamente transformaria acesso ao e-mail
    em acesso à conta sem que ninguém digitasse a senha — e o ponto do
    link é provar posse do endereço, não substituir a senha.
    """
    if not recuperacao.token_vale(token):
        # Token inexistente, vencido, já usado e de outro tipo dão a mesma
        # tela. Distinguir ensinaria a quem está sondando se um token
        # existe.
        return render_template("redefinir_senha.html", token=None), 400

    if request.method == "GET":
        return render_template(
            "redefinir_senha.html",
            token=token,
            minimo_da_senha=recuperacao.TAMANHO_MINIMO_DA_SENHA,
        )

    try:
        trocou = recuperacao.redefinir(
            token,
            request.form.get("senha") or "",
            request.form.get("senha_confirmacao") or "",
        )
    except recuperacao.ErroDeRecuperacao as erro:
        # A senha é validada ANTES de o token ser consumido: um erro de
        # digitação não queima o link.
        flash(str(erro), "erro")
        return render_template(
            "redefinir_senha.html",
            token=token,
            minimo_da_senha=recuperacao.TAMANHO_MINIMO_DA_SENHA,
        ), 400
    except Exception:  # noqa: BLE001
        _registrador.exception("falha ao redefinir a senha")
        flash(
            "Não foi possível trocar a senha agora. "
            "Tente novamente em alguns minutos.",
            "erro",
        )
        return render_template(
            "redefinir_senha.html",
            token=token,
            minimo_da_senha=recuperacao.TAMANHO_MINIMO_DA_SENHA,
        ), 503

    if not trocou:
        # O token venceu entre a abertura da página e o envio, ou outra
        # requisição o consumiu primeiro.
        return render_template("redefinir_senha.html", token=None), 400

    flash("Senha redefinida. Entre com a senha nova.", "sucesso")
    return redirect(url_for("auth.login"))
