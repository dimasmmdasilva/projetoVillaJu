"""Relatório 2, achado K1 — sessão encerrada não volta a valer.

A análise dinâmica de 23/11 copiou o cookie `villaju_sessao` de uma sessão
de administrador, clicou em "Sair" e reapresentou a cópia: o servidor
respondeu 200. A sessão do Flask vive num cookie assinado, sem registro no
servidor; `session.clear()` apaga o cookie do navegador, mas não a cópia.

A correção: `usuario.versao_sessao`. O login grava a versão no cookie;
`usuario_atual()` compara com o banco a cada requisição; sair, trocar a
senha e redefinir a senha somam 1. Estes testes cobrem o achado e o que a
correção NÃO pode mudar: entrar em dois aparelhos, continuar dentro depois
de trocar a própria senha, e a recalibração do hash no login.
"""

import pytest

from app import create_app
from app.config import Configuracao
from app.dados import conexao, token_repo, usuario_repo

ADMIN = ("admin", "Villa@Admin2026")
RECEPCAO = ("recepcao", "Villa@Recep2026")
COOKIE = "villaju_sessao"


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


def _entrar(aplicacao, credenciais=ADMIN):
    cliente = aplicacao.test_client()
    login, senha = credenciais
    resposta = cliente.post("/login", data={"login": login, "senha": senha})
    assert resposta.status_code == 302, "o login precisa funcionar para o teste dizer algo"
    return cliente


def _copia_do_cookie(aplicacao, original):
    """Outro "navegador" com uma cópia exata do cookie de sessão."""
    copia = aplicacao.test_client()
    copia.set_cookie(COOKIE, original.get_cookie(COOKIE).value)
    return copia


def _dentro(cliente) -> bool:
    return cliente.get("/painel", follow_redirects=False).status_code == 200


def _versao(login) -> int:
    with conexao.obter_cursor() as cursor:
        cursor.execute("SELECT versao_sessao FROM usuario WHERE login = %s", (login,))
        return cursor.fetchone()[0]


# ---------------------------------------------------------------------------
# O achado
# ---------------------------------------------------------------------------

def test_k1_copia_do_cookie_nao_entra_depois_de_sair(aplicacao):
    """O caso exato da análise dinâmica: copia, sai, reapresenta a cópia."""
    dono = _entrar(aplicacao)
    copia = _copia_do_cookie(aplicacao, dono)
    assert _dentro(copia), "antes de sair, a cópia entra — senão o teste não prova nada"

    dono.post("/logout")

    resposta = copia.get("/painel", follow_redirects=False)
    assert resposta.status_code == 302
    assert "/login" in resposta.headers["Location"]


def test_k1_a_copia_tambem_nao_abre_area_restrita(aplicacao):
    dono = _entrar(aplicacao)
    copia = _copia_do_cookie(aplicacao, dono)
    dono.post("/logout")
    assert copia.get("/auditoria", follow_redirects=False).status_code == 302


def test_sair_soma_exatamente_um_a_versao(aplicacao):
    antes = _versao("recepcao")
    _entrar(aplicacao, RECEPCAO).post("/logout")
    assert _versao("recepcao") == antes + 1


def test_sair_num_aparelho_encerra_os_outros(aplicacao):
    """Efeito colateral declarado ao Dimas e aceito em 23/11."""
    celular = _entrar(aplicacao, RECEPCAO)
    computador = _entrar(aplicacao, RECEPCAO)
    celular.post("/logout")
    assert not _dentro(computador)


def test_cookie_sem_versao_de_antes_da_correcao_nao_entra(aplicacao):
    """Quem estava logado antes da correção entra de novo, uma vez."""
    cliente = _entrar(aplicacao)
    with cliente.session_transaction() as sessao:
        del sessao["versao_sessao"]
    assert not _dentro(cliente)


