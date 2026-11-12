"""Recuperação de senha pelo link enviado por e-mail.

Etapa 5, Bloco 14.

A Etapa 5 passou a criar contas com senha escolhida pela própria pessoa, e
isso tem uma consequência que não é opcional: **ninguém mais conhece a
senha de ninguém.** Nem a recepção, nem o administrador, nem o banco — só
o hash Argon2id, que não volta.

Sem este bloco, quem esquece a senha perde a conta. Não é inconveniência:
é perda de acesso definitiva, e a recepção não pode ajudar nem querendo.

O que estes testes guardam:

1. **A resposta é idêntica** para endereço com conta e sem conta.
2. **Abrir o link não o consome** — só o envio consome.
3. **Senha inválida não queima o link.**
4. **Conta inativa não recebe link**: a recuperação não é atalho para
   pular a confirmação do cadastro.
5. **Não entra logado depois de redefinir.**
"""

import pathlib
import re
import tempfile

import pytest

from app import create_app
from app.config import Configuracao
from app.dados import (
    auditoria_repo,
    conexao,
    hospede_repo,
    token_repo,
    usuario_repo,
)
from app.seguranca import senhas
from app.servicos import correio, recuperacao

EMAIL = "mariana.prado@exemplo.com"
SENHA = "jureia-praia-2026"
NOVA = "senha-redefinida-com-sucesso"


@pytest.fixture
def pasta_de_emails():
    return pathlib.Path(tempfile.mkdtemp(prefix="villaju-b14-"))


@pytest.fixture
def aplicacao(conexao_banco, pasta_de_emails):
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
    configuracao.EMAIL_MODO = "arquivo"
    configuracao.EMAIL_PASTA = pasta_de_emails
    configuracao.EMAIL_BASE_URL = "https://localhost:5000"

    conexao.iniciar_pool(configuracao)
    app = create_app(configuracao)
    app.config["SESSION_COOKIE_SECURE"] = False
    app.villaju = configuracao

    yield app
    conexao.encerrar_pool()


@pytest.fixture
def conta(aplicacao):
    with aplicacao.app_context():
        hospede_id = hospede_repo.criar({
            "nome": "Mariana Prado",
            "documento": "529.982.247-25",
            "email": EMAIL,
            "telefone": "(12) 99888-7766",
        })
        usuario_id = usuario_repo.criar(
            login=usuario_repo.login_de_email(EMAIL),
            nome="Mariana Prado",
            senha_hash=senhas.gerar_hash(SENHA),
            perfil="hospede",
            hospede_id=hospede_id,
            ativo=True,
        )
    return {"hospede_id": hospede_id, "usuario_id": usuario_id}


@pytest.fixture
def visitante(aplicacao):
    return aplicacao.test_client()


def _enviadas(pasta):
    class _Configuracao:
        EMAIL_PASTA = pasta

    return correio.listar_enviadas(_Configuracao)


def _token(cliente, rota):
    pagina = cliente.get(rota).data.decode()
    achado = re.search(r'name="csrf_token" value="([^"]+)"', pagina)
    return achado.group(1) if achado else ""


def _pedir(cliente, email):
    return cliente.post("/esqueci-senha", data={
        "email": email,
        "csrf_token": _token(cliente, "/esqueci-senha"),
    })


def _link(pasta):
    for mensagem in _enviadas(pasta):
        achado = re.search(r"/redefinir/([A-Za-z0-9_\-]+)", mensagem["corpo"])
        if achado:
            return achado.group(1)
    return None


# ---------------------------------------------------------------------------
# A porta
# ---------------------------------------------------------------------------

def test_a_tela_e_publica(visitante):
    assert visitante.get("/esqueci-senha").status_code == 200


def test_a_tela_de_entrada_oferece_o_caminho(visitante):
    """Sem o link, a pessoa fica sem saída.

    Desde a Etapa 5, ninguém conhece a senha de ninguém — nem a recepção,
    nem o administrador, nem o banco, que guarda só o hash.
    """
    html = visitante.get("/login").data.decode()

    assert "/esqueci-senha" in html
    assert "Esqueci a senha" in html


