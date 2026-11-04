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
    _registrar_cabecalhos_de_seguranca(app)

    return app


def _registrar_rotas(app: Flask) -> None:
    """Registra as rotas da aplicação.

    Por enquanto apenas a verificação de saúde e uma raiz provisória. O
    login, o painel e as demais rotas entram nos blocos seguintes, como
    blueprints de `app/rotas/`.
    """

    @app.route("/saude")
    def saude():
        """Diz se a aplicação subiu, sem revelar nada sobre o sistema.

        Responde com texto fixo de propósito: versão, nome de servidor e
        estado de componentes seriam informação útil a quem está mapeando
        o alvo, e inúteis para quem só quer saber se o serviço respondeu.
        """
        return "ok", 200, {"Content-Type": "text/plain; charset=utf-8"}

    @app.route("/")
    def inicio():
        # Provisória. No Bloco 4 esta rota passa a redirecionar para o
        # painel quando há sessão, e para o login quando não há.
        return "ok", 200, {"Content-Type": "text/plain; charset=utf-8"}


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
