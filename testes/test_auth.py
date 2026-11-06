"""Autenticação: entrada, saída e as defesas do formulário de login.

O formulário de login é a porta do sistema e concentra ataques. Os testes
cobrem as cinco defesas previstas e, principalmente, verificam que elas
não revelam informação por caminhos indiretos — mensagem diferente, tempo
de resposta diferente, código de estado diferente.
"""

import re
import time

import pytest

from app import create_app
from app.config import Configuracao
from app.dados import auditoria_repo, conexao
from app.rotas.auth import MENSAGEM_DE_RECUSA

pytestmark = pytest.mark.banco

ADMIN = ("admin", "Villa@Admin2026")
RECEPCAO = ("recepcao", "Villa@Recep2026")
HOSPEDE = ("joao", "Villa@Hospede2026")


@pytest.fixture(scope="module")
def aplicacao(conexao_banco):
    """Aplicação ligada ao banco de testes, com a massa de dados carregada.

    O schema, as triggers e os dados são aplicados uma vez por arquivo.
    Depois disso a aplicação usa o próprio pool, como em produção.
    """
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
    app = create_app(configuracao)
    yield app
    conexao.encerrar_pool()


@pytest.fixture
def cliente(aplicacao):
    return aplicacao.test_client()


def _entrar(cliente, credenciais=ADMIN):
    login, senha = credenciais
    return cliente.post(
        "/login", data={"login": login, "senha": senha}, follow_redirects=False
    )


def _eventos(acao):
    with conexao.obter_cursor(dicionario=True) as cursor:
        cursor.execute(
            "SELECT * FROM log_auditoria WHERE acao = %s ORDER BY id", (acao,)
        )
        return cursor.fetchall()


# ---------------------------------------------------------------------------
# A tela de login
# ---------------------------------------------------------------------------

def test_tela_de_login_responde(cliente):
    assert cliente.get("/login").status_code == 200


def test_tela_de_login_tem_token_csrf(cliente):
    """Sem o token, um sítio hostil enviaria o formulário pelo usuário."""
    assert b"csrf_token" in cliente.get("/login").data


def test_campo_de_senha_e_mascarado(cliente):
    assert b'type="password"' in cliente.get("/login").data


def test_tela_de_login_nao_lista_usuarios(cliente):
    """Nenhuma dica sobre quais contas existem."""
    corpo = cliente.get("/login").data.decode().lower()
    for conta in ("admin", "recepcao", "joao", "vania"):
        assert conta not in corpo


def test_raiz_sem_sessao_mostra_a_vitrine_e_nao_o_login(cliente):
    """A raiz deixou de ser o login na Etapa 4.

    O que continua valendo, e é o que importa aqui: nenhuma rota da área
    autenticada é alcançável sem sessão. O teste seguinte cobre isso.
    """
    resposta = cliente.get("/", follow_redirects=False)

    assert resposta.status_code == 200
    assert "VillaJu" in resposta.data.decode()


def test_painel_sem_sessao_leva_ao_login(cliente):
    resposta = cliente.get("/painel", follow_redirects=False)
    assert resposta.status_code == 302
    assert "/login" in resposta.headers["Location"]


# ---------------------------------------------------------------------------
# Entrada bem-sucedida
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("credenciais", [ADMIN, RECEPCAO, HOSPEDE])
def test_credencial_correta_entra(cliente, credenciais):
    resposta = _entrar(cliente, credenciais)
    assert resposta.status_code == 302
    assert "/painel" in resposta.headers["Location"]


def test_painel_mostra_o_usuario_conectado(cliente):
    _entrar(cliente, ADMIN)
    corpo = cliente.get("/painel").data.decode()
    assert "Vania Silva" in corpo
    assert "administrador" in corpo


def test_painel_mostra_resumo_sem_dado_pessoal(cliente):
    """Contagens, não conteúdo: a tabela hospede é contada, não lida."""
    _entrar(cliente, ADMIN)
    corpo = cliente.get("/painel").data.decode()
    assert "Chalés ativos" in corpo
    assert "Hóspedes cadastrados" in corpo


