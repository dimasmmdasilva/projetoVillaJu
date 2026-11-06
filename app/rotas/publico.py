"""Face pública: a vitrine da hospedaria.

Esta é a única parte do sistema que um desconhecido alcança. Por isso ela
é construída sob uma regra estreita, que a tarefa 11 da Etapa 4 enuncia:

    a face pública não compartilha sessão com a área autenticada e não
    acessa nenhuma tabela além de `chale` (leitura) e `contato` (escrita).

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

import logging

from flask import Blueprint, render_template

from app.dados import chale_repo

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
