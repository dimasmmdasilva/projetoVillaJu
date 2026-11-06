"""As três defesas do formulário público.

É o único ponto do sistema que aceita escrita de um desconhecido, e por
isso recebe três proteções de naturezas diferentes:

| Defesa          | Contra o quê                                      |
|-----------------|---------------------------------------------------|
| Token CSRF      | sítio hostil enviando em nome de quem visita      |
| Limite por IP   | envio em massa de uma mesma origem                |
| Campo-armadilha | preenchedor automático que lê o HTML              |

**Duas delas ficam desligadas no modo de teste**, de propósito: um teste
que exercita o formulário dez vezes seria bloqueado no quarto, e falharia
por motivo que não tem relação com o que verifica. Este arquivo as
**religa para si** — é a única forma de exercitá-las, e a lacuna de não
fazê-lo já apareceu antes no projeto: a Etapa 2 fechou com o caso
obrigatório "formulário sem token CSRF é rejeitado" registrado como NÃO
COBERTO, exatamente por isso.
"""

import re

import pytest

from app import create_app
from app.config import Configuracao
from app.dados import conexao, contato_repo

pytestmark = pytest.mark.banco

PEDIDO = {
    "nome": "Helena Queirós",
    "email": "helena@exemplo.test",
    "telefone": "12 97777-3333",
    "mensagem": "Gostaria de saber a disponibilidade em julho.",
}


@pytest.fixture
def banco(conexao_banco):
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

    conexao.iniciar_pool(Configuracao(modo_teste=True))
    yield cursor
    conexao.encerrar_pool()


def _aplicacao(**ajustes):
    app = create_app(Configuracao(modo_teste=True))
    app.config["SESSION_COOKIE_SECURE"] = False
    app.config.update(ajustes)
    return app


@pytest.fixture
def visitante(banco):
    return _aplicacao().test_client()


def _token(cliente, rota="/contato") -> str:
    html = cliente.get(rota).data.decode()
    achado = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert achado, "a página precisa trazer o token"
    return achado.group(1)


def _quantos_contatos() -> int:
    with conexao.obter_cursor() as cursor:
        cursor.execute("SELECT COUNT(*) FROM contato")
        return cursor.fetchone()[0]


# ---------------------------------------------------------------------------
# 1. Token CSRF
# ---------------------------------------------------------------------------

@pytest.fixture
def com_csrf(banco):
    """Aplicação com a proteção religada.

    `WTF_CSRF_ENABLED` é desligada pela configuração de teste. Religá-la
    aqui é o que permite exercitar a defesa — sem isto, o teste passaria
    com a proteção removida do código, que é o defeito que ele existe
    para impedir.
    """
    # Apenas a configuração é religada. **Não** se chama `csrf.init_app`
    # aqui: a primeira versão desta fixture chamava, e com isso os testes
    # passariam mesmo que a fábrica da aplicação tivesse deixado de
    # instalar a proteção — a fixture a instalaria por eles.
    #
    # Foi uma sabotagem que mostrou isso: removendo `csrf.init_app` de
    # `extensoes.registrar`, os testes de CSRF continuaram passando, e
    # quem acusou foram outros, porque os templates quebraram ao chamar
    # `csrf_token()`. Detecção por dano colateral não é detecção.
    #
    # Sem a chamada, a proteção só existe se a aplicação real a instalou,
    # que é o que se quer verificar.
    app = _aplicacao(WTF_CSRF_ENABLED=True)
    return app.test_client()


def test_o_formulario_traz_o_token(com_csrf):
    assert "csrf_token" in com_csrf.get("/contato").data.decode()


def test_envio_sem_token_e_rejeitado(com_csrf):
    """O caso obrigatório que ficou descoberto na Etapa 2."""
    antes = _quantos_contatos()
    resposta = com_csrf.post("/contato", data=PEDIDO)

    assert resposta.status_code == 400
    assert _quantos_contatos() == antes, "nada pode ter sido gravado"


def test_envio_com_token_adulterado_e_rejeitado(com_csrf):
    token = _token(com_csrf)
    adulterado = token[:-4] + ("aaaa" if not token.endswith("aaaa") else "bbbb")

    antes = _quantos_contatos()
    resposta = com_csrf.post(
        "/contato", data=dict(PEDIDO, csrf_token=adulterado)
    )

    assert resposta.status_code == 400
    assert _quantos_contatos() == antes


def test_envio_com_token_de_outra_sessao_e_rejeitado(com_csrf, banco):
    """O token vale para a sessão que o recebeu, e só para ela.

    Sem esse vínculo, bastaria a um sítio hostil buscar um token na
    página pública — que é aberta a todos — e embuti-lo no próprio
    formulário.
    """
    outra = _aplicacao(WTF_CSRF_ENABLED=True)
    token_alheio = _token(outra.test_client())

    antes = _quantos_contatos()
    resposta = com_csrf.post(
        "/contato", data=dict(PEDIDO, csrf_token=token_alheio)
    )

    assert resposta.status_code == 400
    assert _quantos_contatos() == antes


