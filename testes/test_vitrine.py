"""Consulta pública de disponibilidade.

Etapa 5, Bloco 5. A porta principal da face pública: diz se um chalé está
livre num período, sem exigir cadastro e sem mostrar valor.

O que estes testes guardam, em ordem de importância:

1. **A resposta não revela nada além de livre ou ocupado.** Nunca quem
   está hospedado, nunca em que datas a reserva alheia começa ou termina,
   nunca quantas reservas existem, nunca identificador de reserva.
2. **Nenhum valor aparece.** A decisão de não publicar preço vale aqui
   como em toda a face pública.
3. **A semântica é a mesma da trigger.** Intervalo semiaberto, mesmas
   duas situações ocupando. Se divergissem, a tela diria livre onde a
   reserva seria recusada — ou ocupado onde cabia.
4. **`vitrine` lê e não escreve.** É a direção que a define, e o que a
   separa de `cadastro`, que escreve e não lê.
"""

import datetime as dt
import pathlib
import re

import pytest

from app import create_app
from app.config import Configuracao
from app.dados import conexao, periodo, reserva_repo, vitrine

RAIZ = pathlib.Path(__file__).resolve().parents[1]

# Datas fora da faixa da massa fictícia, para que um teste não dependa do
# que outro deixou. A tarifa semeada cobre até 29/02/2028.
LIVRE_ENTRADA = dt.date(2027, 9, 6)
LIVRE_SAIDA = dt.date(2027, 9, 10)


@pytest.fixture
def aplicacao(conexao_banco):
    from conftest import (
        SCRIPT_DADOS,
        SCRIPT_SCHEMA,
        SCRIPT_TRIGGERS,
        aplicar_script,
        limpar_banco,
    )

    cursor = conexao_banco.cursor()
    limpar_banco(cursor)
    aplicar_script(cursor, SCRIPT_SCHEMA)
    aplicar_script(cursor, SCRIPT_TRIGGERS)
    aplicar_script(cursor, SCRIPT_DADOS)
    cursor.close()

    configuracao = Configuracao(modo_teste=True)
    conexao.iniciar_pool(configuracao)
    app = create_app(configuracao)
    app.config["SESSION_COOKIE_SECURE"] = False
    yield app
    conexao.encerrar_pool()


@pytest.fixture
def visitante(aplicacao):
    """Cliente sem sessão. É assim que o mundo chega ao sistema."""
    return aplicacao.test_client()


def _reservar(aplicacao, chale_id, entrada, saida, situacao="confirmada"):
    with aplicacao.app_context():
        identificador = reserva_repo.criar({
            "chale_id": chale_id,
            "hospede_id": 1,
            "data_entrada": entrada,
            "data_saida": saida,
            "num_pessoas": 2,
        })
        if situacao != "pendente":
            reserva_repo.mudar_situacao(identificador, situacao)
    return identificador


def _consultar(visitante, chale, entrada, saida):
    return visitante.get(
        "/disponibilidade",
        query_string={
            "chale": chale,
            "entrada": entrada.isoformat() if hasattr(entrada, "isoformat")
            else entrada,
            "saida": saida.isoformat() if hasattr(saida, "isoformat")
            else saida,
        },
    )


# ---------------------------------------------------------------------------
# A tela
# ---------------------------------------------------------------------------

def test_a_consulta_e_publica_e_nao_cria_sessao(visitante):
    """Sem cadastro, e sem gastar cookie de quem só está olhando."""
    resposta = visitante.get("/disponibilidade")

    assert resposta.status_code == 200
    assert "Consultar disponibilidade" in resposta.data.decode()

    with visitante.session_transaction() as sessao:
        assert "usuario_id" not in dict(sessao)


def test_sem_datas_a_tela_abre_vazia_e_nao_adivinha(visitante):
    """Adivinhar o período produziria resposta que parece informação."""
    html = visitante.get("/disponibilidade").data.decode()

    assert "Disponível para esta data" not in html
    assert "Ocupado nesta data" not in html


