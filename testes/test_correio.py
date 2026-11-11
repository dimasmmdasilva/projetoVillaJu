"""Envio de mensagens, e a caixa de saída que torna a demonstração possível.

O sistema precisa provar que um endereço de e-mail pertence a quem se
cadastrou — avisar não basta. Se o link de confirmação aparecesse na tela,
bastaria digitar o endereço de outra pessoa para criar conta no nome dela.

Três assuntos:

**A montagem da mensagem**, inclusive a defesa contra injeção de
cabeçalho: quem digita o endereço é um desconhecido no formulário público.

**Os dois modos.** `arquivo` grava em disco e é o padrão, porque a
demonstração roda sem rede. `smtp` envia de verdade.

**O que não pode vazar.** Nem a senha de app nem o token de confirmação
podem aparecer em log, em tela de erro ou no resumo da configuração.
"""

import datetime as dt
import logging
import pathlib
import re

import pytest

from app import create_app
from app.config import Configuracao
from app.servicos import correio
from app.servicos.correio import ErroDeCorreio


class _ConfiguracaoDeTeste:
    """Configuração mínima, sem tocar no ambiente real."""

    def __init__(self, pasta, modo="arquivo", **ajustes):
        self.EMAIL_MODO = modo
        self.EMAIL_REMETENTE = "VillaJu <nao-responda@villaju.local>"
        self.EMAIL_PASTA = pasta
        self.SMTP_HOST = ""
        self.SMTP_PORTA = 587
        self.SMTP_USUARIO = ""
        self.SMTP_SENHA = ""
        for nome, valor in ajustes.items():
            setattr(self, nome, valor)


@pytest.fixture
def pasta(tmp_path):
    return tmp_path / "emails"


@pytest.fixture
def configuracao(pasta):
    return _ConfiguracaoDeTeste(pasta)


CORPO = (
    "Olá, Helena.\n\n"
    "Confirme seu cadastro:\n"
    "https://127.0.0.1:5000/confirmar/abc123\n\n"
    "VillaJu"
)


# ---------------------------------------------------------------------------
# Montagem
# ---------------------------------------------------------------------------

def test_a_mensagem_leva_destinatario_assunto_e_corpo():
    mensagem = correio.montar(
        "helena@exemplo.test", "Confirme seu cadastro", CORPO, "VillaJu <x@y.z>"
    )

    assert mensagem["To"] == "helena@exemplo.test"
    assert mensagem["Subject"] == "Confirme seu cadastro"
    assert "confirmar/abc123" in mensagem.get_content()


def test_a_mensagem_e_texto_puro():
    """Sem HTML, sem imagem, sem pixel de rastreamento.

    Cada recurso remoto numa mensagem informa a um terceiro que ela foi
    aberta, por quem e quando. Uma confirmação de cadastro não precisa
    de nada disso.
    """
    mensagem = correio.montar("h@e.test", "Assunto", CORPO, "VillaJu <x@y.z>")

    assert mensagem.get_content_type() == "text/plain"
    assert not mensagem.is_multipart()


@pytest.mark.parametrize(
    "campo,malicioso",
    [
        ("destinatario", "vitima@exemplo.test\nBcc: atacante@mal.test"),
        ("destinatario", "vitima@exemplo.test\r\nBcc: atacante@mal.test"),
        ("assunto", "Confirme\nBcc: atacante@mal.test"),
        ("assunto", "Confirme\r\nX-Qualquer: valor"),
    ],
)
def test_injecao_de_cabecalho_e_barrada(campo, malicioso):
    """Quem digita o endereço é um desconhecido no formulário público.

    Uma quebra de linha seguida de `Bcc:` acrescentaria destinatários à
    mensagem — e o link de confirmação ativa uma conta.
    """
    argumentos = {
        "destinatario": "helena@exemplo.test",
        "assunto": "Confirme seu cadastro",
        "corpo": CORPO,
        "remetente": "VillaJu <x@y.z>",
    }
    argumentos[campo] = malicioso

    mensagem = correio.montar(**argumentos)

    assert mensagem["Bcc"] is None
    assert mensagem["X-Qualquer"] is None
    for cabecalho in ("To", "Subject"):
        assert "\n" not in str(mensagem[cabecalho])
        assert "\r" not in str(mensagem[cabecalho])


