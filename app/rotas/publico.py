"""Face pública: a vitrine da hospedaria.

Esta é a única parte do sistema que um desconhecido alcança. Por isso ela
é construída sob uma regra estreita, que a tarefa 11 da Etapa 4 enuncia:

    a face pública não compartilha sessão com a área autenticada e não
    acessa nenhuma tabela além de `chale` (leitura) e `contato` (escrita).

**A regra foi estreitada na Etapa 5, não abandonada.** O autocadastro do
hóspede precisa escrever em `hospede` e em `usuario`, e por isso esta
face alcança agora um terceiro módulo: `app.servicos.cadastro`, que tem
duas entradas e **nenhuma função que devolva dado** — uma devolve `None`,
a outra um booleano.

A propriedade essencial se mantém, e o teste que a verifica mudou de
"não importa repositório proibido" para "entre os módulos que escrevem
em tabela de pessoa, importa apenas `cadastro`": **a face pública pode
criar um cadastro, e não tem como ler nenhum.**

**Por que a regra importa mais do que parece.** Um sítio de divulgação é
o alvo mais exposto que o projeto tem: não exige senha, é indexado por
buscadores e recebe tráfego automatizado o tempo todo. Se uma rota daqui
tiver motivo para ler `hospede` ou `reserva`, qualquer defeito nela passa
a ser um defeito com acesso a dado pessoal. Não dando esse motivo a
nenhuma rota, a classe inteira de falha deixa de existir — e isso é
verificável, não apenas prometido: há teste conferindo que nenhum
repositório proibido é importado aqui.

**Nenhuma rota desta face exige sessão.** O visitante é anônimo e
continua anônimo. O link para a área da equipe é apenas um link; quem o
seguir encontra o login, que é onde o controle de acesso começa.

**Conteúdo provisório.** As fotos e os textos definitivos da parceira
ainda não chegaram. A tarefa 12 prevê isso: a página é construída com
conteúdo provisório desde o início, de modo que a troca seja substituição
de arquivos em `static/img/chales/`, sem mexer em template nem em rota.
"""

import datetime as dt
import logging

from flask import (
    Blueprint,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)

from app.dados import chale_repo, contato_repo
from app.extensoes import limiter
from app.servicos import cadastro

publico = Blueprint("publico", __name__)

_registrador = logging.getLogger(__name__)

# Dados da hospedaria. Ficam aqui, e não no banco, porque são texto
# institucional de uma única instalação — criar tabela e tela de edição
# para cinco linhas que mudam uma vez por ano seria trabalho sem retorno.
# Quando a parceira enviar o texto definitivo, troca-se aqui.
HOSPEDARIA = {
    "nome": "VillaJu",
    # Título da aba do navegador. Curto de propósito: a aba tem pouco
    # espaço e corta o que passar disso, e é também o que aparece como
    # título no resultado de busca.
    "titulo_curto": "VillaJu — Praia da Juréia",
    "chamada": "Cinco chalés na Praia da Juréia, em São Sebastião.",
    "apresentacao": (
        "A VillaJu fica na Alameda Jubéris, a poucos minutos a pé da "
        "Praia da Juréia. São cinco chalés independentes, cercados por "
        "Mata Atlântica, cada um com cozinha equipada e varanda. "
        "Piscina e churrasqueira são compartilhadas."
    ),
    "endereco": "Alameda Jubéris, 48 — Praia da Juréia",
    "cidade": "São Sebastião, São Paulo",
    "regiao": (
        "No litoral norte paulista, entre Boiçucanga e Maresias. "
        "Cerca de três horas de carro da capital pela Rio–Santos."
    ),
}

# Até as fotos chegarem, cada chalé recebe um bloco de cor como imagem de
# apresentação. É conteúdo provisório declarado, não lacuna esquecida: a
# página não fica com espaço vazio nem com imagem quebrada, e a troca é
# substituir o arquivo em `static/img/chales/`.
#
# O que varia é a CLASSE, não a cor. A primeira versão escrevia a cor num
# atributo `style=` do template, e o navegador a bloqueou: a política de
# conteúdo da aplicação declara `style-src 'self'`, sem `unsafe-inline`.
# Os blocos apareciam vazios na tela.
#
# A correção foi tirar o estilo do HTML, não afrouxar a política —
# permitir estilo embutido para resolver um bloco decorativo enfraqueceria
# a defesa contra XSS no sistema inteiro, e por um motivo cosmético.
TOTAL_DE_CORES = 5