def test_a_consulta_nao_mostra_valor_em_nenhum_dos_dois_resultados(
    aplicacao, visitante
):
    """A decisão de não publicar preço, nos dois caminhos da tela.

    Verificado no resultado livre E no ocupado: um deles poderia ganhar o
    valor numa alteração futura — justamente o livre, que é onde a
    tentação está, porque quem viu que há vaga quer saber quanto custa.
    """
    livre = _consultar(visitante, 1, LIVRE_ENTRADA, LIVRE_SAIDA)
    assert "R$" not in livre.data.decode()

    _reservar(aplicacao, 1, LIVRE_ENTRADA, LIVRE_SAIDA)
    ocupado = _consultar(visitante, 1, LIVRE_ENTRADA, LIVRE_SAIDA)
    assert "R$" not in ocupado.data.decode()


def test_chale_livre_diz_disponivel_e_convida_ao_cadastro(visitante):
    """A redação exata que a especificação pede.

    O convite ao cadastro é o ponto: a consulta existe para dar motivo de
    se cadastrar, e uma tela que apenas dissesse "disponível" deixaria o
    visitante sem o próximo passo.
    """
    html = _consultar(visitante, 1, LIVRE_ENTRADA, LIVRE_SAIDA).data.decode()

    assert "Disponível para esta data" in html
    assert "Complete o cadastro" in html
    assert "/cadastro" in html


def test_chale_ocupado_diz_ocupado_e_oferece_os_livres(aplicacao, visitante):
    """A saída que a tela oferece.

    Sem a lista, o visitante testaria os cinco chalés um por um — ou
    desistiria, que é o resultado provável.
    """
    _reservar(aplicacao, 1, LIVRE_ENTRADA, LIVRE_SAIDA)

    html = _consultar(visitante, 1, LIVRE_ENTRADA, LIVRE_SAIDA).data.decode()

    assert "Ocupado nesta data" in html
    assert "Livres neste mesmo período" in html

    # Os outros quatro chalés da massa aparecem; o consultado, não.
    assert "Embaré" in html or "Camburi" in html


def test_o_chale_consultado_nao_aparece_na_lista_de_livres(
    aplicacao, visitante
):
    """Ele está ocupado — listá-lo como livre contradiria a própria tela.

    **A propriedade vale por construção, e não por um filtro.** A lista
    só aparece quando `esta_livre` respondeu NÃO, e `nomes_livres`
    devolve apenas os livres: as duas consultas usam a mesma condição de
    sobreposição, então um chalé ocupado não pode estar na lista.

    A rota chegou a filtrá-lo explicitamente, por precaução. A sabotagem
    que removeu esse filtro não quebrou nada — e a investigação mostrou
    que não havia o que quebrar. O filtro saiu; este teste ficou, para
    que a propriedade continue verificada e ninguém o reintroduza achando
    que faltava.
    """
    with aplicacao.app_context():
        from app.dados import chale_repo

        nome = chale_repo.buscar_por_id(1)["nome"]

    _reservar(aplicacao, 1, LIVRE_ENTRADA, LIVRE_SAIDA)
    html = _consultar(visitante, 1, LIVRE_ENTRADA, LIVRE_SAIDA).data.decode()

    corte = html.index("Livres neste mesmo período")
    assert nome not in html[corte:]


# ---------------------------------------------------------------------------
# O que a resposta nunca pode conter
# ---------------------------------------------------------------------------

def test_a_resposta_nao_revela_quem_esta_hospedado(aplicacao, visitante):
    """O requisito central do bloco.

    A reserva existente é do hóspede 1 da massa. Nem o nome, nem o
    documento, nem o e-mail dele podem aparecer — e a garantia não está
    na tela: está em `vitrine`, que não tem como consultar `hospede`.
    """
    _reservar(aplicacao, 1, LIVRE_ENTRADA, LIVRE_SAIDA)

    html = _consultar(visitante, 1, LIVRE_ENTRADA, LIVRE_SAIDA).data.decode()

    assert "João" not in html
    assert "Silva" not in html
    assert "hospede" not in html.lower().replace("hospedaria", "")


def test_a_resposta_nao_revela_as_datas_da_reserva_alheia(
    aplicacao, visitante
):
    """As datas entram como parâmetro e não saem como resultado.

    A reserva existente vai de 20 a 25/09, e a consulta é de 21 a 22. A
    tela tem de dizer ocupado sem informar que a reserva começa no dia 20
    — essa é informação sobre a estadia de outra pessoa.
    """
    _reservar(aplicacao, 1, dt.date(2027, 9, 20), dt.date(2027, 9, 25))

    html = _consultar(
        visitante, 1, dt.date(2027, 9, 21), dt.date(2027, 9, 22)
    ).data.decode()

    assert "Ocupado nesta data" in html
    assert "20/09/2027" not in html, "a data de entrada da reserva alheia vazou"
    assert "25/09/2027" not in html, "a data de saída da reserva alheia vazou"
    assert "2027-09-20" not in html
    assert "2027-09-25" not in html