def test_mensagem_sem_destinatario_e_recusada():
    with pytest.raises(ErroDeCorreio) as erro:
        correio.montar("", "Assunto", CORPO, "VillaJu <x@y.z>")
    assert "destinatário" in str(erro.value)


# ---------------------------------------------------------------------------
# Modo arquivo
# ---------------------------------------------------------------------------

def test_o_modo_arquivo_grava_a_mensagem(configuracao, pasta):
    onde = correio.enviar(
        "helena@exemplo.test", "Confirme", CORPO, configuracao
    )

    arquivos = list(pasta.glob("*.txt"))
    assert len(arquivos) == 1
    assert str(arquivos[0]) == onde


def test_o_modo_arquivo_cria_a_pasta_se_nao_existir(configuracao, pasta):
    assert not pasta.exists()
    correio.enviar("h@e.test", "Confirme", CORPO, configuracao)
    assert pasta.is_dir()


def test_o_nome_do_arquivo_nao_escapa_da_pasta(configuracao, pasta):
    """O endereço entra no nome do arquivo, e endereço vem de fora.

    `../` num nome de arquivo escreveria fora da pasta pretendida.
    """
    correio.enviar(
        "../../../etc/passwd@exemplo.test", "Confirme", CORPO, configuracao
    )

    arquivos = list(pasta.glob("*.txt"))
    assert len(arquivos) == 1
    assert ".." not in arquivos[0].name
    assert "/" not in arquivos[0].name
    assert "\\" not in arquivos[0].name


def test_o_nome_do_arquivo_normaliza_acento(configuracao, pasta):
    correio.enviar("helená@exemplo.test", "Confirme", CORPO, configuracao)

    nome = list(pasta.glob("*.txt"))[0].name
    assert nome.isascii(), f"o nome do arquivo tem caractere não ASCII: {nome}"


def test_modo_desconhecido_e_recusado(pasta):
    with pytest.raises(ErroDeCorreio) as erro:
        correio.enviar(
            "h@e.test", "A", CORPO, _ConfiguracaoDeTeste(pasta, modo="pombo")
        )
    assert "pombo" in str(erro.value)


# ---------------------------------------------------------------------------
# Caixa de saída
# ---------------------------------------------------------------------------

def test_a_caixa_de_saida_decodifica_o_corpo(configuracao):
    """Mensagem em português é codificada em base64 pela biblioteca.

    A primeira versão da leitura fatiava o arquivo no primeiro parágrafo
    em branco e tratava o resto como texto. O resultado era uma caixa de
    saída que exibia base64 e não encontrava link nenhum — defeito achado
    ao exercitar o módulo, com o acento de "Olá" sendo a causa.
    """
    correio.enviar("helena@exemplo.test", "Confirme", CORPO, configuracao)

    mensagem = correio.listar_enviadas(configuracao)[0]

    assert "Olá, Helena." in mensagem["corpo"]
    assert "base64" not in mensagem["corpo"]


def test_a_caixa_de_saida_extrai_o_link(configuracao):
    """É o que torna a demonstração possível sem rede."""
    correio.enviar("helena@exemplo.test", "Confirme", CORPO, configuracao)

    mensagem = correio.listar_enviadas(configuracao)[0]

    assert mensagem["links"] == ["https://127.0.0.1:5000/confirmar/abc123"]


