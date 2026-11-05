"""Controle de acesso por perfil.

A regra do projeto é que a verificação acontece no servidor, em cada rota.
Estes testes a exercitam pelo caminho que um atacante usaria: digitando o
endereço direto, sem passar por link nenhum.

Cobrem também o caso 13 dos obrigatórios da Etapa 2 — formulário enviado
sem token CSRF é rejeitado — que até aqui estava descoberto porque a
configuração de teste desliga a proteção para não quebrar os demais
testes. Aqui ela é religada de propósito.
"""

import pytest

from app import create_app
from app.config import Configuracao
from app.dados import auditoria_repo, conexao
from app.seguranca.autorizacao import ADMINISTRADOR, PERFIS, requer_perfil

pytestmark = pytest.mark.banco

ADMIN = ("admin", "Villa@Admin2026")
RECEPCAO = ("recepcao", "Villa@Recep2026")
HOSPEDE = ("joao", "Villa@Hospede2026")

# Rotas da área autenticada e o perfil mínimo que cada uma exige.
ROTA_LIVRE = "/painel"
ROTA_RESTRITA = "/auditoria"


@pytest.fixture(scope="module")
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

    app = create_app(Configuracao(modo_teste=True))
    yield app
    conexao.encerrar_pool()


@pytest.fixture
def cliente(aplicacao):
    return aplicacao.test_client()


def _entrar(aplicacao, credenciais):
    cliente = aplicacao.test_client()
    login, senha = credenciais
    cliente.post("/login", data={"login": login, "senha": senha})
    return cliente


def _negacoes():
    with conexao.obter_cursor(dicionario=True) as cursor:
        cursor.execute(
            "SELECT * FROM log_auditoria WHERE acao = %s ORDER BY id",
            (auditoria_repo.ACESSO_NEGADO,),
        )
        return cursor.fetchall()


# ---------------------------------------------------------------------------
# Sem sessão
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("rota", [ROTA_LIVRE, ROTA_RESTRITA])
def test_rota_autenticada_sem_sessao_leva_ao_login(cliente, rota):
    """Caso obrigatório 12 da Etapa 2."""
    resposta = cliente.get(rota, follow_redirects=False)
    assert resposta.status_code == 302
    assert "/login" in resposta.headers["Location"]


def test_sem_sessao_nao_gera_acesso_negado(cliente):
    """Quem não entrou pode só ter expirado; não é tentativa de invasão.

    Registrar `ACESSO_NEGADO` para toda visita anônima encheria o log de
    ruído e esconderia as tentativas que importam.
    """
    antes = len(_negacoes())
    cliente.get(ROTA_RESTRITA)
    assert len(_negacoes()) == antes


# ---------------------------------------------------------------------------
# Com sessão e perfil errado
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("credenciais", [RECEPCAO, HOSPEDE])
def test_perfil_errado_recebe_403(aplicacao, credenciais):
    """Caso obrigatório 11 da Etapa 2, primeira metade.

    O acesso é tentado digitando o endereço, sem passar por link nenhum —
    que é exatamente o que alguém faria.
    """
    cliente = _entrar(aplicacao, credenciais)
    assert cliente.get(ROTA_RESTRITA).status_code == 403


@pytest.mark.parametrize("credenciais", [RECEPCAO, HOSPEDE])
def test_perfil_errado_gera_acesso_negado(aplicacao, credenciais):
    """Caso obrigatório 11 da Etapa 2, segunda metade."""
    antes = len(_negacoes())
    cliente = _entrar(aplicacao, credenciais)
    cliente.get(ROTA_RESTRITA)

    eventos = _negacoes()
    assert len(eventos) == antes + 1
    assert eventos[-1]["usuario_id"] is not None, (
        "a tentativa precisa ser vinculada a quem a fez"
    )


def test_registro_identifica_a_rota_e_o_perfil(aplicacao):
    cliente = _entrar(aplicacao, RECEPCAO)
    cliente.get(ROTA_RESTRITA)

    detalhe = _negacoes()[-1]["detalhe"]
    assert "painel.auditoria" in detalhe
    assert "administrador" in detalhe
    assert "recepcionista" in detalhe


def test_registro_nao_guarda_o_endereco_com_identificador(aplicacao):
    """O log guarda o nome da rota, não o caminho completo.

    Um caminho como `/reservas/42` carregaria o identificador do registro,
    e a regra do projeto é que o log não guarda valores.
    """
    cliente = _entrar(aplicacao, RECEPCAO)
    cliente.get(ROTA_RESTRITA)

    detalhe = _negacoes()[-1]["detalhe"]
    assert "/auditoria" not in detalhe


def test_administrador_acessa_a_rota_restrita(aplicacao):
    cliente = _entrar(aplicacao, ADMIN)
    assert cliente.get(ROTA_RESTRITA).status_code == 200