def test_a_resposta_nao_revela_quantas_reservas_existem(aplicacao, visitante):
    """Contagem de reservas é informação de ocupação do negócio.

    Duas reservas no mesmo chalé, em períodos diferentes, produzem a mesma
    tela que uma só.
    """
    _reservar(aplicacao, 1, dt.date(2027, 9, 20), dt.date(2027, 9, 25))
    _reservar(aplicacao, 1, dt.date(2027, 10, 1), dt.date(2027, 10, 5))

    html = _consultar(
        visitante, 1, dt.date(2027, 9, 21), dt.date(2027, 9, 22)
    ).data.decode()

    # Nenhum número solto que possa ser contagem de reserva.
    assert "2 reservas" not in html
    assert "reservas" not in html.lower()


def test_a_resposta_nao_revela_identificador_de_reserva(aplicacao, visitante):
    """Identificador numa tela pública é convite a tentar abri-lo."""
    identificador = _reservar(aplicacao, 1, LIVRE_ENTRADA, LIVRE_SAIDA)

    html = _consultar(visitante, 1, LIVRE_ENTRADA, LIVRE_SAIDA).data.decode()

    assert f"reserva/{identificador}" not in html
    assert f"reserva_id" not in html


def test_chale_desativado_responde_como_inexistente(aplicacao, visitante):
    """Os dois casos juntos, e de propósito.

    Um chalé desativado é um que a hospedaria tirou de operação. Dizer que
    ele existe mas está desligado é informação de operação interna — e
    permitiria contar quantos chalés a hospedaria tem, somando os que
    respondem de um jeito e de outro.
    """
    with aplicacao.app_context():
        from app.dados import chale_repo

        chale_repo.definir_ativo(5, False)

    desativado = _consultar(visitante, 5, LIVRE_ENTRADA, LIVRE_SAIDA)
    inexistente = _consultar(visitante, 9999, LIVRE_ENTRADA, LIVRE_SAIDA)

    assert desativado.status_code == inexistente.status_code == 404
    assert desativado.data == inexistente.data, (
        "a resposta a chalé desativado precisa ser idêntica à de chalé que "
        "nunca existiu"
    )


# ---------------------------------------------------------------------------
# A semântica é a da trigger
# ---------------------------------------------------------------------------

def test_a_saida_de_um_libera_a_entrada_de_outro_no_mesmo_dia(
    aplicacao, visitante
):
    """O intervalo semiaberto, que é a regra da trigger.

    A reserva ocupa de `data_entrada` inclusive até `data_saida`
    exclusive. Se a consulta usasse intervalo fechado, diria ocupado num
    dia em que o chalé está livre — e a hospedaria perderia a diária.
    """
    _reservar(aplicacao, 1, dt.date(2027, 9, 10), dt.date(2027, 9, 15))

    html = _consultar(
        visitante, 1, dt.date(2027, 9, 15), dt.date(2027, 9, 18)
    ).data.decode()

    assert "Disponível para esta data" in html


def test_reserva_cancelada_nao_ocupa(aplicacao, visitante):
    _reservar(aplicacao, 1, LIVRE_ENTRADA, LIVRE_SAIDA, situacao="cancelada")

    html = _consultar(visitante, 1, LIVRE_ENTRADA, LIVRE_SAIDA).data.decode()

    assert "Disponível para esta data" in html


def test_reserva_pendente_ocupa(aplicacao, visitante):
    """Pendente bloqueia, e é isso que o prazo do sinal vai usar.

    O Bloco 7 dá à reserva um prazo de 24 horas para o sinal, e enquanto
    pendente o chalé fica bloqueado. É por isso que a trigger **não
    precisa mudar** naquele bloco: `pendente` já conta como ocupação
    ativa desde a Etapa 1, e esta consulta respeita o mesmo.
    """
    _reservar(aplicacao, 1, LIVRE_ENTRADA, LIVRE_SAIDA, situacao="pendente")

    html = _consultar(visitante, 1, LIVRE_ENTRADA, LIVRE_SAIDA).data.decode()

    assert "Ocupado nesta data" in html