def test_sessao_guarda_o_identificador_e_so(cliente, aplicacao):
    """A sessão identifica quem entrou. Nada além disso.

    Até a Etapa 3 este teste exigia `sessao["perfil"] == "recepcionista"`,
    porque o login copiava login, nome e perfil para o cookie. A cópia foi
    removida: ela envelhecia, e a barra de navegação passou a mostrar o
    valor congelado no instante do login enquanto o corpo da página
    mostrava o valor atual do banco.

    Quem precisa do perfil chama `usuario_atual()`, que relê o registro.
    A verificação detalhada está em `testes/test_barra_de_navegacao.py`.
    """
    _entrar(cliente, RECEPCAO)
    with cliente.session_transaction() as sessao:
        assert sessao["usuario_id"]
        assert "perfil" not in sessao


def test_sessao_nao_guarda_a_senha(cliente):
    """Nem o hash: a sessão viaja no cookie, assinada mas legível."""
    _entrar(cliente, ADMIN)
    with cliente.session_transaction() as sessao:
        conteudo = str(dict(sessao)).lower()
        assert "senha" not in conteudo
        assert "argon2" not in conteudo
        assert "Villa@Admin2026".lower() not in conteudo


def test_entrada_atualiza_o_ultimo_acesso(cliente):
    with conexao.obter_cursor() as cursor:
        cursor.execute("UPDATE usuario SET ultimo_acesso = NULL WHERE login = 'admin'")

    _entrar(cliente, ADMIN)

    with conexao.obter_cursor() as cursor:
        cursor.execute("SELECT ultimo_acesso FROM usuario WHERE login = 'admin'")
        assert cursor.fetchone()[0] is not None


def test_quem_ja_entrou_e_levado_ao_painel(cliente):
    _entrar(cliente, ADMIN)
    resposta = cliente.get("/login", follow_redirects=False)
    assert resposta.status_code == 302
    assert "/painel" in resposta.headers["Location"]


# ---------------------------------------------------------------------------
# Recusa sem revelar informação
# ---------------------------------------------------------------------------

def test_senha_errada_e_recusada(cliente):
    resposta = cliente.post("/login", data={"login": "admin", "senha": "errada"})
    assert resposta.status_code == 401
    assert MENSAGEM_DE_RECUSA in resposta.data.decode()


def test_login_inexistente_e_recusado(cliente):
    resposta = cliente.post(
        "/login", data={"login": "nao-existe", "senha": "qualquer"}
    )
    assert resposta.status_code == 401
    assert MENSAGEM_DE_RECUSA in resposta.data.decode()


def test_mensagem_e_identica_nos_dois_casos(cliente):
    """A defesa central contra enumeração de contas.

    Se a resposta para login inexistente diferisse da resposta para senha
    errada, o formulário viraria uma ferramenta de descoberta de contas
    válidas — e, sabendo quais existem, o ataque por senha se concentra.
    """
    com_login_valido = cliente.post(
        "/login", data={"login": "admin", "senha": "errada"}
    )
    com_login_invalido = cliente.post(
        "/login", data={"login": "fantasma", "senha": "errada"}
    )

    assert com_login_valido.status_code == com_login_invalido.status_code

    def sem_token(corpo):
        # O token CSRF muda a cada resposta e não é informação sobre contas.
        return re.sub(r'value="[^"]{20,}"', 'value="TOKEN"', corpo)

    assert sem_token(com_login_valido.data.decode()) == sem_token(
        com_login_invalido.data.decode()
    )


def test_tempo_de_resposta_e_parecido_nos_dois_casos(cliente):
    """Mensagem igual não basta: o relógio também fala.

    Sem a equalização de tempo, o login inexistente responderia em
    microssegundos e o existente em centenas de milissegundos, porque só
    o segundo paga o custo do Argon2id.
    """
    def medir(login):
        inicio = time.perf_counter()
        cliente.post("/login", data={"login": login, "senha": "errada"})
        return time.perf_counter() - inicio

    medir("admin")  # primeira chamada aquece o hash de referência

    com_conta = medir("admin")
    sem_conta = medir("fantasma-que-nao-existe")

    proporcao = max(com_conta, sem_conta) / min(com_conta, sem_conta)
    assert proporcao < 3.0, (
        f"com conta {com_conta:.3f} s, sem conta {sem_conta:.3f} s — "
        f"a diferença revela quais contas existem"
    )