@pytest.mark.parametrize("credenciais", [ADMIN, RECEPCAO, HOSPEDE])
def test_todo_perfil_acessa_a_rota_livre(aplicacao, credenciais):
    cliente = _entrar(aplicacao, credenciais)
    assert cliente.get(ROTA_LIVRE).status_code == 200


def test_acesso_autorizado_nao_gera_registro_de_negacao(aplicacao):
    antes = len(_negacoes())
    cliente = _entrar(aplicacao, ADMIN)
    cliente.get(ROTA_RESTRITA)
    assert len(_negacoes()) == antes


# ---------------------------------------------------------------------------
# A página de 403
# ---------------------------------------------------------------------------

def test_pagina_de_403_nao_revela_detalhe_tecnico(aplicacao):
    """Detalhe de erro conta ao sondador qual biblioteca falhou."""
    cliente = _entrar(aplicacao, RECEPCAO)
    corpo = cliente.get(ROTA_RESTRITA).data.decode().lower()

    for vazamento in (
        "traceback", "werkzeug", "flask", "mysql", "python",
        "file \"", "line ", "select ",
    ):
        assert vazamento not in corpo, f"a página de erro revela: {vazamento}"


def test_pagina_de_404_e_generica(cliente):
    resposta = cliente.get("/rota-que-nao-existe")
    assert resposta.status_code == 404
    corpo = resposta.data.decode().lower()
    assert "traceback" not in corpo
    assert "werkzeug" not in corpo


# ---------------------------------------------------------------------------
# A sessão reflete o estado atual da conta
# ---------------------------------------------------------------------------

def test_conta_desativada_perde_o_acesso_na_proxima_requisicao(aplicacao):
    """Sem releitura do banco, a sessão valeria até oito horas depois.

    A sessão guarda um retrato do momento do login. Se um administrador
    desativa uma conta, a pessoa continuaria dentro do sistema até o
    cookie expirar — o que torna a desativação inútil justamente quando
    ela é urgente.
    """
    cliente = _entrar(aplicacao, RECEPCAO)
    assert cliente.get(ROTA_LIVRE).status_code == 200

    with conexao.obter_cursor() as cursor:
        cursor.execute("UPDATE usuario SET ativo = FALSE WHERE login = 'recepcao'")
    try:
        resposta = cliente.get(ROTA_LIVRE, follow_redirects=False)
        assert resposta.status_code == 302
        assert "/login" in resposta.headers["Location"]
    finally:
        with conexao.obter_cursor() as cursor:
            cursor.execute(
                "UPDATE usuario SET ativo = TRUE WHERE login = 'recepcao'"
            )


def test_perfil_rebaixado_perde_o_acesso_na_proxima_requisicao(aplicacao):
    """Mesmo princípio, para quem é rebaixado em vez de desativado."""
    cliente = _entrar(aplicacao, ADMIN)
    assert cliente.get(ROTA_RESTRITA).status_code == 200

    with conexao.obter_cursor() as cursor:
        cursor.execute(
            "UPDATE usuario SET perfil = 'recepcionista' WHERE login = 'admin'"
        )
    try:
        assert cliente.get(ROTA_RESTRITA).status_code == 403, (
            "o perfil guardado na sessão prevaleceu sobre o do banco"
        )
    finally:
        with conexao.obter_cursor() as cursor:
            cursor.execute(
                "UPDATE usuario SET perfil = 'administrador' WHERE login = 'admin'"
            )


def test_perfil_forjado_na_sessao_nao_concede_acesso(aplicacao):
    """A sessão é assinada, e o perfil é relido do banco de qualquer forma.

    Mesmo que alguém conseguisse reescrever o cookie, o que exigiria a
    chave secreta, o perfil efetivo vem da tabela `usuario`.
    """
    cliente = _entrar(aplicacao, RECEPCAO)

    with cliente.session_transaction() as sessao:
        sessao["perfil"] = "administrador"

    assert cliente.get(ROTA_RESTRITA).status_code == 403


def test_identificador_inexistente_na_sessao_leva_ao_login(aplicacao):
    cliente = _entrar(aplicacao, RECEPCAO)

    with cliente.session_transaction() as sessao:
        sessao["usuario_id"] = 999999

    resposta = cliente.get(ROTA_LIVRE, follow_redirects=False)
    assert resposta.status_code == 302


# ---------------------------------------------------------------------------
# A interface não é controle de acesso
# ---------------------------------------------------------------------------

def test_link_da_rota_restrita_so_aparece_para_quem_pode(aplicacao):
    corpo_admin = _entrar(aplicacao, ADMIN).get(ROTA_LIVRE).data.decode()
    corpo_recepcao = _entrar(aplicacao, RECEPCAO).get(ROTA_LIVRE).data.decode()

    assert "/auditoria" in corpo_admin
    assert "/auditoria" not in corpo_recepcao