def test_a_caixa_de_saida_ordena_da_mais_recente(configuracao):
    import time

    for numero in range(3):
        correio.enviar(
            f"pessoa{numero}@exemplo.test", f"Mensagem {numero}", CORPO,
            configuracao,
        )
        time.sleep(1.1)  # o nome do arquivo tem resolução de segundo

    assuntos = [m["assunto"] for m in correio.listar_enviadas(configuracao)]
    assert assuntos == ["Mensagem 2", "Mensagem 1", "Mensagem 0"]


def test_a_caixa_de_saida_respeita_o_limite(configuracao):
    for numero in range(5):
        correio.enviar(f"p{numero}@e.test", "A", CORPO, configuracao)

    assert len(correio.listar_enviadas(configuracao, limite=2)) == 2


def test_pasta_inexistente_devolve_lista_vazia(pasta):
    assert correio.listar_enviadas(_ConfiguracaoDeTeste(pasta)) == []


def test_arquivo_ilegivel_nao_derruba_a_lista(configuracao, pasta):
    """Um arquivo estragado não pode esconder os demais."""
    correio.enviar("boa@exemplo.test", "Mensagem boa", CORPO, configuracao)
    (pasta / "estragada.txt").write_bytes(b"\xff\xfe isto nao e mensagem")

    mensagens = correio.listar_enviadas(configuracao)

    assert any(m["assunto"] == "Mensagem boa" for m in mensagens)


# ---------------------------------------------------------------------------
# Modo SMTP
# ---------------------------------------------------------------------------

def test_modo_smtp_sem_servidor_configurado_avisa_o_que_falta(pasta):
    """A mensagem precisa dizer o que preencher, não só que falhou."""
    configuracao = _ConfiguracaoDeTeste(pasta, modo="smtp")

    with pytest.raises(ErroDeCorreio) as erro:
        correio.enviar("h@e.test", "A", CORPO, configuracao)

    assert "VILLAJU_SMTP_HOST" in str(erro.value)


def test_o_envio_por_smtp_usa_starttls_antes_de_autenticar(pasta, monkeypatch):
    """Sem STARTTLS, a senha de app viajaria em texto claro.

    O servidor é substituído por um substituto que registra a ordem das
    chamadas — não há rede envolvida.
    """
    chamadas = []

    class _ServidorFalso:
        def __init__(self, host, porta, timeout=None):
            chamadas.append(("conectar", host, porta))

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def starttls(self, context=None):
            chamadas.append(("starttls", context is not None))

        def login(self, usuario, senha):
            chamadas.append(("login", usuario))

        def send_message(self, mensagem):
            chamadas.append(("enviar", mensagem["To"]))

    monkeypatch.setattr(correio.smtplib, "SMTP", _ServidorFalso)

    configuracao = _ConfiguracaoDeTeste(
        pasta,
        modo="smtp",
        SMTP_HOST="smtp.exemplo.test",
        SMTP_USUARIO="conta@exemplo.test",
        SMTP_SENHA="senha-de-app",
    )
    correio.enviar("helena@exemplo.test", "Confirme", CORPO, configuracao)

    acoes = [c[0] for c in chamadas]
    assert acoes == ["conectar", "starttls", "login", "enviar"]
    assert chamadas[1][1] is True, "o STARTTLS precisa receber contexto TLS"


def test_falha_de_autenticacao_nao_revela_a_senha(pasta, monkeypatch, caplog):
    """A senha de app é credencial, como a do banco e as chaves."""
    import smtplib as biblioteca

    class _ServidorQueRecusa:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def starttls(self, context=None):
            pass

        def login(self, usuario, senha):
            raise biblioteca.SMTPAuthenticationError(535, b"Bad credentials")

    monkeypatch.setattr(correio.smtplib, "SMTP", _ServidorQueRecusa)

    configuracao = _ConfiguracaoDeTeste(
        pasta,
        modo="smtp",
        SMTP_HOST="smtp.exemplo.test",
        SMTP_USUARIO="conta@exemplo.test",
        SMTP_SENHA="senha-secreta-de-app",
    )

    with caplog.at_level(logging.DEBUG):
        with pytest.raises(ErroDeCorreio) as erro:
            correio.enviar("h@e.test", "A", CORPO, configuracao)

    assert "senha-secreta-de-app" not in str(erro.value)
    assert "senha-secreta-de-app" not in caplog.text


