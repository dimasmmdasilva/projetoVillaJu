"""Pacote da aplicação VillaJu.

Aqui mora a fábrica `create_app`, que monta a aplicação Flask a partir da
configuração do ambiente.

O padrão de fábrica, em vez de uma variável global `app`, existe por três
razões. Permite criar uma aplicação por teste, com configuração própria e
sem estado compartilhado. Adia a leitura do ambiente para o momento da
criação, em vez de fazê-la na importação do módulo. E deixa explícito o
que a aplicação precisa para funcionar, em vez de espalhar a montagem por
vários arquivos.
"""

from flask import Flask

from app.config import Configuracao


def create_app(configuracao=None, iniciar_banco: bool = True) -> Flask:
    """Monta e devolve a aplicação.

    `configuracao` permite injetar uma configuração pronta, o que os
    testes usam. Quando ausente, ela é lida do ambiente.

    `iniciar_banco` permite montar a aplicação sem exigir um MySQL no ar.
    Serve aos testes que verificam apenas configuração e rotas, e ao
    diagnóstico de ambiente.
    """
    configuracao = configuracao or Configuracao()

    app = Flask(__name__)
    app.config.update(configuracao.como_dicionario())

    # Guardado para quem precisar dos atributos que não são do Flask —
    # parâmetros do banco, caminhos do certificado, limites de taxa.
    app.villaju = configuracao

    from app import extensoes

    extensoes.registrar(app)

    if iniciar_banco:
        from app.dados import conexao

        conexao.iniciar_pool(configuracao)

    _registrar_rotas(app)
    _registrar_contexto(app)
    _registrar_cabecalhos_de_seguranca(app)
    _registrar_paginas_de_erro(app)

    return app


def _registrar_paginas_de_erro(app: Flask) -> None:
    """Página de erro genérica, com o detalhe só no log (seção 6.4).

    Uma mensagem de erro detalhada na resposta conta a quem está sondando
    o sistema qual biblioteca falhou, qual consulta quebrou e às vezes
    qual arquivo existe no disco. O texto exibido vem deste mapa fixo e
    nunca da exceção.
    """
    import logging

    from flask import render_template

    registrador = logging.getLogger(__name__)

    MENSAGENS = {
        400: "A requisição não pôde ser entendida.",
        401: "É preciso entrar no sistema para continuar.",
        403: "Você não tem permissão para acessar esta página.",
        404: "Página não encontrada.",
        405: "Este endereço não aceita esse tipo de requisição.",
        413: "O conteúdo enviado é grande demais.",
        429: "Tentativas demais em pouco tempo. Aguarde um momento.",
        500: "Ocorreu um erro inesperado. A equipe foi notificada.",
    }

    def responder(codigo):
        def manipulador(erro):
            if codigo >= 500:
                # Só o erro interno vai ao log com rastro completo: os
                # demais são resposta esperada a uma requisição ruim, e
                # registrá-los todos afogaria o que importa.
                registrador.exception("erro interno na requisição")
            return (
                render_template(
                    "erro.html",
                    codigo=codigo,
                    mensagem=MENSAGENS.get(codigo, MENSAGENS[500]),
                ),
                codigo,
            )

        return manipulador

    for codigo in MENSAGENS:
        app.register_error_handler(codigo, responder(codigo))


def _registrar_rotas(app: Flask) -> None:
    """Registra os blueprints e as rotas soltas da aplicação."""
    from app.rotas.auth import auth
    from app.rotas.chales import chales
    from app.rotas.contatos import contatos
    from app.rotas.hospedes import hospedes
    from app.rotas.painel import painel
    from app.rotas.publico import publico
    from app.rotas.reservas import reservas

    # A face pública vem primeiro na leitura por ser a única alcançável
    # sem sessão. A ordem de registro não altera o roteamento.
    app.register_blueprint(publico)
    app.register_blueprint(auth)
    app.register_blueprint(painel)
    app.register_blueprint(hospedes)
    app.register_blueprint(chales)
    app.register_blueprint(contatos)
    app.register_blueprint(reservas)

    @app.route("/saude")
    def saude():
        """Diz se a aplicação subiu, sem revelar nada sobre o sistema.

        Responde com texto fixo de propósito: versão, nome de servidor e
        estado de componentes seriam informação útil a quem está mapeando
        o alvo, e inúteis para quem só quer saber se o serviço respondeu.
        """
        return "ok", 200, {"Content-Type": "text/plain; charset=utf-8"}

    # A raiz `/` é servida pelo blueprint público, em `app/rotas/publico.py`.
    #
    # Até a Etapa 3 havia aqui uma rota que redirecionava ao painel ou ao
    # login. Isso estava errado como produto: um hóspede em potencial
    # chegava ao endereço da hospedaria e encontrava um pedido de senha.
    # Quem precisa do login são três pessoas; o restante do mundo quer ver
    # os chalés. O acesso da equipe passou a ser um link no cabeçalho da
    # vitrine.