def test_as_situacoes_que_ocupam_sao_as_mesmas_da_trigger():
    """A cópia é inevitável; a divergência não precisa ser.

    Uma lista está em SQL, dentro da trigger, e a outra em Python, dentro
    de `vitrine`. Não há como ter uma só — mas há como impedir que
    discordem em silêncio, lendo a trigger do arquivo e comparando.

    Se divergissem, a tela diria livre onde a reserva seria recusada pela
    trigger, ou ocupado onde cabia. O primeiro caso é pior: o visitante
    se cadastraria, escolheria as datas, e levaria um erro no fim do
    caminho.
    """
    sql = (RAIZ / "banco" / "02_triggers.sql").read_text(encoding="utf-8")

    achados = set(
        re.findall(r"situacao IN \(([^)]+)\)", sql)
    )
    assert achados, "a trigger não traz a lista de situações ativas"

    for bruto in achados:
        situacoes = {
            parte.strip().strip("'") for parte in bruto.split(",")
        }
        assert situacoes == set(vitrine.SITUACOES_QUE_OCUPAM), (
            f"a trigger usa {sorted(situacoes)} e a vitrine usa "
            f"{sorted(vitrine.SITUACOES_QUE_OCUPAM)}"
        )


def test_a_vitrine_concorda_com_o_repositorio_interno(aplicacao):
    """As duas consultas, lado a lado, sobre a mesma massa.

    `reserva_repo.chale_disponivel` é a da face interna, e
    `vitrine.esta_livre` a da pública. São comandos diferentes, escritos
    separadamente, e precisam dar a mesma resposta — senão a recepção e o
    visitante veem coisas distintas sobre o mesmo chalé.
    """
    _reservar(aplicacao, 2, dt.date(2027, 9, 20), dt.date(2027, 9, 25))

    casos = [
        (dt.date(2027, 9, 21), dt.date(2027, 9, 22)),   # dentro
        (dt.date(2027, 9, 18), dt.date(2027, 9, 21)),   # encavala o início
        (dt.date(2027, 9, 24), dt.date(2027, 9, 28)),   # encavala o fim
        (dt.date(2027, 9, 25), dt.date(2027, 9, 28)),   # começa na saída
        (dt.date(2027, 9, 15), dt.date(2027, 9, 20)),   # termina na entrada
        (dt.date(2027, 10, 1), dt.date(2027, 10, 5)),   # longe
    ]

    with aplicacao.app_context():
        for entrada, saida in casos:
            interna = reserva_repo.chale_disponivel(2, entrada, saida)
            publica = vitrine.esta_livre(2, entrada, saida)

            assert interna == publica, (
                f"as duas consultas discordam sobre {entrada} a {saida}: "
                f"interna={interna}, publica={publica}"
            )


# ---------------------------------------------------------------------------
# Validação do período
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "rotulo,entrada,saida",
    [
        ("saída antes da entrada", "2027-09-10", "2027-09-06"),
        ("saída igual à entrada", "2027-09-10", "2027-09-10"),
        ("data sem sentido", "2027-13-45", "2027-09-10"),
        ("texto no lugar da data", "amanha", "2027-09-10"),
        ("entrada em branco", "", "2027-09-10"),
    ],
)
def test_periodo_invalido_e_recusado_com_mensagem(
    visitante, rotulo, entrada, saida
):
    resposta = visitante.get(
        "/disponibilidade",
        query_string={"chale": 1, "entrada": entrada, "saida": saida},
    )

    assert resposta.status_code == 400, rotulo
    assert "aviso-erro" in resposta.data.decode(), rotulo


def test_estadia_longa_demais_e_recusada(visitante):
    """O teto de diárias, que não é regra de negócio e sim defesa.

    Uma consulta de 2027 a 2032, por um ano errado no campo de saída,
    percorreria mais de mil e oitocentas diárias.
    """
    entrada = dt.date(2027, 9, 1)
    saida = entrada + dt.timedelta(days=periodo.MAXIMO_DE_DIARIAS + 1)

    resposta = _consultar(visitante, 1, entrada, saida)

    assert resposta.status_code == 400


def test_o_teto_de_diarias_existe_num_lugar_so():
    """A razão de `periodo` existir, verificada pela identidade do valor.

    Se `reserva_repo` tivesse a própria cópia, as duas poderiam divergir
    — e a face pública recusaria o que a interna aceita, ou o contrário.
    """
    assert reserva_repo.MAXIMO_DE_DIARIAS is periodo.MAXIMO_DE_DIARIAS