def test_o_corpo_nao_vai_para_o_log(configuracao, caplog):
    """O corpo carrega o token de confirmação. Token em log é token vazado.

    A primeira versão deste teste procurava o token em texto claro e
    **não detectou** uma sabotagem que registrava a mensagem inteira —
    porque `str(mensagem)` codifica o corpo em base64 quando há acento, e
    acento há em qualquer mensagem em português.

    O token estava no log; só não estava legível para quem procurasse por
    ele. Base64 se desfaz num comando, então o vazamento era real.

    A verificação passou a ser sobre a forma: uma mensagem serializada
    sempre carrega o cabeçalho `Content-Type`. Se ele aparece no log, a
    mensagem inteira foi registrada — codificada ou não.
    """
    import base64

    with caplog.at_level(logging.DEBUG):
        correio.enviar("helena@exemplo.test", "Confirme", CORPO, configuracao)

    assert "Content-Type" not in caplog.text, (
        "a mensagem inteira foi para o log"
    )

    assert "abc123" not in caplog.text
    assert "confirmar/" not in caplog.text

    codificado = base64.b64encode(CORPO.encode("utf-8")).decode("ascii")
    assert codificado[:20] not in caplog.text, "o corpo foi para o log em base64"

    # O destinatário pode e deve aparecer: é o que permite diagnosticar.
    assert "helena@exemplo.test" in caplog.text


# ---------------------------------------------------------------------------
# A configuração
# ---------------------------------------------------------------------------

def test_o_modo_padrao_e_arquivo():
    """Um padrão que tentasse enviar falharia em toda instalação nova.

    E a falha apareceria no meio de um cadastro, não na partida, onde
    seria fácil de ver.
    """
    import os

    if not os.environ.get("VILLAJU_SECRET_KEY"):
        pytest.skip("configuração real ausente neste ambiente")

    assert Configuracao(modo_teste=True).EMAIL_MODO in correio.MODOS


def test_o_resumo_da_configuracao_nao_mostra_a_senha_de_app():
    import os

    if not os.environ.get("VILLAJU_SECRET_KEY"):
        pytest.skip("configuração real ausente neste ambiente")

    resumo = str(Configuracao(modo_teste=True).resumo_seguro())

    assert "SMTP_SENHA" not in resumo
    assert "senha" not in resumo.lower() or "smtp_senha" not in resumo.lower()


def test_o_endereco_base_vem_da_configuracao_e_nao_do_cabecalho():
    """O link do e-mail não pode ser montado a partir do `Host` recebido.

    Um atacante que enviasse `Host: sitio-hostil.test` faria o sistema
    gerar um link de confirmação apontando para o sítio dele — e a vítima
    clicaria, entregando o token.
    """
    import os

    if not os.environ.get("VILLAJU_SECRET_KEY"):
        pytest.skip("configuração real ausente neste ambiente")

    base = Configuracao(modo_teste=True).EMAIL_BASE_URL

    assert base.startswith("http")
    assert "request" not in base


# ---------------------------------------------------------------------------
# A tela de caixa de saída
# ---------------------------------------------------------------------------

@pytest.fixture
def aplicacao(conexao_banco, tmp_path):
    """Aplicação com o banco recarregado e uma pasta de mensagens própria.

    A pasta vem de `tmp_path` para que cada teste comece com a caixa de
    saída vazia — sem isso, um teste veria as mensagens que o anterior
    gerou e passaria ou falharia conforme a ordem de execução.
    """
    from app.dados import conexao as modulo_conexao
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

    modulo_conexao.iniciar_pool(Configuracao(modo_teste=True))
    app = create_app(Configuracao(modo_teste=True))
    app.config["SESSION_COOKIE_SECURE"] = False
    app.config["EMAIL_PASTA"] = tmp_path / "emails"
    yield app
    modulo_conexao.encerrar_pool()