def test_o_pedido_e_limitado_por_taxa():
    """Limitada por uma razão a mais que a do login.

    Esta rota manda e-mail para um endereço que o visitante digitou: sem
    limite, alguém a usaria para encher a caixa de entrada de outra
    pessoa, com o nome da VillaJu no remetente.

    Verificado na fonte porque em modo de teste o limitador fica
    desligado, e religá-lo aqui mediria a configuração e não a rota.
    """
    fonte = (
        pathlib.Path(__file__).resolve().parents[1] / "app" / "rotas" / "auth.py"
    ).read_text(encoding="utf-8")

    for rota in ("def esqueci_senha(", "def redefinir_senha("):
        corte = fonte.index(rota)
        decoradores = fonte[:corte].rsplit("@auth.route", 1)[-1]
        assert "@limiter.limit" in decoradores, (
            f"{rota} não está limitada por taxa"
        )


# ---------------------------------------------------------------------------
# A resposta uniforme
# ---------------------------------------------------------------------------

def test_a_resposta_e_identica_com_e_sem_conta(aplicacao, conta):
    """O requisito central do bloco.

    Se distinguisse, a tela viraria um levantamento de quem é cliente da
    hospedaria — exatamente o que o cadastro se esforça para evitar, e
    seria inútil evitá-lo lá e abri-lo aqui.
    """
    com_conta = aplicacao.test_client()
    sem_conta = aplicacao.test_client()

    uma = _pedir(com_conta, EMAIL)
    outra = _pedir(sem_conta, "ninguem.aqui@exemplo.com")

    assert uma.status_code == outra.status_code == 302
    assert uma.headers["Location"] == outra.headers["Location"]

    pagina_um = com_conta.get(uma.headers["Location"]).data
    pagina_dois = sem_conta.get(outra.headers["Location"]).data

    assert pagina_um == pagina_dois


def test_endereco_sem_conta_nao_gera_email(visitante, pasta_de_emails):
    _pedir(visitante, "ninguem.aqui@exemplo.com")

    assert _enviadas(pasta_de_emails) == []


def test_a_pagina_de_confirmacao_fala_no_condicional(visitante):
    html = visitante.get("/esqueci-senha/enviado").data.decode()

    assert "Se este endereço tiver conta" in html
    assert "enviamos" not in html.lower()
    assert "não está cadastrado" not in html.lower()


@pytest.mark.parametrize(
    "rotulo,email",
    [("vazio", ""), ("sem arroba", "naoeumendereco")],
)
def test_endereco_invalido_e_recusado(visitante, rotulo, email):
    resposta = _pedir(visitante, email)

    assert resposta.status_code == 400, rotulo
    assert "aviso-erro" in resposta.data.decode(), rotulo


def test_a_recusa_nunca_fala_da_conta_existir(visitante, conta):
    resposta = _pedir(visitante, "")
    texto = resposta.data.decode().lower()

    for revelador in ("não existe", "não está cadastrado", "já existe"):
        assert revelador not in texto


# ---------------------------------------------------------------------------
# O e-mail
# ---------------------------------------------------------------------------

def test_o_email_traz_o_link_e_nao_a_senha(visitante, conta, pasta_de_emails):
    _pedir(visitante, EMAIL)

    enviadas = _enviadas(pasta_de_emails)
    assert len(enviadas) == 1

    mensagem = enviadas[0]
    assert "/redefinir/" in mensagem["corpo"]
    assert "48 horas" in mensagem["corpo"]
    assert SENHA not in mensagem["corpo"]
    assert "529.982" not in mensagem["corpo"], (
        "o documento não tem o que fazer num e-mail"
    )