def test_envio_com_o_proprio_token_passa(com_csrf):
    """A prova de que os testes acima recusam pelo motivo certo.

    Sem ele, uma rota que recusasse tudo passaria nos três anteriores.
    """
    antes = _quantos_contatos()
    resposta = com_csrf.post(
        "/contato", data=dict(PEDIDO, csrf_token=_token(com_csrf))
    )

    assert resposta.status_code == 302
    assert _quantos_contatos() == antes + 1


# ---------------------------------------------------------------------------
# 2. Limitação de taxa por origem
# ---------------------------------------------------------------------------

@pytest.fixture
def com_limite(banco):
    """Aplicação com a limitação religada e teto baixo.

    Dois por minuto para que o teste seja rápido. O valor real vem da
    configuração; o que se verifica aqui é o mecanismo, não o número.
    """
    from app import extensoes

    # `RATELIMIT_ENABLED` na configuração, e `enabled` DEPOIS do
    # `init_app`: a primeira versão deste teste ligava o atributo antes, e
    # o `init_app` o sobrescrevia a partir da configuração — o limitador
    # continuava desligado e o teste media um formulário sem defesa.
    app = _aplicacao(LIMITE_PUBLICO="2 per minute", RATELIMIT_ENABLED=True)
    extensoes.limiter.init_app(app)
    extensoes.limiter.enabled = True

    cliente = app.test_client()
    yield cliente

    extensoes.limiter.enabled = False
    extensoes.limiter.reset()


def test_envios_demais_da_mesma_origem_sao_barrados(com_limite):
    codigos = [
        com_limite.post("/contato", data=PEDIDO).status_code
        for _ in range(4)
    ]

    assert codigos[:2] == [302, 302], "os primeiros precisam passar"
    assert codigos[2:] == [429, 429], "os seguintes precisam ser barrados"


def test_o_que_foi_barrado_nao_chega_ao_banco(com_limite):
    # Pela diferença, não pelo total: a massa fictícia já traz quatro
    # pedidos de contato.
    antes = _quantos_contatos()

    for _ in range(4):
        com_limite.post("/contato", data=PEDIDO)

    assert _quantos_contatos() - antes == 2, (
        "apenas os dois envios aceitos podem ter sido gravados"
    )


def test_origem_diferente_nao_herda_o_bloqueio(com_limite):
    """O limite é por origem, e precisa ser mesmo.

    Um limite global transformaria o formulário num alvo trivial: bastaria
    a um robô esgotá-lo para que a hospedaria parasse de receber pedidos
    de qualquer pessoa. Seria negação de serviço construída pela própria
    defesa.
    """
    for _ in range(3):
        com_limite.post("/contato", data=PEDIDO)

    outra_origem = com_limite.post(
        "/contato", data=PEDIDO, environ_base={"REMOTE_ADDR": "203.0.113.77"}
    )

    assert outra_origem.status_code == 302


def test_a_consulta_da_vitrine_nao_e_barrada(com_limite):
    """O limite é do envio, não da leitura.

    Barrar quem só olha a página afastaria visitante legítimo e não
    conteria robô algum — ler a vitrine não custa nada à hospedaria.
    """
    for _ in range(4):
        com_limite.post("/contato", data=PEDIDO)

    assert com_limite.get("/").status_code == 200
    assert com_limite.get("/contato").status_code == 200


def test_a_resposta_ao_limite_e_pagina_e_nao_erro_cru(com_limite):
    for _ in range(3):
        resposta = com_limite.post("/contato", data=PEDIDO)

    html = resposta.data.decode()
    assert resposta.status_code == 429
    assert "Muitos envios" in html
    assert "VillaJu" in html, "precisa ser a página do sítio, não erro do servidor"


def test_a_resposta_ao_limite_nao_revela_o_teto(com_limite):
    """Dizer "2 por minuto" entrega a medida a quem está sondando."""
    for _ in range(3):
        resposta = com_limite.post("/contato", data=PEDIDO)

    html = resposta.data.decode()
    for revelador in ("2 per minute", "per minute", "2 por minuto"):
        assert revelador not in html


# ---------------------------------------------------------------------------
# 3. Campo-armadilha
# ---------------------------------------------------------------------------

def test_o_campo_existe_no_formulario(visitante):
    html = visitante.get("/contato").data.decode()
    assert 'name="assunto"' in html


def test_o_campo_e_inalcancavel_por_quem_nao_ve_a_tela(visitante):
    """A parte da armadilha que é questão de acessibilidade, não de truque.

    Sem estes três atributos, a armadilha pegaria justamente quem navega
    por leitor de tela ou por teclado — e descartar em silêncio o pedido
    de uma pessoa cega seria pior do que não ter armadilha alguma.
    """
    html = visitante.get("/contato").data.decode()

    bloco = re.search(
        r'<div class="armadilha".*?</div>', html, re.S
    )
    assert bloco, "o campo precisa estar no invólucro próprio"
    trecho = bloco.group(0)

    assert 'aria-hidden="true"' in trecho
    assert 'tabindex="-1"' in trecho
    assert 'autocomplete="off"' in trecho