def test_consulta_sem_chale_e_recusada(visitante):
    resposta = visitante.get(
        "/disponibilidade",
        query_string={"entrada": "2027-09-06", "saida": "2027-09-10"},
    )

    assert resposta.status_code == 400


# ---------------------------------------------------------------------------
# A vitrine lê e não escreve
# ---------------------------------------------------------------------------

def test_a_vitrine_nao_contem_comando_de_escrita():
    """A direção que define o módulo.

    `cadastro` escreve e não lê; `vitrine` lê e não escreve. Se esta
    ganhasse um `UPDATE`, a face pública passaria a ter escrita em
    `reserva` por um caminho que nenhuma porta declara.

    A tentação concreta tem nome e chega no Bloco 7: a reserva com prazo
    de sinal vencido precisa ser liberada, e varrer aqui seria uma linha.
    Em vez disso, o vencimento é ignorado na leitura, e a varredura
    autoritativa pertence ao lado autenticado — onde a linha de auditoria
    tem autor possível.
    """
    fonte = (RAIZ / "app" / "dados" / "vitrine.py").read_text(encoding="utf-8")

    # Procurado no código, não nos comentários: a docstring do módulo fala
    # de `UPDATE` ao explicar por que não há nenhum.
    codigo = "\n".join(
        linha for linha in fonte.splitlines()
        if not linha.strip().startswith("#")
    )
    corpo = codigo.split('"""', 2)[-1] if '"""' in codigo else codigo

    for comando in ("INSERT", "UPDATE ", "DELETE", "REPLACE", "TRUNCATE"):
        assert comando not in corpo.upper(), (
            f"a vitrine contém um {comando}: ela lê e não escreve"
        )


def test_a_vitrine_nao_alcanca_repositorio_de_pessoa():
    """O módulo de porta não serve de passagem.

    Se `vitrine` importasse `hospede_repo`, a face pública o alcançaria
    de segunda mão — e o estreitamento da regra de isolamento não teria
    valor algum.
    """
    import ast

    arvore = ast.parse(
        (RAIZ / "app" / "dados" / "vitrine.py").read_text(encoding="utf-8")
    )

    importados = set()
    for no in ast.walk(arvore):
        if isinstance(no, ast.ImportFrom) and no.module:
            importados.add(no.module)
            for alvo in no.names:
                importados.add(f"{no.module}.{alvo.name}")
        elif isinstance(no, ast.Import):
            for alvo in no.names:
                importados.add(alvo.name)

    proibidos = {
        "hospede_repo",
        "usuario_repo",
        "reserva_repo",
        "auditoria_repo",
        "tarifa_repo",
        "calculo",
        "contato_repo",
    }
    achados = {n for n in importados if n.split(".")[-1] in proibidos}

    assert not achados, f"a vitrine importa: {sorted(achados)}"


def test_a_vitrine_devolve_apenas_booleano_e_nomes(aplicacao):
    """O teto do que esta face pode saber.

    `esta_livre` devolve um booleano. `nomes_livres` devolve id e nome de
    chalé — e nome de chalé é dado público: está impresso na vitrine para
    qualquer visitante ler.

    Nenhuma capacidade, nenhuma descrição, nenhum dado de reserva.
    """
    with aplicacao.app_context():
        resposta = vitrine.esta_livre(1, LIVRE_ENTRADA, LIVRE_SAIDA)
        assert isinstance(resposta, bool)

        livres = vitrine.nomes_livres(LIVRE_ENTRADA, LIVRE_SAIDA)

    assert livres
    for chale in livres:
        assert set(chale) == {"id", "nome"}, (
            f"nomes_livres devolveu mais do que id e nome: {sorted(chale)}"
        )


def test_a_consulta_publica_e_limitada_por_taxa():
    """A rota recebe o limite, verificado no código.

    Ela é pública, faz uma consulta ao banco por chamada, e o endereço é
    compartilhável — o que a torna alvo fácil para quem quiser medir a
    ocupação da hospedaria varrendo datas.

    Verificado na fonte porque em modo de teste o limitador está
    desligado, e religá-lo aqui mediria a configuração e não a rota.
    """
    fonte = (RAIZ / "app" / "rotas" / "publico.py").read_text(encoding="utf-8")

    corte = fonte.index("def disponibilidade(")
    antes = fonte[:corte]

    assert "@limiter.limit(_limite_publico)" in antes.rsplit(
        "@publico.route", 1
    )[-1], "a rota de disponibilidade não está limitada por taxa"


