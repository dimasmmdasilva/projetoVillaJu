"""A tela de atendimento e a conversão de pedido em hóspede.

É a outra ponta do formulário público: o que entra por lá é uma alegação
de um desconhecido, e o que sai daqui, quando a recepção confirma, é um
registro verificado.

Três assuntos:

**Escape do conteúdo de terceiro.** A mensagem é o único texto livre que
um desconhecido grava no sistema, e por isso o lugar mais provável de uma
tentativa de XSS. É caso de teste obrigatório da Etapa 4.

**A conversão sem redigitação.** Era o incômodo real por trás do pedido
de "cadastro prévio" na face pública. A recepção não copia nada à mão:
acrescenta o documento, que é o único dado que falta.

**O contato permanece.** Convertido, ele não é apagado nem movido — fica
como registro de origem.
"""

import re

import pytest

from app import create_app
from app.config import Configuracao
from app.dados import conexao, contato_repo, hospede_repo

pytestmark = pytest.mark.banco

ADMIN = ("admin", "Villa@Admin2026")
RECEPCAO = ("recepcao", "Villa@Recep2026")
HOSPEDE = ("joao", "Villa@Hospede2026")

PEDIDO = {
    "nome": "Helena Queirós",
    "email": "helena@exemplo.test",
    "telefone": "12 97777-3333",
    "mensagem": "Gostaria de saber a disponibilidade do Dunas em julho.",
    "chale_interesse_id": "5",
    "data_entrada_desejada": "2027-07-10",
    "data_saida_desejada": "2027-07-15",
}


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

    conexao.iniciar_pool(Configuracao(modo_teste=True))
    app = create_app(Configuracao(modo_teste=True))
    app.config["SESSION_COOKIE_SECURE"] = False
    yield app
    conexao.encerrar_pool()


@pytest.fixture
def sessao(aplicacao):
    def entrar(credenciais=RECEPCAO):
        cliente = aplicacao.test_client()
        pagina = cliente.get("/login").data.decode()
        dados = {"login": credenciais[0], "senha": credenciais[1]}
        achado = re.search(r'name="csrf_token" value="([^"]+)"', pagina)
        if achado:
            dados["csrf_token"] = achado.group(1)
        cliente.post("/login", data=dados)
        return cliente

    return entrar


def _aviso(resposta) -> str:
    achados = re.findall(
        r'<p class="aviso aviso-\w+">(.*?)</p>', resposta.data.decode(), re.S
    )
    return achados[0].strip() if achados else ""


# ---------------------------------------------------------------------------
# Caso obrigatório: conteúdo de terceiro é escapado
# ---------------------------------------------------------------------------

def test_script_na_mensagem_e_escapado_na_tela_interna(sessao):
    """Caso obrigatório da Etapa 4.

    A mensagem é o único texto livre que um desconhecido grava. Se ela
    chegasse à tela sem escape, o visitante escreveria código que roda no
    navegador de quem atende — e quem atende está autenticado.
    """
    carga = '<script>alert("xss")</script>'
    identificador = contato_repo.criar(dict(PEDIDO, mensagem=carga))

    html = sessao().get(f"/contatos/{identificador}").data.decode()

    assert "<script>alert" not in html, "a carga foi renderizada como código"
    assert "&lt;script&gt;" in html, "precisa aparecer como texto"


@pytest.mark.parametrize(
    "carga",
    [
        '<img src=x onerror=alert(1)>',
        '"><script>alert(1)</script>',
        "<svg onload=alert(1)>",
        "javascript:alert(1)",
        "<iframe src='https://hostil.exemplo'></iframe>",
    ],
)
def test_cargas_de_xss_na_mensagem_nao_viram_marcacao(sessao, carga):
    identificador = contato_repo.criar(dict(PEDIDO, mensagem=carga))

    html = sessao().get(f"/contatos/{identificador}").data.decode()
    corpo = re.search(r'<p class="mensagem">(.*?)</p>', html, re.S).group(1)

    # A verificação é sobre os sinais de menor e maior, não sobre as
    # palavras. `onload=` aparece dentro do texto escapado — em
    # `&lt;svg onload=alert(1)&gt;` — e ali é inofensivo: sem o `<`
    # literal não existe tag, e sem tag não existe atributo que o
    # navegador execute. A primeira versão deste teste proibia a palavra
    # e acusava o escape correto como se fosse defeito.
    for proibido in ("<script", "<img", "<svg", "<iframe", "<"):
        assert proibido not in corpo, (
            f"{proibido!r} chegou como marcação, não como texto"
        )

    if "<" in carga:
        assert "&lt;" in corpo, "o sinal de menor precisa estar escapado"