@publico.route("/")
def inicio():
    """A vitrine. É a primeira página que qualquer visitante vê.

    Até a Etapa 3 a raiz redirecionava ao login. Isso estava errado como
    produto: um hóspede em potencial chegava ao endereço da hospedaria e
    encontrava um pedido de senha. Quem precisa do login são três
    pessoas; o restante do mundo quer ver os chalés.

    A página é servida igual a todos, autenticados ou não. O que muda
    para quem tem sessão é apenas o destino do link da equipe, resolvido
    no template — nenhuma decisão de conteúdo depende de quem está
    olhando, porque não há nada aqui que seja de alguém.
    """
    # Chalé desativado não aparece na vitrine. A desativação é como a
    # hospedaria tira um chalé de operação — reforma, manutenção — e
    # anunciá-lo produziria pedido de reserva que a recepção teria de
    # recusar.
    #
    # A leitura é protegida porque esta é a página inicial da hospedaria:
    # a mais visível que existe, e a única que um desconhecido encontra
    # pelo buscador. Com o banco fora do ar, mostrar a apresentação e a
    # localização — que são texto fixo — é melhor do que devolver erro.
    # O visitante continua sabendo onde fica e como é o lugar; o que
    # falta é a lista de chalés, e a página diz isso.
    try:
        chales = chale_repo.listar(apenas_ativos=True)
        banco_disponivel = True
    except Exception:  # noqa: BLE001
        _registrador.warning("vitrine servida sem a lista de chalés")
        chales = []
        banco_disponivel = False

    for posicao, chale in enumerate(chales):
        chale["cor_provisoria"] = posicao % TOTAL_DE_CORES + 1

    return render_template(
        "publico/inicio.html",
        hospedaria=HOSPEDARIA,
        chales=chales,
        banco_disponivel=banco_disponivel,
    )


# ---------------------------------------------------------------------------
# Pedido de contato
# ---------------------------------------------------------------------------

# Nome do campo-armadilha.
#
# Escolhido para parecer plausível a um preenchedor automático e não
# significar nada para a aplicação. "assunto" é campo comum em formulário
# de contato, então um robô que preencha tudo o que encontra vai cair nele.
CAMPO_ARMADILHA = "assunto"


def _limite_publico():
    """Lê o limite da configuração no momento da requisição.

    Lido a cada chamada, e não na importação, para que o valor venha da
    aplicação em execução — é o que permite a um teste subir uma
    aplicação com limite próprio sem alterar as demais.
    """
    from flask import current_app

    return current_app.config.get("LIMITE_PUBLICO", "3 per minute")


