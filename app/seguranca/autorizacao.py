"""Controle de acesso por perfil.

A regra do projeto é curta e absoluta: **a verificação acontece no
servidor, em cada rota**. Esconder um botão no template não é controle de
acesso — é organização de interface. Quem souber o endereço chega lá do
mesmo jeito, digitando na barra do navegador.

    @painel.route("/auditoria")
    @requer_perfil(ADMINISTRADOR)
    def auditoria():
        ...

Dois decoradores, com responsabilidades diferentes:

    @requer_login            exige sessão válida, qualquer perfil
    @requer_perfil(...)      exige sessão válida E um dos perfis listados

O perfil vem da sessão, que o Flask assina com a chave secreta — alterá-lo
no cookie invalida a assinatura e a sessão é descartada. Mas a sessão
guarda um retrato do momento do login, e isso abre uma janela: se um
administrador rebaixar ou desativar uma conta, a sessão aberta continuaria
valendo até expirar, até oito horas depois.

Por isso o usuário é relido do banco a cada requisição, e a leitura é
guardada em `g` para que uma requisição não consulte o banco duas vezes.
Uma conta desativada ou rebaixada perde o acesso na requisição seguinte,
não no dia seguinte.
"""

import functools

from flask import abort, g, redirect, request, session, url_for

ADMINISTRADOR = "administrador"
RECEPCIONISTA = "recepcionista"
HOSPEDE = "hospede"

PERFIS = (ADMINISTRADOR, RECEPCIONISTA, HOSPEDE)

# Chave usada no contexto da requisição para guardar o usuário já lido.
_CHAVE_NO_CONTEXTO = "villaju_usuario"


def usuario_atual():
    """Usuário autenticado, relido do banco, ou None.

    Devolve None quando não há sessão, quando a conta deixou de existir e
    quando ela foi desativada. Nos dois últimos casos a sessão é limpa, de
    modo que a requisição seguinte caia direto no login.

    A leitura fica guardada no contexto da requisição: várias chamadas
    dentro da mesma requisição custam uma consulta só.
    """
    if _CHAVE_NO_CONTEXTO in g:
        return getattr(g, _CHAVE_NO_CONTEXTO)

    identificador = session.get("usuario_id")
    if not identificador:
        setattr(g, _CHAVE_NO_CONTEXTO, None)
        return None

    from app.dados import usuario_repo

    usuario = usuario_repo.buscar_por_id(identificador, usuario_id=identificador)

    if usuario is None or not usuario["ativo"]:
        # A conta sumiu ou foi desativada depois que a sessão abriu.
        session.clear()
        usuario = None

    setattr(g, _CHAVE_NO_CONTEXTO, usuario)
    return usuario


def _negar(usuario, perfis_permitidos):
    """Registra a tentativa e interrompe com 403.

    O que vai ao log é o nome da rota, não o endereço completo: um caminho
    como `/reservas/42` carregaria o identificador do registro, e o log não
    guarda valores. O nome da rota responde à pergunta que importa — que
    área a pessoa tentou alcançar.
    """
    from app.dados import auditoria_repo

    auditoria_repo.registrar(
        auditoria_repo.ACESSO_NEGADO,
        usuario_id=usuario["id"],
        detalhe=(
            f"Rota {request.endpoint} exige "
            f"{' ou '.join(perfis_permitidos)}; perfil da sessão: "
            f"{usuario['perfil']}"
        ),
    )
    abort(403)


def requer_login(vista):
    """Exige sessão válida. Sem ela, encaminha ao login."""

    @functools.wraps(vista)
    def envelope(*args, **kwargs):
        if usuario_atual() is None:
            return redirect(url_for("auth.login"))
        return vista(*args, **kwargs)

    return envelope


def requer_perfil(*perfis_permitidos):
    """Exige sessão válida e um dos perfis informados.

        @requer_perfil(ADMINISTRADOR)
        @requer_perfil(ADMINISTRADOR, RECEPCIONISTA)

    Sem sessão, encaminha ao login — a pessoa talvez só tenha expirado. Com
    sessão e perfil errado, responde 403 e registra `ACESSO_NEGADO`: aí já
    não é engano de navegação, é alguém autenticado tentando uma área que
    não lhe cabe, e isso precisa deixar rastro.
    """
    if not perfis_permitidos:
        raise ValueError("informe ao menos um perfil")

    desconhecidos = set(perfis_permitidos) - set(PERFIS)
    if desconhecidos:
        # Erro de programação, detectado na importação do módulo da rota e
        # não quando alguém tentar acessá-la. Um perfil escrito errado
        # criaria uma rota que ninguém alcança — ou, pior, que todos
        # alcançam, se a comparação fosse invertida em algum refatoramento.
        raise ValueError(f"perfil desconhecido: {sorted(desconhecidos)}")

    def decorador(vista):
        @functools.wraps(vista)
        def envelope(*args, **kwargs):
            usuario = usuario_atual()

            if usuario is None:
                return redirect(url_for("auth.login"))

            if usuario["perfil"] not in perfis_permitidos:
                _negar(usuario, perfis_permitidos)

            return vista(*args, **kwargs)

        return envelope

    return decorador


def tem_perfil(*perfis_permitidos) -> bool:
    """Diz se o usuário atual tem um dos perfis. Para uso nos templates.

    Serve para não exibir um link que levaria a um 403. É conveniência de
    interface, NÃO controle de acesso: a rota continua decorada, e esconder
    o link não protege nada por si.
    """
    usuario = usuario_atual()
    return usuario is not None and usuario["perfil"] in perfis_permitidos