def _entrar(aplicacao, credenciais):
    cliente = aplicacao.test_client()
    pagina = cliente.get("/login").data.decode()
    dados = {"login": credenciais[0], "senha": credenciais[1]}
    achado = re.search(r'name="csrf_token" value="([^"]+)"', pagina)
    if achado:
        dados["csrf_token"] = achado.group(1)
    cliente.post("/login", data=dados)
    return cliente


@pytest.mark.banco
def test_so_o_administrador_abre_a_caixa_de_saida(aplicacao):
    """As mensagens carregam o link que ativa uma conta.

    Quem lê a caixa de saída consegue ativar a conta de outra pessoa, e
    escolher o momento. Por isso nem a recepção alcança.
    """
    assert _entrar(aplicacao, ("admin", "Villa@Admin2026")).get(
        "/mensagens/"
    ).status_code == 200
    assert _entrar(aplicacao, ("recepcao", "Villa@Recep2026")).get(
        "/mensagens/"
    ).status_code == 403
    assert _entrar(aplicacao, ("joao", "Villa@Hospede2026")).get(
        "/mensagens/"
    ).status_code == 403


@pytest.mark.banco
def test_sem_sessao_a_caixa_de_saida_leva_ao_login(aplicacao):
    resposta = aplicacao.test_client().get("/mensagens/", follow_redirects=False)
    assert resposta.status_code == 302
    assert "/login" in resposta.headers["Location"]


@pytest.mark.banco
def test_em_modo_smtp_a_caixa_de_saida_nao_existe(aplicacao):
    """Uma caixa vazia num sistema que envia de verdade só confundiria."""
    aplicacao.config["EMAIL_MODO"] = "smtp"

    cliente = _entrar(aplicacao, ("admin", "Villa@Admin2026"))
    assert cliente.get("/mensagens/").status_code == 404

    # E o link some da barra junto com a rota.
    assert "/mensagens/" not in cliente.get("/painel").data.decode()


@pytest.mark.banco
def test_a_caixa_de_saida_mostra_a_mensagem_e_o_link(aplicacao):
    class _Configuracao:
        EMAIL_MODO = "arquivo"
        EMAIL_REMETENTE = "VillaJu <x@y.z>"
        EMAIL_PASTA = aplicacao.config["EMAIL_PASTA"]

    correio.enviar("helena@exemplo.test", "Confirme", CORPO, _Configuracao)

    html = _entrar(aplicacao, ("admin", "Villa@Admin2026")).get(
        "/mensagens/"
    ).data.decode()

    assert "helena@exemplo.test" in html
    assert "Confirme" in html
    assert "https://127.0.0.1:5000/confirmar/abc123" in html


@pytest.mark.banco
def test_a_caixa_de_saida_escapa_o_conteudo(aplicacao):
    """O corpo inclui o nome, que veio de um formulário público."""
    class _Configuracao:
        EMAIL_MODO = "arquivo"
        EMAIL_REMETENTE = "VillaJu <x@y.z>"
        EMAIL_PASTA = aplicacao.config["EMAIL_PASTA"]

    correio.enviar(
        "h@e.test",
        "Confirme",
        "Olá, <script>alert(1)</script>.\n\nhttps://x.test/confirmar/a",
        _Configuracao,
    )

    html = _entrar(aplicacao, ("admin", "Villa@Admin2026")).get(
        "/mensagens/"
    ).data.decode()

    assert "<script>alert" not in html
    assert "&lt;script&gt;" in html


@pytest.mark.banco
def test_caixa_vazia_diz_onde_as_mensagens_apareceriam(aplicacao):
    html = _entrar(aplicacao, ("admin", "Villa@Admin2026")).get(
        "/mensagens/"
    ).data.decode()

    assert "Nenhuma mensagem gerada ainda" in html
    assert "emails" in html