def test_o_email_diz_o_que_fazer_se_nao_foi_a_pessoa(
    visitante, conta, pasta_de_emails
):
    """Quem recebe sem ter pedido precisa saber que nada mudou."""
    _pedir(visitante, EMAIL)

    corpo = _enviadas(pasta_de_emails)[0]["corpo"]

    assert "Se não foi você" in corpo
    assert "nada foi alterado" in corpo.lower()


def test_o_token_e_do_tipo_recuperacao(aplicacao, conta, visitante):
    """E não de confirmação: os dois provam a posse do endereço, mas
    autorizam coisas diferentes."""
    _pedir(visitante, EMAIL)

    with aplicacao.app_context():
        with conexao.obter_cursor(dicionario=True) as cursor:
            cursor.execute(
                "SELECT tipo, TIMESTAMPDIFF(HOUR, criado_em, expira_em) AS horas "
                "FROM token_acesso ORDER BY id DESC LIMIT 1"
            )
            linha = cursor.fetchone()

    assert linha["tipo"] == "recuperacao"
    assert linha["horas"] == 48


# ---------------------------------------------------------------------------
# Abrir o link não o consome
# ---------------------------------------------------------------------------

def test_abrir_o_link_nao_consome_o_token(visitante, conta, pasta_de_emails):
    """Se consumisse, o link morreria antes de servir para alguma coisa.

    Quem fechasse a aba sem querer teria de pedir outro — e quem abrisse
    o e-mail no celular para depois usar o computador ficaria sem.
    """
    _pedir(visitante, EMAIL)
    token = _link(pasta_de_emails)

    for tentativa in range(3):
        resposta = visitante.get(f"/redefinir/{token}")
        assert resposta.status_code == 200, tentativa
        assert 'name="senha"' in resposta.data.decode(), tentativa


def test_conferir_nao_consome_mas_consumir_consome(aplicacao, conta):
    """As duas operações, lado a lado.

    `conferir` é o que decide se a tela aparece; `consumir` é o que
    autoriza. Quem usar só a primeira para autorizar algo abre uma janela
    de corrida entre a conferência e o efeito.
    """
    with aplicacao.app_context():
        token = token_repo.gerar(
            conta["usuario_id"], token_repo.RECUPERACAO
        )

        assert token_repo.conferir(token, token_repo.RECUPERACAO) == \
            conta["usuario_id"]
        assert token_repo.conferir(token, token_repo.RECUPERACAO) == \
            conta["usuario_id"]

        assert token_repo.consumir(token, token_repo.RECUPERACAO) == \
            conta["usuario_id"]
        assert token_repo.conferir(token, token_repo.RECUPERACAO) is None


def test_conferir_responde_None_uniforme(aplicacao, conta):
    """Por não consumir, `conferir` seria a ferramenta óbvia para sondar
    tokens — se respondesse diferente para cada caso de falha."""
    with aplicacao.app_context():
        de_outro_tipo = token_repo.gerar(
            conta["usuario_id"], token_repo.CONFIRMACAO
        )

        assert token_repo.conferir("x" * 43, token_repo.RECUPERACAO) is None
        assert token_repo.conferir("", token_repo.RECUPERACAO) is None
        assert token_repo.conferir(
            de_outro_tipo, token_repo.RECUPERACAO
        ) is None


# ---------------------------------------------------------------------------
# A troca
# ---------------------------------------------------------------------------

def test_senha_invalida_nao_queima_o_link(
    aplicacao, conta, visitante, pasta_de_emails
):
    """A ordem das operações é a defesa.

    A senha é validada antes de o token ser consumido: do contrário,
    digitar uma senha curta queimaria o link, e a pessoa teria de pedir
    outro por causa de um erro de digitação.
    """
    _pedir(visitante, EMAIL)
    token = _link(pasta_de_emails)

    recusada = visitante.post(f"/redefinir/{token}", data={
        "senha": "curta1",
        "senha_confirmacao": "curta1",
        "csrf_token": _token(visitante, f"/redefinir/{token}"),
    })

    assert recusada.status_code == 400
    assert "aviso-erro" in recusada.data.decode()

    # O link continua valendo.
    assert 'name="senha"' in visitante.get(
        f"/redefinir/{token}"
    ).data.decode()

    # E a senha não mudou.
    with aplicacao.app_context():
        atual = usuario_repo.buscar_por_id(conta["usuario_id"])
    assert senhas.verificar(atual["senha_hash"], SENHA)