@publico.route("/contato", methods=["GET", "POST"])
@limiter.limit(_limite_publico, methods=["POST"])
def contato():
    """O formulário de pedido de contato, e o seu envio.

    **É o único ponto do sistema que aceita escrita de um desconhecido.**
    Por isso recebe três defesas, que entram no Bloco 3: token CSRF,
    limitação de taxa por origem e campo-armadilha. O token já está aqui,
    porque sem ele o formulário não funcionaria fora do modo de teste.

    **O que o formulário NÃO faz.** Não cria reserva e não cadastra
    hóspede. Grava em `contato`, que é tabela de quarentena: o que chega
    de fora entra nela e só sai pelas mãos da recepção. O raciocínio está
    na Etapa 4 de `docs/contexto.md` — em resumo, escrita anônima em
    `reserva` deixaria o log de auditoria sem autor, e em `hospede`
    transformaria o índice de busca num oráculo de enumeração.

    **A validação é no servidor.** `required` e `maxlength` no HTML são
    conveniência para quem preenche; não existem para quem envia a
    requisição por fora do navegador, que é o que um robô faz.
    """
    chales = _chales_para_selecao()

    if request.method == "GET":
        # A vitrine encaminha para cá com o chalé já escolhido, para que
        # o visitante não precise procurá-lo de novo numa lista.
        return render_template(
            "publico/contato.html",
            hospedaria=HOSPEDARIA,
            chales=chales,
            hoje=dt.date.today().isoformat(),
            dados={"chale_interesse_id": request.args.get("chale", type=int)},
        )

    dados = {
        campo: request.form.get(campo)
        for campo in (
            "nome",
            "email",
            "telefone",
            "mensagem",
            "data_entrada_desejada",
            "data_saida_desejada",
        )
    }

    # Como inteiro, e não como texto. O template marca o chalé escolhido
    # comparando com o identificador, que é inteiro, e em Jinja2 "5" == 5
    # é falso — num envio recusado, a escolha do visitante se perdia e ele
    # precisava procurar o chalé outra vez na lista.
    dados["chale_interesse_id"] = request.form.get("chale_interesse_id", type=int)

    # ----------------------------------------------------------------
    # Campo-armadilha
    #
    # Um campo que pessoa alguma vê e que todo preenchedor automático
    # encontra, porque robô lê o HTML e não a tela. Vindo preenchido, o
    # envio é descartado **em silêncio**: a resposta é a mesma da de um
    # envio bem-sucedido.
    #
    # O silêncio é a parte que importa. Responder "você parece um robô"
    # entrega ao autor do robô exatamente a informação de que ele precisa
    # para ajustá-lo e tentar de novo. Devolvendo a página de confirmação,
    # ele conclui que funcionou e vai embora.
    #
    # Fica o registro em log, sem o conteúdo: interessa contar quantos
    # envios automatizados foram barrados, não guardar o que eles
    # escreveram.
    # ----------------------------------------------------------------
    # Sem `strip()`: descarta qualquer conteúdo, inclusive só espaços.
    # Um navegador envia o campo vazio, e nenhuma pessoa legítima digita
    # nele — nem espaço. Aparar o valor antes de testar deixaria passar um
    # preenchedor que escreve espaços, que é um caso real.
    if request.form.get(CAMPO_ARMADILHA):
        _registrador.info(
            "envio descartado pelo campo-armadilha, origem %s",
            request.remote_addr,
        )
        return redirect(url_for("publico.contato_enviado"))

    try:
        contato_repo.criar(dados, ip_origem=request.remote_addr)
    except contato_repo.ErroDeContato as erro:
        # A mensagem destas exceções é escrita pelo próprio repositório e
        # fala de campo e formato, nunca de banco ou de biblioteca.
        flash(str(erro), "erro")
        return render_template(
            "publico/contato.html",
            hospedaria=HOSPEDARIA,
            chales=chales,
            hoje=dt.date.today().isoformat(),
            dados=dados,
        ), 400
    except Exception:  # noqa: BLE001
        _registrador.exception("falha ao gravar pedido de contato")
        flash(
            "Não foi possível enviar o seu pedido agora. "
            "Tente novamente em alguns minutos.",
            "erro",
        )
        return render_template(
            "publico/contato.html",
            hospedaria=HOSPEDARIA,
            chales=chales,
            hoje=dt.date.today().isoformat(),
            dados=dados,
        ), 503

    return redirect(url_for("publico.contato_enviado"))


@publico.route("/contato/enviado")
def contato_enviado():
    """Confirmação, em página própria.

    Rota separada, alcançada por redirecionamento, para que recarregar a
    página não reenvie o formulário — e para que o visitante não fique
    com um POST no histórico do navegador.
    """
    return render_template("publico/contato_enviado.html", hospedaria=HOSPEDARIA)


# ---------------------------------------------------------------------------
# Autocadastro do hóspede
# ---------------------------------------------------------------------------