def test_script_no_nome_tambem_e_escapado_na_lista(sessao):
    """A lista também mostra dado de terceiro, e também precisa escapar."""
    contato_repo.criar(dict(PEDIDO, nome='<script>alert(1)</script>'))

    html = sessao().get("/contatos/").data.decode()

    assert "<script>alert" not in html
    assert "&lt;script&gt;" in html


# ---------------------------------------------------------------------------
# Controle de acesso
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("credenciais", [ADMIN, RECEPCAO])
def test_administrador_e_recepcionista_atendem(sessao, credenciais):
    assert sessao(credenciais).get("/contatos/").status_code == 200


def test_hospede_nao_alcanca_o_atendimento(sessao):
    """Os pedidos são de terceiros; o hóspede não tem o que fazer aqui."""
    cliente = sessao(HOSPEDE)
    identificador = contato_repo.criar(PEDIDO)

    assert cliente.get("/contatos/").status_code == 403
    assert cliente.get(f"/contatos/{identificador}").status_code == 403
    assert cliente.post(f"/contatos/{identificador}/situacao").status_code == 403
    # A rota de conversão saiu no Bloco 4 da Etapa 5. O alvo passou a ser
    # a de detalhe, porque a propriedade verificada aqui é autorização por
    # perfil, e não a conversão em si.
    assert cliente.get(f"/contatos/{identificador}").status_code == 403


def test_sem_sessao_leva_ao_login(aplicacao):
    resposta = aplicacao.test_client().get("/contatos/", follow_redirects=False)
    assert resposta.status_code == 302
    assert "/login" in resposta.headers["Location"]


def test_o_link_de_contatos_nao_aparece_para_hospede(sessao):
    html = sessao(HOSPEDE).get("/painel").data.decode()
    assert "/contatos/" not in html


# ---------------------------------------------------------------------------
# A lista de atendimento
# ---------------------------------------------------------------------------

def test_a_lista_mostra_os_pedidos(sessao):
    """Um pedido criado pela aplicação, não os da massa.

    Os contatos da massa fictícia têm bytes aleatórios nas colunas
    cifradas e aparecem como não legíveis — comportamento correto, e
    verificado em teste próprio logo abaixo. Procurar por um nome deles
    aqui mediria a massa, não a tela.
    """
    contato_repo.criar(PEDIDO)

    html = sessao().get("/contatos/").data.decode()
    assert "Helena Queirós" in html


def test_o_filtro_por_situacao_funciona(sessao):
    identificador = contato_repo.criar(PEDIDO)
    contato_repo.mudar_situacao(identificador, "descartado")

    novos = sessao().get("/contatos/?situacao=novo").data.decode()
    descartados = sessao().get("/contatos/?situacao=descartado").data.decode()

    assert "Helena Queirós" not in novos
    assert "Helena Queirós" in descartados


def test_situacao_invalida_no_filtro_nao_quebra_a_tela(sessao):
    """Parâmetro de URL é entrada de usuário, mesmo vindo de quem atende."""
    resposta = sessao().get("/contatos/?situacao=' OR 1=1 --")

    assert resposta.status_code == 200
    assert "Traceback" not in resposta.data.decode()


def test_registro_ilegivel_aparece_marcado_e_nao_derruba_a_lista(sessao):
    """A massa fictícia tem bytes aleatórios; a tela sobrevive a eles."""
    with conexao.obter_conexao() as ligacao:
        cursor = ligacao.cursor()
        cursor.execute(
            "UPDATE contato SET nome_cif = %s WHERE id = 1", (b"lixo" * 4,)
        )
        cursor.close()

    resposta = sessao().get("/contatos/")

    assert resposta.status_code == 200
    assert "não legível" in resposta.data.decode()


# ---------------------------------------------------------------------------
# Ciclo de vida pela tela
# ---------------------------------------------------------------------------

def test_mudar_situacao_pela_tela(sessao):
    identificador = contato_repo.criar(PEDIDO)

    resposta = sessao().post(
        f"/contatos/{identificador}/situacao",
        data={"situacao": "em_atendimento"},
        follow_redirects=True,
    )

    assert resposta.status_code == 200
    assert contato_repo.buscar_por_id(identificador)["situacao"] == "em_atendimento"