def test_o_campo_esta_escondido_por_classe_e_nao_por_estilo(visitante):
    """A política de conteúdo bloqueia `style=`; a lição do Bloco 1."""
    import pathlib

    html = visitante.get("/contato").data.decode()
    assert 'class="armadilha"' in html

    css = (
        pathlib.Path(__file__).resolve().parents[1]
        / "app" / "static" / "css" / "villaju.css"
    ).read_text(encoding="utf-8")

    assert ".armadilha" in css, "a classe existe no HTML mas não no CSS"
    assert "position: absolute" in css


def test_envio_com_a_armadilha_preenchida_nao_grava(visitante):
    antes = _quantos_contatos()
    visitante.post("/contato", data=dict(PEDIDO, assunto="comprar seguidores"))

    assert _quantos_contatos() == antes


def test_o_descarte_e_indistinguivel_de_um_envio_aceito(visitante):
    """O silêncio é a defesa, não o descarte.

    Responder "você parece um robô" entrega ao autor do robô exatamente a
    informação de que ele precisa para ajustá-lo. Vendo a confirmação,
    ele conclui que funcionou e vai embora.
    """
    legitimo = visitante.post("/contato", data=PEDIDO)
    robo = visitante.post(
        "/contato", data=dict(PEDIDO, assunto="comprar seguidores")
    )

    assert robo.status_code == legitimo.status_code
    assert robo.headers["Location"] == legitimo.headers["Location"]

    pagina_robo = visitante.get(robo.headers["Location"]).data.decode()
    assert "Pedido enviado" in pagina_robo
    for revelador in ("robô", "robo", "automat", "descart", "spam", "bloque"):
        assert revelador not in pagina_robo.lower()


def test_o_descarte_nao_enfileira_mensagem_na_sessao(visitante):
    """O vazamento adiado, que a página de confirmação esconde.

    Este teste nasceu de uma sabotagem que **não foi detectada**. Ela
    acrescentava um `flash("Envio automatizado detectado e descartado.")`
    antes do redirecionamento, e os testes continuaram passando — porque
    a página de confirmação não renderiza mensagens, e o aviso ficou
    guardado na sessão.

    Guardado, não perdido: apareceria na página seguinte que o visitante
    abrisse. O autor do robô voltaria à vitrine e leria ali exatamente a
    informação que o silêncio existe para negar.

    Verificar a sessão, e não a tela, é o que alcança isso.
    """
    visitante.post("/contato", data=dict(PEDIDO, assunto="comprar seguidores"))

    with visitante.session_transaction() as sessao:
        assert not dict(sessao), (
            f"o descarte deixou algo na sessão: {dict(sessao)}"
        )


def test_a_armadilha_vazia_nao_atrapalha_o_envio_legitimo(visitante):
    """O navegador envia o campo vazio; isso não pode barrar ninguém."""
    antes = _quantos_contatos()
    resposta = visitante.post("/contato", data=dict(PEDIDO, assunto=""))

    assert resposta.status_code == 302
    assert _quantos_contatos() == antes + 1


def test_a_armadilha_so_com_espacos_tambem_e_descartada(visitante):
    antes = _quantos_contatos()
    visitante.post("/contato", data=dict(PEDIDO, assunto="   "))

    assert _quantos_contatos() == antes


def test_a_armadilha_nao_guarda_o_que_o_robo_escreveu(visitante):
    """Descartar é descartar: nada do envio entra no banco."""
    visitante.post(
        "/contato",
        data=dict(PEDIDO, assunto="https://sitio-hostil.exemplo/x"),
    )

    for contato in contato_repo.listar():
        assert "sitio-hostil" not in str(contato.get("mensagem") or "")
        assert "sitio-hostil" not in str(contato.get("nome") or "")


# ---------------------------------------------------------------------------
# As três juntas
# ---------------------------------------------------------------------------

def test_as_tres_defesas_estao_ativas_fora_do_modo_de_teste():
    """O que a configuração real entrega.

    Os testes acima religam as defesas uma a uma, o que é necessário para
    exercitá-las — e é também o que torna possível esquecer de ligá-las
    em produção sem que nenhum teste reclame. Este olha a configuração
    que a aplicação real usa.
    """
    import os

    if not os.environ.get("VILLAJU_SECRET_KEY"):
        pytest.skip("configuração real ausente neste ambiente")

    real = Configuracao()

    assert real.WTF_CSRF_ENABLED is True
    assert real.LIMITE_PUBLICO, "o limite do formulário público precisa existir"
    assert real.TESTING is False