def test_conta_desativada_recebe_a_mesma_recusa(cliente):
    """Uma conta desativada que respondesse diferente revelaria que existe."""
    with conexao.obter_cursor() as cursor:
        cursor.execute("UPDATE usuario SET ativo = FALSE WHERE login = 'joao'")
    try:
        resposta = cliente.post(
            "/login", data={"login": "joao", "senha": HOSPEDE[1]}
        )
        assert resposta.status_code == 401
        assert MENSAGEM_DE_RECUSA in resposta.data.decode()
    finally:
        with conexao.obter_cursor() as cursor:
            cursor.execute("UPDATE usuario SET ativo = TRUE WHERE login = 'joao'")


@pytest.mark.parametrize(
    "entrada",
    [
        pytest.param("' OR '1'='1", id="injecao-classica"),
        pytest.param("admin'--", id="comentario-sql"),
        pytest.param("'; DROP TABLE usuario; --", id="drop-table"),
        pytest.param("admin' UNION SELECT 1,2,3,4,5,6,7,8,9,10 --", id="union"),
    ],
)
def test_injecao_de_sql_no_login_nao_funciona(cliente, entrada):
    """A consulta é parametrizada: o valor nunca vira comando.

    O login digitado é procurado literalmente, como se fosse um nome
    esquisito — e não encontrado.
    """
    resposta = cliente.post("/login", data={"login": entrada, "senha": "x"})
    assert resposta.status_code == 401

    with conexao.obter_cursor() as cursor:
        cursor.execute("SELECT COUNT(*) FROM usuario")
        assert cursor.fetchone()[0] == 3, "a tabela de usuários foi alterada"


@pytest.mark.parametrize(
    "faltando", [{"senha": "x"}, {"login": "admin"}, {}]
)
def test_campo_ausente_e_recusado(cliente, faltando):
    resposta = cliente.post("/login", data=faltando)
    assert resposta.status_code == 401


# ---------------------------------------------------------------------------
# Fixação de sessão
# ---------------------------------------------------------------------------

def test_sessao_anterior_e_descartada_na_entrada(cliente):
    """Defesa contra fixação de sessão.

    Se o conteúdo plantado antes do login sobrevivesse, um atacante que
    conseguisse fixar um valor na sessão da vítima o manteria depois de
    ela autenticar.
    """
    with cliente.session_transaction() as sessao:
        sessao["plantado_pelo_atacante"] = "valor"
        sessao["perfil"] = "administrador"

    _entrar(cliente, RECEPCAO)

    with cliente.session_transaction() as sessao:
        assert "plantado_pelo_atacante" not in sessao

        # O `perfil` plantado também some — a sessão não guarda mais essa
        # chave, e `session.clear()` descarta o que houvesse. Antes, a
        # verificação era que o valor fosse SUBSTITUÍDO pelo correto; agora
        # é que ele desapareça, o que é mais forte: a aplicação sequer lê
        # essa chave, então plantá-la não tem efeito algum.
        assert "perfil" not in sessao, (
            "o perfil plantado sobreviveu ao login"
        )

        # E a sessão passou a identificar quem de fato entrou.
        assert sessao["usuario_id"]


def test_cookie_de_sessao_muda_apos_entrar(cliente):
    cliente.get("/login")
    antes = cliente.get_cookie("villaju_sessao")
    _entrar(cliente, ADMIN)
    depois = cliente.get_cookie("villaju_sessao")

    assert depois is not None
    if antes is not None:
        assert antes.value != depois.value


# ---------------------------------------------------------------------------
# Saída
# ---------------------------------------------------------------------------

def test_logout_encerra_a_sessao(cliente):
    _entrar(cliente, ADMIN)
    cliente.post("/logout")

    with cliente.session_transaction() as sessao:
        assert "usuario_id" not in sessao

    resposta = cliente.get("/painel", follow_redirects=False)
    assert resposta.status_code == 302


def test_logout_nao_aceita_get(cliente):
    """Com GET, uma imagem embutida em sítio hostil derrubaria a sessão."""
    _entrar(cliente, ADMIN)
    assert cliente.get("/logout").status_code == 405

    with cliente.session_transaction() as sessao:
        assert sessao.get("usuario_id"), "a sessão caiu por uma requisição GET"


# ---------------------------------------------------------------------------
# Auditoria
# ---------------------------------------------------------------------------