def test_cookie_com_versao_antiga_e_limpo_e_a_tela_de_login_abre(aplicacao):
    """Cookie morto não pode mandar ao painel nem prender num redirecionamento."""
    dono = _entrar(aplicacao)
    copia = _copia_do_cookie(aplicacao, dono)
    dono.post("/logout")

    assert copia.get("/login", follow_redirects=False).status_code == 200
    with copia.session_transaction() as sessao:
        assert "usuario_id" not in sessao


# ---------------------------------------------------------------------------
# O que a correção não pode mudar
# ---------------------------------------------------------------------------

def test_entrar_de_novo_depois_de_sair_funciona(aplicacao):
    _entrar(aplicacao).post("/logout")
    assert _dentro(_entrar(aplicacao))


def test_entrar_em_outro_aparelho_nao_derruba_o_primeiro(aplicacao):
    primeiro = _entrar(aplicacao)
    _entrar(aplicacao)
    assert _dentro(primeiro)


def test_entrar_nao_muda_a_versao(aplicacao):
    """Nem a recalibração do hash no login (atualizar_hash) mexe nela."""
    antes = _versao("admin")
    _entrar(aplicacao)
    assert _versao("admin") == antes


def test_a_sessao_guarda_a_versao_do_banco(aplicacao):
    cliente = _entrar(aplicacao)
    with cliente.session_transaction() as sessao:
        assert sessao["versao_sessao"] == _versao("admin")


# ---------------------------------------------------------------------------
# Senha trocada ou redefinida
# ---------------------------------------------------------------------------

@pytest.fixture
def conta_descartavel(aplicacao):
    """Conta de recepção só deste teste: a senha dela muda."""
    from app.seguranca import senhas

    with aplicacao.app_context():
        identificador = usuario_repo.criar(
            login="sessao.teste", nome="Conta do teste de sessão",
            senha_hash=senhas.gerar_hash("Senha@Inicial2026"),
            perfil="recepcionista", ativo=True,
        )
    yield identificador, ("sessao.teste", "Senha@Inicial2026")
    with conexao.obter_conexao() as c:
        cursor = c.cursor()
        cursor.execute("DELETE FROM log_auditoria WHERE usuario_id = %s", (identificador,))
        cursor.execute("DELETE FROM token_acesso WHERE usuario_id = %s", (identificador,))
        cursor.execute("DELETE FROM usuario WHERE id = %s", (identificador,))
        cursor.close()


def test_redefinir_a_senha_pelo_link_encerra_as_sessoes_abertas(aplicacao, conta_descartavel):
    identificador, credenciais = conta_descartavel
    aberta = _entrar(aplicacao, credenciais)
    assert _dentro(aberta)

    with aplicacao.app_context():
        token = token_repo.gerar(identificador, token_repo.RECUPERACAO)
    visitante = aplicacao.test_client()
    resposta = visitante.post(f"/redefinir/{token}", data={
        "senha": "Senha@Nova2026xyz", "senha_confirmacao": "Senha@Nova2026xyz"})
    assert resposta.status_code == 302, "a redefinição precisa ter acontecido"

    assert not _dentro(aberta)


def test_trocar_a_senha_no_repositorio_soma_um_e_grava_o_hash(aplicacao, conta_descartavel):
    from app.seguranca import senhas

    identificador, _ = conta_descartavel
    antes = _versao("sessao.teste")
    with aplicacao.app_context():
        devolvida = usuario_repo.trocar_senha(identificador, senhas.gerar_hash("Outra@Senha2026"))
        conta = usuario_repo.buscar_por_id(identificador)
    assert devolvida == antes + 1 == _versao("sessao.teste")
    assert senhas.verificar(conta["senha_hash"], "Outra@Senha2026")


def test_atualizar_hash_da_recalibracao_nao_soma(aplicacao, conta_descartavel):
    from app.seguranca import senhas

    identificador, _ = conta_descartavel
    antes = _versao("sessao.teste")
    with aplicacao.app_context():
        usuario_repo.atualizar_hash(identificador, senhas.gerar_hash("Senha@Inicial2026"))
    assert _versao("sessao.teste") == antes
