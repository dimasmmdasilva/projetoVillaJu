"""Face pública: a vitrine da hospedaria.

Esta é a única parte do sistema que um desconhecido alcança. Por isso ela
é construída sob uma regra estreita, que a tarefa 11 da Etapa 4 enuncia:

    a face pública não compartilha sessão com a área autenticada e não
    acessa nenhuma tabela além de `chale` (leitura) e `contato` (escrita).

**A regra foi estreitada na Etapa 5, não abandonada.** Esta face alcança
exatamente **dois módulos de porta**, e cada um tem uma direção só:

    app/servicos/cadastro.py    escreve, não lê   (Bloco 3)
    app/dados/vitrine.py        lê, não escreve   (Bloco 5)

`cadastro` cria hóspede e conta, e nenhuma das suas funções devolve
registro — uma devolve `None`, as outras um booleano e um inteiro de
contagem. `vitrine` responde se um chalé está livre num período, e o
máximo que devolve é um booleano e uma lista de nomes de chalé, que já
estão impressos nesta mesma vitrine.

A propriedade essencial se mantém, e os testes que a verificam mudaram de
"não importa repositório proibido" para "não alcança repositório por fora
das duas portas": **a face pública pode criar um cadastro e perguntar se
uma data está livre, e não tem como exibir dado de hóspede nem de
reserva.**

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
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)

from app.dados import chale_repo, contato_repo, vitrine
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

# Quantos lugares de fotografia cada chalé tem na própria página.
#
# Cinco é o número de blocos provisórios enquanto o material da parceira
# não chega. Quando as fotos chegarem, este valor passa a ser a contagem
# de arquivos encontrados na pasta do chalé.
FOTOS_POR_CHALE = 5

# Quantas amostras de cor existem na folha de estilo.
#
# Doze, e não cinco: a escolha é por `(chalé, posição)`, e com cinco a
# foto 1 de todos os chalés teria a mesma cor — clicar numa miniatura
# pareceria não mudar nada, o que atrapalha justamente quem está
# conferindo se o link funciona.
TOTAL_DE_AMOSTRAS = 12


def apelido_do_chale(nome: str) -> str:
    """O nome do chalé como pedaço de endereço: `Boracéia` vira `boraceia`.

    **Sem acento, e em minúsculas.** Um endereço com acento é
    percentualmente codificado pelo navegador — `/chale/borac%C3%A9ia` —,
    o que destrói justamente o que o endereço legível deveria entregar:
    alguém colar o link no WhatsApp e a outra pessoa ver de que chalé se
    trata antes de abrir.

    A decomposição é NFKD seguida de descarte do que não for ASCII, que é
    a mesma técnica de `correio._nome_de_arquivo`. `cripto.normalizar` não
    serve aqui: ele usa NFKC, que **mantém** o acento, porque existe para
    comparar valores e não para formar endereços.
    """
    import re as expressao
    import unicodedata

    cru = unicodedata.normalize("NFKD", nome or "")
    cru = cru.encode("ascii", "ignore").decode("ascii")
    return expressao.sub(r"[^a-z0-9]+", "-", cru.lower()).strip("-")


def cor_da_foto(chale_id, posicao) -> int:
    """A amostra de cor de uma posição de foto. Provisório, declarado.

    Multiplica o identificador do chalé antes de somar a posição, para
    que dois chalés vizinhos não comecem na mesma cor — o que faria as
    páginas parecerem iguais ao olhar rápido de quem está conferindo os
    links.
    """
    return ((int(chale_id) * FOTOS_POR_CHALE + int(posicao) - 1)
            % TOTAL_DE_AMOSTRAS) + 1


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

        # O apelido vai calculado para o template, e não calculado lá.
        # Jinja2 não tem como remover acento, e passar a função ao molde
        # faria a vitrine depender de uma regra de endereço que não é
        # dela.
        chale["apelido"] = apelido_do_chale(chale["nome"])

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
# A página de cada chalé
# ---------------------------------------------------------------------------

@publico.route("/chale/<apelido>")
def chale(apelido):
    """A página de divulgação de um chalé, com galeria.

    **Endereço pelo nome, e não pelo número.** `/chale/dunas` é
    compartilhável no WhatsApp, salvável nos favoritos e indexável por
    buscador — e o RF10 é sobre divulgação. `/chale/5` não diz nada a
    ninguém.

    **A galeria funciona por navegação**, sem JavaScript. Cada miniatura
    é um link para esta mesma rota com `?foto=N`, e o servidor desenha
    aquela como a principal. Cada foto ganha endereço próprio, pelo mesmo
    argumento que deu endereço ao chalé.

    **Chalé desativado não tem página**, e responde como inexistente. A
    desativação é como a hospedaria tira um chalé de operação; informar
    que ele existe mas está desligado é dado de operação interna, e
    permitiria contar quantos chalés a hospedaria tem somando os que
    respondem de um jeito e de outro.
    """
    try:
        chales = chale_repo.listar(apenas_ativos=True)
    except Exception:  # noqa: BLE001
        _registrador.exception("falha ao carregar a página do chalé")
        abort(503)

    procurado = (apelido or "").strip().lower()
    achado = next(
        (c for c in chales if apelido_do_chale(c["nome"]) == procurado), None
    )

    if achado is None:
        abort(404)

    # A foto pedida, presa à faixa que existe. Fora dela, volta à
    # primeira em vez de dar erro: um `?foto=99` vindo de um link velho
    # ou de alguém mexendo no endereço não é motivo para uma página de
    # erro — é motivo para mostrar o chalé.
    foto = request.args.get("foto", type=int) or 1
    if foto < 1 or foto > FOTOS_POR_CHALE:
        foto = 1

    return render_template(
        "publico/chale.html",
        hospedaria=HOSPEDARIA,
        chale=achado,
        apelido=procurado,
        foto=foto,
        total_de_fotos=FOTOS_POR_CHALE,
        cor_da_foto=cor_da_foto,
    )


# ---------------------------------------------------------------------------
# Consulta de disponibilidade
# ---------------------------------------------------------------------------

@publico.route("/disponibilidade")
@limiter.limit(_limite_publico)
def disponibilidade():
    """Diz se um chalé está livre num período. Sem cadastro, e sem valor.

    **É a porta principal desta face**, e não um apêndice: é o que o
    visitante quer saber antes de qualquer outra coisa, e é o que dá
    motivo para criar conta. Quem descobre que a data está livre tem
    razão para se cadastrar; quem descobre que está ocupada tem razão
    para olhar outro chalé.

    **Por GET, e de propósito.** A consulta não altera nada, e o endereço
    com as datas é compartilhável — metade da utilidade está em poder
    mandar o link no WhatsApp. Um POST exigiria token CSRF e tornaria a
    consulta irrepetível pelo histórico do navegador, sem ganhar
    segurança nenhuma: não há estado a proteger.

    **Nenhum valor.** A decisão é da Etapa 5 e tem razão de negócio.
    Publicar a tabela faria a hospedaria perder a margem de negociar caso
    a caso e entregaria ao concorrente o que ela cobra. O valor aparece
    depois do cadastro confirmado, na tela de reserva.

    **Nada sobre a reserva alheia.** A resposta é livre ou ocupado. Nunca
    quem está hospedado, nunca em que datas a reserva existente começa ou
    termina, nunca quantas reservas há. A garantia não está nesta rota:
    está em `vitrine`, que não tem como devolver essas coisas.
    """
    chales = _chales_para_selecao()
    escolhido = request.args.get("chale", type=int)
    entrada = (request.args.get("entrada") or "").strip()
    saida = (request.args.get("saida") or "").strip()

    contexto = {
        "hospedaria": HOSPEDARIA,
        "chales": chales,
        "escolhido": escolhido,
        "entrada": entrada,
        "saida": saida,
        "hoje": dt.date.today().isoformat(),
        "resposta": None,
        "nome_do_chale": None,
        "entrada_legivel": None,
        "saida_legivel": None,
        "diarias": None,
        "livres": None,
        "erro": None,
    }

    # Sem nada informado, a tela abre com o formulário vazio em vez de
    # assumir uma data. Adivinhar o que o visitante quer consultar
    # produziria uma resposta que parece informação e não é.
    if not escolhido and not entrada and not saida:
        return render_template("publico/disponibilidade.html", **contexto)

    if not escolhido:
        contexto["erro"] = "Escolha um chalé."
        return render_template("publico/disponibilidade.html", **contexto), 400

    try:
        # O período é validado uma vez, e as datas convertidas seguem para
        # a consulta e para a tela. Validar de novo mais abaixo faria o
        # mesmo trabalho duas vezes e abriria a chance de as duas
        # chamadas discordarem numa alteração futura.
        inicio, fim = vitrine.validar_periodo(entrada, saida)
        contexto["resposta"] = vitrine.esta_livre(escolhido, inicio, fim)
    except vitrine.ErroDePeriodo as erro:
        # Mensagem escrita pelo módulo de período, falando de campo e de
        # formato. Pode ir à tela.
        contexto["erro"] = str(erro)
        return render_template("publico/disponibilidade.html", **contexto), 400
    except vitrine.ChaleIndisponivel:
        # Chalé inexistente e chalé desativado dão a mesma resposta. Um
        # chalé desativado é um que a hospedaria tirou de operação, e
        # dizer que ele existe mas está desligado é informação interna —
        # e permitiria contar quantos chalés a hospedaria tem.
        contexto["erro"] = "Chalé não encontrado."
        return render_template("publico/disponibilidade.html", **contexto), 404
    except Exception:  # noqa: BLE001
        _registrador.exception("falha na consulta pública de disponibilidade")
        contexto["erro"] = (
            "Não foi possível consultar agora. Tente novamente em alguns "
            "minutos."
        )
        return render_template("publico/disponibilidade.html", **contexto), 503

    contexto["entrada_legivel"] = inicio.strftime("%d/%m/%Y")
    contexto["saida_legivel"] = fim.strftime("%d/%m/%Y")
    contexto["diarias"] = (fim - inicio).days
    contexto["nome_do_chale"] = next(
        (c["nome"] for c in chales if c["id"] == escolhido), "selecionado"
    )

    # Ocupado: a tela oferece os que estão livres no mesmo período. Sem
    # isso, o visitante testaria os cinco um por um — ou desistiria, que é
    # o resultado provável.
    #
    # **Sem filtrar o chalé consultado, e de propósito.** A primeira
    # versão o removia da lista por precaução, e a sabotagem que tirou
    # esse filtro não quebrou teste nenhum — porque não havia o que
    # quebrar: este caminho só é alcançado quando `esta_livre` respondeu
    # NÃO, e `nomes_livres` devolve apenas os que estão livres. Um chalé
    # ocupado não pode aparecer numa lista de chalés livres, pela mesma
    # condição de sobreposição nas duas consultas.
    #
    # O filtro era código morto que sugeria uma possibilidade inexistente,
    # e código assim convida a próxima pessoa a procurar o caso que ele
    # trataria. A propriedade continua verificada por teste; o que mudou é
    # que ela passou a ser verificada onde de fato mora.
    if not contexto["resposta"]:
        try:
            contexto["livres"] = vitrine.nomes_livres(inicio, fim)
        except Exception:  # noqa: BLE001
            _registrador.warning(
                "disponibilidade servida sem a lista de chalés livres"
            )
            contexto["livres"] = []

    return render_template("publico/disponibilidade.html", **contexto)


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