@pytest.mark.parametrize(
    "rotulo,nova,confirmacao",
    [
        ("curta", "curta1", "curta1"),
        ("confirmação diferente", "senha-nova-longa", "outra-senha-longa"),
        ("vazia", "", ""),
    ],
)
def test_senha_nova_invalida_e_recusada(
    aplicacao, conta, visitante, pasta_de_emails, rotulo, nova, confirmacao
):
    _pedir(visitante, EMAIL)
    token = _link(pasta_de_emails)

    resposta = visitante.post(f"/redefinir/{token}", data={
        "senha": nova,
        "senha_confirmacao": confirmacao,
        "csrf_token": _token(visitante, f"/redefinir/{token}"),
    })

    assert resposta.status_code == 400, rotulo

    with aplicacao.app_context():
        atual = usuario_repo.buscar_por_id(conta["usuario_id"])
    assert senhas.verificar(atual["senha_hash"], SENHA), rotulo


def test_nenhuma_senha_volta_escrita_no_html(
    conta, visitante, pasta_de_emails
):
    _pedir(visitante, EMAIL)
    token = _link(pasta_de_emails)

    resposta = visitante.post(f"/redefinir/{token}", data={
        "senha": NOVA,
        "senha_confirmacao": "diferente-de-proposito",
        "csrf_token": _token(visitante, f"/redefinir/{token}"),
    })

    assert NOVA not in resposta.data.decode()


def test_a_troca_bem_sucedida_muda_a_senha(
    aplicacao, conta, visitante, pasta_de_emails
):
    _pedir(visitante, EMAIL)
    token = _link(pasta_de_emails)

    resposta = visitante.post(f"/redefinir/{token}", data={
        "senha": NOVA,
        "senha_confirmacao": NOVA,
        "csrf_token": _token(visitante, f"/redefinir/{token}"),
    })

    assert resposta.status_code == 302
    assert "/login" in resposta.headers["Location"]

    with aplicacao.app_context():
        atual = usuario_repo.buscar_por_id(conta["usuario_id"])

    assert senhas.verificar(atual["senha_hash"], NOVA)
    assert not senhas.verificar(atual["senha_hash"], SENHA)


def test_nao_entra_logado_depois_de_redefinir(
    conta, visitante, pasta_de_emails
):
    """Entrar automaticamente transformaria acesso ao e-mail em acesso à
    conta sem que ninguém digitasse a senha.

    O ponto do link é **provar posse do endereço**, não substituir a
    senha.
    """
    _pedir(visitante, EMAIL)
    token = _link(pasta_de_emails)

    visitante.post(f"/redefinir/{token}", data={
        "senha": NOVA,
        "senha_confirmacao": NOVA,
        "csrf_token": _token(visitante, f"/redefinir/{token}"),
    })

    with visitante.session_transaction() as sessao:
        assert "usuario_id" not in dict(sessao)

    # E a área autenticada continua fechada.
    assert visitante.get(
        "/painel", follow_redirects=False
    ).status_code == 302


def test_a_entrada_passa_a_exigir_a_senha_nova(
    aplicacao, conta, visitante, pasta_de_emails
):
    _pedir(visitante, EMAIL)
    token = _link(pasta_de_emails)
    visitante.post(f"/redefinir/{token}", data={
        "senha": NOVA,
        "senha_confirmacao": NOVA,
        "csrf_token": _token(visitante, f"/redefinir/{token}"),
    })

    com_nova = aplicacao.test_client()
    resposta = com_nova.post("/login", data={
        "login": EMAIL, "senha": NOVA,
        "csrf_token": _token(com_nova, "/login"),
    })
    assert resposta.status_code == 302

    com_antiga = aplicacao.test_client()
    resposta = com_antiga.post("/login", data={
        "login": EMAIL, "senha": SENHA,
        "csrf_token": _token(com_antiga, "/login"),
    })
    assert resposta.status_code == 401


