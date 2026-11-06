"""A barra de navegação acompanha o banco, não o cookie.

Este arquivo nasceu de um defeito visível na tela, encontrado por inspeção
visual depois de a suíte inteira estar verde.

A sessão guardava uma cópia de `login`, `nome` e `perfil`, gravada no
instante do login, e a barra de navegação exibia essa cópia. O corpo da
página, que relê do banco a cada requisição, mostrava o valor certo — e a
barra, logo acima, mostrava o valor que estava no banco quando aquela
sessão foi aberta:

    corpo:  Olá, Recepção VillaJu. Você está conectado como recepcionista.
    barra:  Recep├º├úo VillaJu · recepcionista

Dois valores para o mesmo dado, na mesma página, porque eram duas fontes.

O caso do nome é cosmético. O do perfil não seria: a barra diria
"recepcionista" para quem já tivesse sido rebaixado a hóspede. O controle
de acesso nunca dependeu dessa cópia — `requer_perfil` e `tem_perfil`
sempre releram do banco —, mas a tela mentiria sobre o que a pessoa é.

A correção foi deixar na sessão apenas o identificador. Estes testes fixam
isso, e o último impede que a cópia volte por descuido.

**Isolamento.** Cada teste altera o registro do usuário, então a massa é
recarregada a cada caso. Um teste que depende da ordem de execução não
prova nada — lição registrada na Etapa 3.
"""

import re

import pytest

from app import create_app
from app.config import Configuracao
from app.dados import conexao

pytestmark = pytest.mark.banco

RECEPCAO = ("recepcao", "Villa@Recep2026")
NOME_NA_MASSA = "Recepção VillaJu"


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
    cliente = aplicacao.test_client()
    pagina = cliente.get("/login").data.decode()
    dados = {"login": RECEPCAO[0], "senha": RECEPCAO[1]}

    # Em modo de teste o CSRF está desligado; o token é enviado quando
    # existe, para que o teste continue valendo se isso mudar.
    achado = re.search(r'name="csrf_token" value="([^"]+)"', pagina)
    if achado:
        dados["csrf_token"] = achado.group(1)

    cliente.post("/login", data=dados)
    return cliente


def _barra(html: str) -> str:
    achado = re.search(
        r'<span class="identificacao">\s*(.*?)\s*</span>', html, re.S
    )
    return achado.group(1).strip() if achado else ""


def _mudar_usuario(coluna: str, valor: str):
    """Altera o registro por SQL direto, sem passar pela aplicação."""
    comandos = {
        "nome": "UPDATE usuario SET nome = %s WHERE login = 'recepcao'",
        "perfil": "UPDATE usuario SET perfil = %s WHERE login = 'recepcao'",
    }
    with conexao.obter_conexao() as ligacao:
        cursor = ligacao.cursor()
        cursor.execute(comandos[coluna], (valor,))
        cursor.close()


# ---------------------------------------------------------------------------
# O que a barra mostra
# ---------------------------------------------------------------------------

def test_a_barra_mostra_o_nome_da_massa(sessao):
    assert NOME_NA_MASSA in _barra(sessao.get("/painel").data.decode())


def test_alterar_o_nome_aparece_sem_novo_login(sessao):
    """O centro do caso: a mesma sessão, o nome novo."""
    _mudar_usuario("nome", "Atendimento Noturno")

    barra = _barra(sessao.get("/painel").data.decode())

    assert "Atendimento Noturno" in barra, (
        "a barra continua mostrando o nome antigo: a sessão virou uma "
        "cópia congelada do registro"
    )
    assert NOME_NA_MASSA not in barra


def test_alterar_o_perfil_aparece_sem_novo_login(sessao):
    """O caso que não é cosmético.

    Com o perfil copiado na sessão, a barra diria "recepcionista" para
    quem já tivesse sido promovido ou rebaixado.
    """
    assert "recepcionista" in _barra(sessao.get("/painel").data.decode())

    _mudar_usuario("perfil", "administrador")

    barra = _barra(sessao.get("/painel").data.decode())
    assert "administrador" in barra
    assert "recepcionista" not in barra


def test_o_perfil_novo_tambem_muda_os_links(sessao):
    """Barra e permissões precisam contar a mesma história.

    `tem_perfil` sempre releu do banco, então os links já acompanhavam o
    perfil real. O texto é que não acompanhava — e barra dizendo uma coisa
    com links dizendo outra é pior do que as duas erradas juntas.
    """
    html = sessao.get("/painel").data.decode()
    assert "/auditoria" not in html, "recepcionista não deveria ver Auditoria"

    _mudar_usuario("perfil", "administrador")

    html = sessao.get("/painel").data.decode()
    assert "/auditoria" in html
    assert "administrador" in _barra(html)


def test_corpo_e_barra_mostram_o_mesmo_nome(sessao):
    """Duas fontes para o mesmo dado divergem. Uma só, não.

    O corpo do painel usa `usuario_atual()`; a barra, o contexto do
    template. Depois da correção, os dois leem do banco.
    """
    _mudar_usuario("nome", "Plantão da Madrugada")

    html = sessao.get("/painel").data.decode()

    assert "Plantão da Madrugada" in _barra(html)
    assert "Olá, Plantão da Madrugada" in html


# ---------------------------------------------------------------------------
# A cópia não deve voltar
# ---------------------------------------------------------------------------

def test_a_sessao_guarda_apenas_o_identificador(sessao):
    """Se alguém reintroduzir `session["perfil"]`, isto acusa.

    O teste olha o conteúdo do cookie de sessão, não a tela. É a diferença
    entre verificar que o sintoma sumiu e verificar que a causa sumiu.
    """
    with sessao.session_transaction() as guardado:
        chaves = set(guardado.keys())

    assert "usuario_id" in chaves

    copias = chaves & {"nome", "perfil", "login"}
    assert not copias, (
        f"a sessão voltou a guardar {sorted(copias)}: é cópia congelada do "
        f"registro do usuário, e envelhece"
    )


def test_conta_desativada_derruba_a_barra(sessao):
    """Desativar a conta durante a sessão tira a barra junto.

    Com a cópia no cookie, a barra continuaria desenhada, anunciando um
    usuário que o sistema já não reconhece.
    """
    assert _barra(sessao.get("/painel").data.decode())

    with conexao.obter_conexao() as ligacao:
        cursor = ligacao.cursor()
        cursor.execute("UPDATE usuario SET ativo = FALSE WHERE login = 'recepcao'")
        cursor.close()

    resposta = sessao.get("/painel", follow_redirects=True)

    assert not _barra(resposta.data.decode())
    assert b"csrf_token" in resposta.data, "deveria ter caído na tela de login"