def _registrar_contexto(app: Flask) -> None:
    """Expõe o usuário da sessão a todos os templates.

    Evita que cada rota tenha de passar o mesmo dado adiante — e evita o
    esquecimento que faria a barra superior sumir numa tela qualquer.
    """

    @app.context_processor
    def injetar():
        from app.seguranca.autorizacao import tem_perfil, usuario_atual

        try:
            # Relido do banco, não copiado do cookie: ver a nota em
            # `app/rotas/auth.py` sobre por que a sessão guarda apenas o
            # identificador.
            atual = usuario_atual()
        except Exception:  # noqa: BLE001
            # A página de erro também estende `base.html`. Se o banco for
            # justamente o que falhou, a barra não é desenhada — o que é
            # melhor do que a página de erro falhar ao explicar o erro.
            atual = None

        return {
            "sessao_usuario": atual,
            "tem_perfil": tem_perfil,
        }

    @app.template_filter("iso")
    def formatar_iso(valor):
        """Data no formato que o campo <input type="date"> espera.

        O valor pode vir do banco como `date` ou do formulário recusado
        como texto. O filtro aceita os dois e devolve string vazia para
        nulo, o que o campo entende como "em branco".
        """
        import datetime as dt

        if valor is None or valor == "":
            return ""
        if isinstance(valor, (dt.date, dt.datetime)):
            return valor.strftime("%Y-%m-%d")
        return str(valor)


def _registrar_cabecalhos_de_seguranca(app: Flask) -> None:
    """Acrescenta cabeçalhos de segurança a toda resposta.

    São instruções ao navegador sobre o que ele pode fazer com a página.
    Nenhum deles substitui a validação no servidor — são uma segunda
    camada, útil quando a primeira falha.

    A política de conteúdo é restritiva: o navegador só carrega recursos
    da própria origem, e nada de script embutido na página. Com ela, uma
    injeção de `<script>` que escape do escape do Jinja2 ainda assim não
    executa.
    """

    @app.after_request
    def aplicar(resposta):
        resposta.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; "
            "script-src 'self'; "
            "style-src 'self'; "
            "img-src 'self' data:; "
            "form-action 'self'; "
            "frame-ancestors 'none'; "
            "base-uri 'self'",
        )

        # Impede o navegador de adivinhar o tipo do conteúdo. Sem isso, um
        # arquivo enviado por usuário poderia ser interpretado como HTML e
        # executar script.
        resposta.headers.setdefault("X-Content-Type-Options", "nosniff")

        # A página não pode ser carregada dentro de moldura de outro sítio,
        # o que impede o ataque em que uma camada invisível sobre a tela
        # faz o usuário clicar onde não pretendia.
        resposta.headers.setdefault("X-Frame-Options", "DENY")

        # Não envia o endereço da página atual ao navegar para fora. Sem
        # isso, um identificador de reserva na URL vazaria para o sítio de
        # destino pelo cabeçalho de referência.
        resposta.headers.setdefault(
            "Referrer-Policy", "strict-origin-when-cross-origin"
        )

        # Exige HTTPS nas visitas seguintes. Só faz sentido quando a
        # resposta já veio por HTTPS; em modo de teste é omitido.
        if not app.config.get("TESTING"):
            resposta.headers.setdefault(
                "Strict-Transport-Security", "max-age=31536000"
            )

        return resposta