def test_o_link_e_de_uso_unico(conta, visitante, pasta_de_emails):
    _pedir(visitante, EMAIL)
    token = _link(pasta_de_emails)

    visitante.post(f"/redefinir/{token}", data={
        "senha": NOVA,
        "senha_confirmacao": NOVA,
        "csrf_token": _token(visitante, f"/redefinir/{token}"),
    })

    reaberto = visitante.get(f"/redefinir/{token}")

    assert reaberto.status_code == 400
    assert "não vale mais" in reaberto.data.decode()


def test_pedir_de_novo_invalida_o_pendente(
    conta, visitante, pasta_de_emails
):
    """Quem pede o link outra vez porque o primeiro não chegou não fica
    com dois links válidos circulando."""
    _pedir(visitante, EMAIL)
    primeiro = _link(pasta_de_emails)

    _pedir(visitante, EMAIL)
    segundo = _link(pasta_de_emails)

    assert primeiro != segundo

    assert visitante.get(f"/redefinir/{primeiro}").status_code == 400
    assert visitante.get(f"/redefinir/{segundo}").status_code == 200


# ---------------------------------------------------------------------------
# Conta inativa
# ---------------------------------------------------------------------------

def test_conta_inativa_nao_recebe_link(aplicacao, pasta_de_emails):
    """A recuperação não é atalho para pular a confirmação do cadastro.

    Os dois fluxos provam a mesma coisa — posse do endereço — mas
    autorizam coisas diferentes: a confirmação ativa uma conta nova, e a
    recuperação troca a senha de uma conta que já funciona.
    """
    with aplicacao.app_context():
        hospede_id = hospede_repo.criar({
            "nome": "Não Confirmou",
            "documento": "111.444.777-35",
            "email": "nao.confirmou@exemplo.com",
        })
        usuario_repo.criar(
            login=usuario_repo.login_de_email("nao.confirmou@exemplo.com"),
            nome="Não Confirmou",
            senha_hash=senhas.gerar_hash(SENHA),
            perfil="hospede",
            hospede_id=hospede_id,
            ativo=False,
        )

    visitante = aplicacao.test_client()
    resposta = _pedir(visitante, "nao.confirmou@exemplo.com")

    assert resposta.status_code == 302
    assert _enviadas(pasta_de_emails) == [], (
        "conta inativa não pode receber link de redefinição"
    )


# ---------------------------------------------------------------------------
# Os tipos não se substituem
# ---------------------------------------------------------------------------

def test_token_de_confirmacao_nao_redefine_senha(aplicacao, conta, visitante):
    """Um link de ativação de cadastro não troca senha.

    Sem essa separação, quem conseguisse um token de um fluxo o usaria no
    outro — e os dois não têm o mesmo significado nem o mesmo efeito.
    """
    with aplicacao.app_context():
        de_confirmacao = token_repo.gerar(
            conta["usuario_id"], token_repo.CONFIRMACAO
        )

    resposta = visitante.get(f"/redefinir/{de_confirmacao}")

    assert resposta.status_code == 400
    assert "não vale mais" in resposta.data.decode()


def test_token_de_recuperacao_nao_ativa_conta(aplicacao, conta, visitante):
    """E o contrário também."""
    with aplicacao.app_context():
        de_recuperacao = token_repo.gerar(
            conta["usuario_id"], token_repo.RECUPERACAO
        )

    assert visitante.get(f"/confirmar/{de_recuperacao}").status_code == 400