def test_esconder_o_link_nao_protege_a_rota(aplicacao):
    """O ponto central do controle de acesso deste projeto.

    O link não aparece para o recepcionista, mas isso é conveniência de
    interface. A proteção está no decorador, e este teste prova que ela
    funciona mesmo quando a interface é contornada.
    """
    cliente = _entrar(aplicacao, RECEPCAO)

    assert "/auditoria" not in cliente.get(ROTA_LIVRE).data.decode()
    assert cliente.get(ROTA_RESTRITA).status_code == 403


# ---------------------------------------------------------------------------
# O decorador em si
# ---------------------------------------------------------------------------

def test_decorador_recusa_perfil_desconhecido():
    """Erro de digitação num perfil criaria rota inalcançável, ou pior.

    A validação acontece na importação do módulo da rota, não quando
    alguém tentar acessá-la.
    """
    with pytest.raises(ValueError) as erro:
        requer_perfil("adminstrador")  # falta o 'i'
    assert "desconhecido" in str(erro.value)


def test_decorador_exige_ao_menos_um_perfil():
    with pytest.raises(ValueError):
        requer_perfil()


def test_perfis_declarados_batem_com_o_enum_do_banco(aplicacao):
    """O ENUM da coluna e as constantes do módulo não podem divergir."""
    with conexao.obter_cursor() as cursor:
        cursor.execute(
            "SELECT column_type FROM information_schema.columns "
            "WHERE table_schema = DATABASE() AND table_name = 'usuario' "
            "AND column_name = 'perfil'"
        )
        tipo = cursor.fetchone()[0]

    do_banco = {v.strip("'") for v in tipo[len("enum("):-1].split(",")}
    assert do_banco == set(PERFIS)


def test_decorador_preserva_o_nome_da_funcao():
    """Sem functools.wraps, o Flask registraria todas as rotas com o mesmo
    nome e a segunda sobrescreveria a primeira."""

    @requer_perfil(ADMINISTRADOR)
    def uma_vista_qualquer():
        return "ok"

    assert uma_vista_qualquer.__name__ == "uma_vista_qualquer"


# ---------------------------------------------------------------------------
# Caso obrigatório 13: formulário sem token CSRF
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def aplicacao_com_csrf(aplicacao):
    """Aplicação com a proteção contra CSRF religada.

    A configuração de teste a desliga, porque exigir o token em todos os
    testes de formulário os tornaria ilegíveis. Aqui ela volta, para que o
    caso obrigatório 13 seja de fato exercitado.
    """
    configuracao = Configuracao(modo_teste=True)
    configuracao.WTF_CSRF_ENABLED = True
    app = create_app(configuracao, iniciar_banco=False)
    return app


def test_formulario_sem_token_csrf_e_rejeitado(aplicacao_com_csrf):
    """Caso obrigatório 13 da Etapa 2.

    Sem o token, um sítio hostil faria o navegador de quem está
    autenticado enviar requisições ao VillaJu, aproveitando o cookie de
    sessão que o navegador anexa sozinho.
    """
    cliente = aplicacao_com_csrf.test_client()
    resposta = cliente.post(
        "/login", data={"login": "admin", "senha": "Villa@Admin2026"}
    )
    assert resposta.status_code == 400, (
        "o formulário foi aceito sem token CSRF"
    )


def test_logout_sem_token_csrf_e_rejeitado(aplicacao_com_csrf):
    """Com GET ou sem token, derrubar a sessão alheia seria trivial."""
    cliente = aplicacao_com_csrf.test_client()
    assert cliente.post("/logout").status_code == 400


def test_formulario_de_login_entrega_o_token(aplicacao_com_csrf):
    corpo = aplicacao_com_csrf.test_client().get("/login").data.decode()
    assert 'name="csrf_token"' in corpo
    assert 'value=""' not in corpo


def test_token_csrf_valido_e_aceito(aplicacao_com_csrf):
    """Prova que a rejeição acima vem do token, e não de outra coisa."""
    import re

    cliente = aplicacao_com_csrf.test_client()
    corpo = cliente.get("/login").data.decode()
    achado = re.search(r'name="csrf_token" value="([^"]+)"', corpo)
    assert achado, "o formulário não trouxe token"

    resposta = cliente.post(
        "/login",
        data={
            "login": "admin",
            "senha": "senha-errada-de-proposito",
            "csrf_token": achado.group(1),
        },
    )
    # Com token válido a requisição é processada; a recusa vem da senha.
    assert resposta.status_code == 401, (
        f"esperado 401 por senha errada, veio {resposta.status_code}"
    )