# ---------------------------------------------------------------------------
# A porta principal
# ---------------------------------------------------------------------------

def test_a_vitrine_oferece_a_consulta_antes_do_cadastro(visitante):
    """A ordem das chamadas na página inicial.

    Consultar vem antes de criar conta, e não depois: quem é convidado a
    se cadastrar antes de saber se há vaga não tem razão nenhuma para
    fazê-lo.

    **A verificação é no CORPO, e não na página inteira.** A primeira
    versão procurava `/disponibilidade` no HTML todo — e a barra de
    navegação, que fica acima e tem o mesmo link, satisfazia a condição
    sozinha. A sabotagem que removeu o botão da chamada principal não
    quebrou nada, porque o menu cobria.

    Botão de chamada e link de menu não são a mesma coisa: o menu é para
    quem já sabe o que procura, e a chamada é para quem acabou de chegar.
    """
    html = visitante.get("/").data.decode()

    # Fora o cabeçalho, que tem a navegação.
    corpo = html.split("</header>", 1)[-1]

    # Procurado o link GERAL — `/disponibilidade` sem parâmetro —, e não
    # qualquer ocorrência do endereço.
    #
    # Os cartões de chalé também apontam para cá, com `?chale=N`, e a
    # primeira versão deste teste aceitava qualquer um deles. A sabotagem
    # que removeu a chamada principal não quebrou nada, porque os botões
    # por chalé cobriam.
    #
    # São coisas diferentes: o botão por chalé é para quem já escolheu, e
    # a chamada principal é para quem acabou de chegar e ainda não sabe o
    # que procura.
    assert re.search(r'href="/disponibilidade"', corpo), (
        "a chamada geral para consultar disponibilidade sumiu do corpo da "
        "página inicial; nem o link do menu nem os botões por chalé a "
        "substituem"
    )
    assert 'class="botao"' in corpo

    # Hoje o corpo nem oferece o cadastro: ele vive no menu, e a chamada
    # do corpo é só a consulta. A verificação é condicional para que o dia
    # em que um convite ao cadastro entrar no corpo — e ele vai entrar,
    # quando houver o que oferecer depois da consulta — ele entre DEPOIS.
    if "/cadastro" in corpo:
        assert corpo.index("/disponibilidade") < corpo.index("/cadastro"), (
            "o convite ao cadastro aparece antes da consulta de "
            "disponibilidade no corpo da página inicial"
        )


def test_cada_cartao_leva_a_pagina_do_chale(visitante):
    """A imagem do cartão é o link, desde o Bloco 13.

    **Mudou de destino.** O cartão levava direto à consulta de
    disponibilidade, com `?chale=N`. Agora leva à página do chalé, de
    onde se consulta — porque há o que ver antes de pedir, e porque
    obrigar a pessoa a decidir datas antes de ver as fotos invertia a
    ordem natural.

    A imagem É o link: é o que se olha primeiro num cartão, e obrigar a
    procurar um botão ao lado para chegar ao mesmo lugar era ruído.
    """
    html = visitante.get("/").data.decode()
    corpo = html.split("</header>", 1)[-1]

    assert re.search(r'<a class="foto-provisoria[^"]*"\s*\n?\s*href="/chale/',
                     corpo), (
        "a imagem do cartão precisa ser o link para a página do chalé"
    )

    assert "Solicitar reserva" not in corpo, (
        "o botão de solicitar reserva saiu do cartão no Bloco 13; ele "
        "passou a ser da página do chalé"
    )


def test_o_chale_escolhido_vem_marcado_no_formulario(visitante):
    """O defeito de tipo da Etapa 4, prendido nesta tela também.

    `request.args.get("chale")` devolve texto; o template compara com
    `chale.id`, que é inteiro; e em Jinja2 `"1" == 1` é falso. Sem o
    `type=int` na rota, a escolha do visitante se perderia em silêncio.
    """
    html = visitante.get(
        "/disponibilidade", query_string={"chale": 3}
    ).data.decode()

    assert re.search(r'value="3"\s+selected', html), (
        "o chalé escolhido não voltou marcado no campo de seleção"
    )


# ---------------------------------------------------------------------------
# As condições da consulta, uma por uma
#
# Duas sabotagens do Bloco 5 escaparam porque a proteção estava em dois
# lugares e cada metade cobria a outra. A resposta não é remover a
# duplicação — ela é desejável — mas descer ao nível em que cada uma mora.
# ---------------------------------------------------------------------------