@pytest.mark.parametrize(
    "rotulo,token",
    [
        ("inexistente", "x" * 43),
        ("texto qualquer", "nao-existe"),
        ("curto", "a"),
    ],
)
def test_link_que_nao_serve_e_recusado(visitante, rotulo, token):
    resposta = visitante.get(f"/redefinir/{token}")

    assert resposta.status_code == 400, rotulo
    assert 'name="senha"' not in resposta.data.decode(), rotulo


def test_a_tela_de_falha_nao_aponta_a_causa(visitante, conta, pasta_de_emails):
    """Token inexistente, vencido, já usado e de outro tipo dão a mesma
    tela — distinguir ensinaria a quem sonda se um token existe."""
    _pedir(visitante, EMAIL)
    token = _link(pasta_de_emails)

    visitante.post(f"/redefinir/{token}", data={
        "senha": NOVA,
        "senha_confirmacao": NOVA,
        "csrf_token": _token(visitante, f"/redefinir/{token}"),
    })

    usado = visitante.get(f"/redefinir/{token}")
    inexistente = visitante.get(f"/redefinir/{'z' * 43}")

    assert usado.status_code == inexistente.status_code
    assert usado.data == inexistente.data


# ---------------------------------------------------------------------------
# O log
# ---------------------------------------------------------------------------

def test_o_pedido_e_a_troca_ficam_no_log(
    aplicacao, conta, visitante, pasta_de_emails
):
    _pedir(visitante, EMAIL)
    token = _link(pasta_de_emails)
    visitante.post(f"/redefinir/{token}", data={
        "senha": NOVA,
        "senha_confirmacao": NOVA,
        "csrf_token": _token(visitante, f"/redefinir/{token}"),
    })

    with aplicacao.app_context():
        linhas = [
            linha for linha in auditoria_repo.listar(limite=80)
            if linha["acao"] == auditoria_repo.SENHA_ALTERADA
        ]

    detalhes = " ".join(str(linha["detalhe"]) for linha in linhas)

    assert "enviado" in detalhes, "o pedido do link não foi registrado"
    assert "redefinida" in detalhes, "a troca não foi registrada"
    assert NOVA not in detalhes, "a senha foi parar no log"
    assert SENHA not in detalhes


# ---------------------------------------------------------------------------
# O serviço, isolado da rota
# ---------------------------------------------------------------------------

def test_solicitar_nao_devolve_nada(aplicacao, conta):
    """O retorno vazio é parte da defesa.

    Se devolvesse algo, a rota poderia ramificar sobre ele — por
    descuido, numa alteração futura — e a diferença entre endereço com
    conta e sem conta apareceria na tela.
    """
    with aplicacao.app_context():
        assert recuperacao.solicitar(EMAIL, aplicacao.villaju) is None
        assert recuperacao.solicitar(
            "ninguem@exemplo.com", aplicacao.villaju
        ) is None


def test_token_vale_devolve_booleano(aplicacao, conta):
    """E não o identificador: a tela não precisa saber de quem é a conta,
    e o que não é devolvido não vaza."""
    with aplicacao.app_context():
        token = token_repo.gerar(
            conta["usuario_id"], token_repo.RECUPERACAO
        )

        assert recuperacao.token_vale(token) is True
        assert recuperacao.token_vale("x" * 43) is False


def test_a_falha_do_correio_nao_derruba_o_pedido(
    aplicacao, conta, monkeypatch
):
    """Um erro de SMTP não pode virar página de erro.

    Se virasse, o tempo e o código de estado passariam a distinguir
    endereço com conta de endereço sem conta — exatamente o que a
    resposta uniforme existe para impedir.
    """
    def explodir(*args, **kwargs):
        raise correio.ErroDeCorreio("servidor fora do ar")

    monkeypatch.setattr(correio, "enviar", explodir)

    visitante = aplicacao.test_client()
    resposta = _pedir(visitante, EMAIL)

    assert resposta.status_code == 302