@publico.route("/cadastro", methods=["GET", "POST"])
@limiter.limit(_limite_publico, methods=["POST"])
def criar_conta():
    """O formulário de cadastro, e o seu envio.

    **As mesmas três defesas do formulário de contato**, porque é o mesmo
    tipo de porta: token CSRF, limitação de taxa por origem e
    campo-armadilha. A diferença é que esta porta cria conta, o que a
    torna mais atraente para abuso, não menos.

    **A resposta é sempre a mesma.** Erro de formulário — campo em
    branco, senha curta, e-mail malformado — é mostrado, porque fala
    apenas do que a pessoa digitou. Tudo o mais termina na página de
    confirmação, inclusive o caso em que o endereço já tem conta. O
    raciocínio está no cabeçalho de `app/servicos/cadastro.py`: responder
    diferente transformaria o formulário num levantamento da lista de
    clientes.
    """
    if request.method == "GET":
        return render_template(
            "publico/cadastro.html",
            hospedaria=HOSPEDARIA,
            minimo_da_senha=cadastro.TAMANHO_MINIMO_DA_SENHA,
            dados={},
        )

    dados = {
        campo: request.form.get(campo)
        for campo in ("nome", "documento", "email", "telefone")
    }

    # A senha vai para o serviço e **não volta ao template**. Num envio
    # recusado, os outros campos são devolvidos preenchidos para que a
    # pessoa não digite tudo de novo; a senha, não. Devolvê-la a
    # escreveria no HTML, e dali ela chegaria ao histórico do navegador,
    # ao cache de página e a qualquer registro de proxy no caminho.
    dados["senha"] = request.form.get("senha")
    dados["senha_confirmacao"] = request.form.get("senha_confirmacao")

    # Campo-armadilha, com a mesma lógica da rota de contato: descarte em
    # silêncio, resposta idêntica à de um envio bem-sucedido, registro em
    # log sem o conteúdo.
    if request.form.get(CAMPO_ARMADILHA):
        _registrador.info(
            "cadastro descartado pelo campo-armadilha, origem %s",
            request.remote_addr,
        )
        return redirect(url_for("publico.conta_solicitada"))

    def recusar(mensagem: str, estado: int):
        flash(mensagem, "erro")
        devolver = {
            campo: dados[campo]
            for campo in ("nome", "documento", "email", "telefone")
        }
        return render_template(
            "publico/cadastro.html",
            hospedaria=HOSPEDARIA,
            minimo_da_senha=cadastro.TAMANHO_MINIMO_DA_SENHA,
            dados=devolver,
        ), estado

    try:
        cadastro.registrar(dados, current_app.villaju)
    except cadastro.ErroDeCadastro as erro:
        return recusar(str(erro), 400)
    except Exception:  # noqa: BLE001
        _registrador.exception("falha ao processar cadastro público")
        return recusar(
            "Não foi possível concluir o seu cadastro agora. "
            "Tente novamente em alguns minutos.",
            503,
        )

    return redirect(url_for("publico.conta_solicitada"))


@publico.route("/cadastro/enviado")
def conta_solicitada():
    """Confirmação, em página própria e alcançada por redirecionamento.

    A mensagem é condicional de propósito — *"se este endereço estiver
    disponível, você receberá um link"*. Ela é verdadeira nos dois casos
    e informativa em nenhum, que é exatamente o que se quer: quem acabou
    de se cadastrar entende o que fazer, e quem está sondando endereços
    não aprende nada.
    """
    return render_template(
        "publico/cadastro_enviado.html", hospedaria=HOSPEDARIA
    )


@publico.route("/confirmar/<token>")
@limiter.limit(_limite_publico)
def confirmar_conta(token):
    """Ativa a conta pelo link do e-mail.

    **Limitada por taxa**, como o Bloco 2 previu. Sem o limite, esta rota
    aceitaria tentativas ilimitadas de adivinhar token — o que não
    quebraria os 256 bits de aleatoriedade, mas custaria uma consulta ao
    banco por tentativa e serviria de alavanca para esgotar o servidor.

    **Uma página, duas mensagens.** Token vencido, inexistente, já usado
    e de outro tipo produzem a mesma resposta, sem distinguir entre eles
    — é o requisito do Bloco 2. O sucesso é distinto da falha, e precisa
    ser: uma pessoa que acabou de confirmar a conta tem de saber que pode
    entrar.
    """
    try:
        ativada = cadastro.confirmar(token, current_app.villaju)
    except Exception:  # noqa: BLE001
        _registrador.exception("falha ao confirmar cadastro")
        ativada = False

    return render_template(
        "publico/cadastro_confirmado.html",
        hospedaria=HOSPEDARIA,
        ativada=ativada,
    ), (200 if ativada else 400)


def _chales_para_selecao() -> list:
    """Apenas id e nome, para o campo de seleção.

    Falha de banco devolve lista vazia em vez de derrubar a página: o
    visitante ainda consegue enviar o pedido sem indicar chalé, e um
    pedido sem chalé é melhor do que pedido nenhum.
    """
    try:
        return [
            {"id": chale["id"], "nome": chale["nome"]}
            for chale in chale_repo.listar(apenas_ativos=True)
        ]
    except Exception:  # noqa: BLE001
        _registrador.warning("formulário de contato servido sem a lista de chalés")
        return []


@publico.errorhandler(429)
def limite_excedido(erro):
    """Resposta ao limite de envios, em página e não em erro cru.

    O 429 do Flask-Limiter sai como página de erro do servidor, que
    assusta quem só clicou duas vezes no botão achando que não tinha
    enviado. A mensagem aqui diz o que aconteceu e o que fazer, sem
    revelar qual é o limite — informação que serviria a quem está
    medindo até onde pode ir.
    """
    return render_template(
        "publico/limite.html", hospedaria=HOSPEDARIA
    ), 429