def test_chale_desativado_nao_aparece_entre_os_livres(aplicacao, visitante):
    """A condição `ativo = TRUE` da consulta de livres.

    Um chalé desativado é um que a hospedaria tirou de operação. Oferecê-lo
    como alternativa produziria um pedido que a recepção teria de recusar —
    e, pior, informaria ao visitante que ele existe, que é exatamente o que
    a resposta 404 da consulta direta se recusa a dizer.

    Esta condição não tinha teste: a verificação de chalé desativado
    cobria a consulta direta, e não a lista de alternativas.
    """
    with aplicacao.app_context():
        from app.dados import chale_repo

        nome = chale_repo.buscar_por_id(5)["nome"]
        chale_repo.definir_ativo(5, False)

    # O chalé 1 é ocupado, para que a lista de alternativas apareça.
    _reservar(aplicacao, 1, LIVRE_ENTRADA, LIVRE_SAIDA)

    html = _consultar(visitante, 1, LIVRE_ENTRADA, LIVRE_SAIDA).data.decode()

    assert "Livres neste mesmo período" in html
    corte = html.index("Livres neste mesmo período")
    assert nome not in html[corte:], (
        f"o chalé desativado {nome!r} foi oferecido como alternativa"
    )

    # E pela função, sem passar pela tela.
    with aplicacao.app_context():
        livres = vitrine.nomes_livres(LIVRE_ENTRADA, LIVRE_SAIDA)

    assert 5 not in {chale["id"] for chale in livres}


def test_a_consulta_de_livres_busca_apenas_o_que_devolve(aplicacao):
    """A consulta estreita, isolada da montagem do dicionário.

    São duas proteções com razões diferentes. A montagem explícita em
    `nomes_livres` garante que o que SAI da função é id e nome. A consulta
    estreita garante que o que ENTRA pela rede é id e nome — e é ela que a
    sabotagem removeu sem quebrar teste nenhum, porque a montagem cobria.

    A razão própria da consulta estreita: `descricao` é um TEXT, e trazer
    cinco deles para montar uma lista que mostra só nomes é trabalho e
    tráfego por nada. Num sistema maior seria o começo de um problema de
    desempenho; aqui é disciplina.
    """
    import re as expressao

    colunas = expressao.search(
        r"SELECT (.+?) FROM chale", vitrine._SQL_NOMES_LIVRES
    )
    assert colunas, "a consulta de livres mudou de forma"

    pedidas = {
        parte.strip().replace("c.", "")
        for parte in colunas.group(1).split(",")
    }

    assert pedidas == {"id", "nome"}, (
        f"a consulta de livres busca mais do que id e nome: {sorted(pedidas)}"
    )


def test_nomes_livres_monta_o_dicionario_em_vez_de_repassar_a_linha(aplicacao):
    """A outra metade: o que sai da função.

    Mesmo que a consulta engorde um dia — por reuso, por descuido, por uma
    alteração que precise de outra coluna para filtrar —, o que `vitrine`
    entrega à face pública continua sendo id e nome.

    Verificado no código e não no resultado, porque com a consulta atual o
    resultado é o mesmo das duas formas. É precisamente por isso que a
    sabotagem escapou.
    """
    import ast
    import pathlib as caminho

    fonte = (
        caminho.Path(__file__).resolve().parents[1]
        / "app" / "dados" / "vitrine.py"
    ).read_text(encoding="utf-8")

    arvore = ast.parse(fonte)
    funcao = next(
        no for no in arvore.body
        if isinstance(no, ast.FunctionDef) and no.name == "nomes_livres"
    )

    # O retorno precisa construir um dicionário literal, e não repassar a
    # linha que o cursor devolveu.
    dicionarios = [
        no for no in ast.walk(funcao) if isinstance(no, ast.Dict)
    ]
    assert dicionarios, (
        "nomes_livres precisa montar o dicionário explicitamente; repassar "
        "a linha do cursor faria qualquer coluna nova vazar para a face "
        "pública"
    )

    chaves = {
        c.value for d in dicionarios for c in d.keys
        if isinstance(c, ast.Constant)
    }
    assert chaves == {"id", "nome"}, (
        f"nomes_livres monta o dicionário com {sorted(chaves)}"
    )