def test_transicao_proibida_e_recusada_com_mensagem(sessao):
    identificador = contato_repo.criar(PEDIDO)
    contato_repo.mudar_situacao(identificador, "descartado")

    resposta = sessao().post(
        f"/contatos/{identificador}/situacao",
        data={"situacao": "convertido"},
        follow_redirects=True,
    )

    assert "descartado" in _aviso(resposta)
    assert contato_repo.buscar_por_id(identificador)["situacao"] == "descartado"


def test_mudanca_de_situacao_nao_atende_a_get(sessao):
    identificador = contato_repo.criar(PEDIDO)
    assert sessao().get(f"/contatos/{identificador}/situacao").status_code == 405


# ---------------------------------------------------------------------------
# A conversão saiu no Bloco 4 da Etapa 5
#
# Eram seis testes de uma rota que abria o formulário de hóspede
# preenchido com o que o visitante informou e pedia à equipe que
# **completasse o documento** obtido por telefone.
#
# A funcionalidade foi removida, e com ela os testes. O que ficou no
# lugar está em `test_caminho_unico.py`, e verifica a ausência em três
# níveis: a rota responde 404 inclusive ao administrador, nenhuma rota
# registrada aponta para criação, e nenhum módulo em `app/rotas/` chama
# `hospede_repo.criar`.
#
# **O que sobreviveu é `ligar`**, testado logo abaixo, e a diferença
# entre as duas é o ponto do bloco: ligar associa o pedido a um cadastro
# que a própria pessoa fez; converter criava o cadastro com dado digitado
# por terceiro.
#
# Um teste foi reaproveitado em vez de apagado: o que confere que o
# perfil hóspede não alcança o atendimento. Ele mudou de alvo, da rota de
# conversão para a de detalhe, porque a propriedade que ele guarda —
# autorização por perfil — continua valendo e não tem nada a ver com a
# conversão.
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Quando a pessoa já é hóspede
# ---------------------------------------------------------------------------

def test_a_tela_oferece_ligar_ao_cadastro_existente(sessao):
    """A mesma pessoa pode escrever várias vezes ao longo de um ano.

    Criar um segundo cadastro espalharia o histórico dela por duas
    linhas, e faria a busca por documento devolver uma das duas conforme
    a sorte.
    """
    hospede_repo.criar(
        {
            "nome": "Helena Queirós",
            "documento": "321.654.987-00",
            "email": "helena@exemplo.test",
        }
    )
    identificador = contato_repo.criar(PEDIDO)

    html = sessao().get(f"/contatos/{identificador}").data.decode()

    assert "Já existe cadastro com este e-mail" in html
    assert f"/contatos/{identificador}/ligar" in html
    assert "Converter em hóspede" not in html


def test_ligar_ao_cadastro_existente_nao_duplica(sessao):
    hospede = hospede_repo.criar(
        {
            "nome": "Helena Queirós",
            "documento": "321.654.987-00",
            "email": "helena@exemplo.test",
        }
    )
    identificador = contato_repo.criar(PEDIDO)
    antes = hospede_repo.contar()

    sessao().post(
        f"/contatos/{identificador}/ligar",
        data={"hospede_id": str(hospede)},
        follow_redirects=True,
    )

    assert hospede_repo.contar() == antes, "não pode ter criado um segundo"

    ligado = contato_repo.buscar_por_id(identificador)
    assert ligado["hospede_id"] == hospede
    assert ligado["situacao"] == "convertido"


def test_ligar_a_hospede_inexistente_e_recusado(sessao):
    identificador = contato_repo.criar(PEDIDO)

    resposta = sessao().post(
        f"/contatos/{identificador}/ligar",
        data={"hospede_id": "9999"},
        follow_redirects=True,
    )

    assert "não existe" in _aviso(resposta)
    assert contato_repo.buscar_por_id(identificador)["hospede_id"] is None


def test_o_detalhe_mostra_quantas_vezes_a_pessoa_escreveu(sessao):
    contato_repo.criar(PEDIDO)
    segundo = contato_repo.criar(PEDIDO)

    html = sessao().get(f"/contatos/{segundo}").data.decode()

    assert "Pedidos anteriores" in html
    assert "primeira vez" not in html