def test_entrada_gera_login_ok(cliente):
    antes = len(_eventos(auditoria_repo.LOGIN_OK))
    _entrar(cliente, ADMIN)
    assert len(_eventos(auditoria_repo.LOGIN_OK)) == antes + 1


def test_recusa_gera_login_falha(cliente):
    antes = len(_eventos(auditoria_repo.LOGIN_FALHA))
    cliente.post("/login", data={"login": "admin", "senha": "errada"})
    assert len(_eventos(auditoria_repo.LOGIN_FALHA)) == antes + 1


def test_login_inexistente_tambem_e_registrado(cliente):
    antes = len(_eventos(auditoria_repo.LOGIN_FALHA))
    cliente.post("/login", data={"login": "fantasma", "senha": "x"})

    eventos = _eventos(auditoria_repo.LOGIN_FALHA)
    assert len(eventos) == antes + 1
    assert eventos[-1]["usuario_id"] is None, (
        "não há usuário a vincular quando o login não existe"
    )


def test_saida_gera_logout(cliente):
    antes = len(_eventos(auditoria_repo.LOGOUT))
    _entrar(cliente, ADMIN)
    cliente.post("/logout")
    assert len(_eventos(auditoria_repo.LOGOUT)) == antes + 1


def test_auditoria_de_login_nao_guarda_a_senha(cliente):
    """Nem a senha certa, nem a tentada, nem o login digitado."""
    cliente.post(
        "/login", data={"login": "admin", "senha": "SenhaSecretaTentada123"}
    )

    with conexao.obter_cursor(dicionario=True) as cursor:
        cursor.execute("SELECT detalhe FROM log_auditoria")
        tudo = " ".join(linha["detalhe"] or "" for linha in cursor.fetchall())

    assert "SenhaSecretaTentada123" not in tudo
    assert "Villa@Admin2026" not in tudo


def test_auditoria_registra_o_autor_da_entrada(cliente):
    _entrar(cliente, RECEPCAO)
    eventos = _eventos(auditoria_repo.LOGIN_OK)
    assert eventos[-1]["usuario_id"] is not None


# ---------------------------------------------------------------------------
# Escape de conteúdo
# ---------------------------------------------------------------------------

def test_login_com_script_e_escapado(cliente):
    """O escape do Jinja2 impede que conteúdo do usuário vire código.

    A resposta pode repetir o que foi digitado; o que não pode é que o
    navegador o interprete como HTML.
    """
    resposta = cliente.post(
        "/login",
        data={"login": "<script>alert(1)</script>", "senha": "x"},
    )
    assert b"<script>alert(1)</script>" not in resposta.data


def test_resposta_tem_cabecalhos_de_seguranca(cliente):
    cabecalhos = cliente.get("/login").headers
    assert cabecalhos.get("X-Frame-Options") == "DENY"
    assert "script-src 'self'" in cabecalhos.get("Content-Security-Policy", "")


# ---------------------------------------------------------------------------
# Recalibração do hash
# ---------------------------------------------------------------------------

def test_hash_fraco_e_regerado_na_entrada(cliente):
    """O hash defasado é regerado no primeiro login bem-sucedido.

    É o único momento em que a senha em claro está disponível para
    recalcular — fora dele, só restaria forçar todo mundo a trocar de
    senha.

    O hash fraco é gravado aqui, e não herdado da massa de dados, para que
    o teste não dependa de outro teste ter rodado antes ou depois.
    """
    from argon2 import PasswordHasher

    from app.seguranca import senhas

    fraco = PasswordHasher(
        memory_cost=65536, time_cost=3, parallelism=4
    ).hash(RECEPCAO[1])
    assert senhas.precisa_recalibrar(fraco) is True

    with conexao.obter_cursor() as cursor:
        cursor.execute(
            "UPDATE usuario SET senha_hash = %s WHERE login = %s",
            (fraco, RECEPCAO[0]),
        )

    _entrar(cliente, RECEPCAO)

    with conexao.obter_cursor(dicionario=True) as cursor:
        cursor.execute(
            "SELECT senha_hash FROM usuario WHERE login = %s", (RECEPCAO[0],)
        )
        depois = cursor.fetchone()["senha_hash"]

    assert depois != fraco, "o hash defasado não foi regerado"
    assert senhas.precisa_recalibrar(depois) is False
    assert senhas.verificar(depois, RECEPCAO[1]) is True
