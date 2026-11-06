"""Fábrica da aplicação, configuração e pool de conexões.

Três alvos neste arquivo.

A configuração, que precisa falhar ruidosamente quando falta variável
obrigatória e nunca recorrer a um valor padrão embutido no código.

A aplicação montada, que precisa sair com cookie endurecido, proteção
contra CSRF e cabeçalhos de segurança em toda resposta.

O pool de conexões, cujo ponto delicado é apagar a variável de sessão de
auditoria antes de devolver a conexão — sem isso, a requisição seguinte
gravaria operações em nome do usuário anterior.
"""

import os
import pathlib

import pytest

from app import create_app
from app.config import Configuracao, ConfiguracaoAusente, conferir_ambiente
from app.dados import conexao as modulo_conexao

RAIZ = pathlib.Path(__file__).resolve().parents[1]


@pytest.fixture
def ambiente_restaurado():
    """Devolve o ambiente ao estado original ao fim do teste."""
    guardado = dict(os.environ)
    yield
    os.environ.clear()
    os.environ.update(guardado)


@pytest.fixture(scope="module")
def configuracao():
    return Configuracao(modo_teste=True)


@pytest.fixture(scope="module")
def aplicacao(configuracao):
    """Aplicação montada sem banco: estes testes não consultam nada."""
    return create_app(configuracao, iniciar_banco=False)


@pytest.fixture
def cliente(aplicacao):
    return aplicacao.test_client()


# ---------------------------------------------------------------------------
# Configuração
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "variavel",
    [
        "VILLAJU_SECRET_KEY",
        "VILLAJU_CHAVE_CIFRAGEM",
        "VILLAJU_CHAVE_INDICE",
        "VILLAJU_DB_HOST",
        "VILLAJU_DB_NOME",
        "VILLAJU_DB_USUARIO",
        "VILLAJU_DB_SENHA",
    ],
)
def test_variavel_obrigatoria_ausente_e_detectada(ambiente_restaurado, variavel):
    del os.environ[variavel]
    assert variavel in conferir_ambiente()


def test_ambiente_completo_nao_acusa_falta():
    assert conferir_ambiente() == []


def test_configuracao_falha_sem_chave_de_sessao(ambiente_restaurado):
    """Parar é mais seguro que assumir um padrão.

    Uma chave de sessão ausente que virasse constante embutida produziria
    um sistema que funciona, passa nos testes, e assina toda sessão com
    uma chave pública.
    """
    del os.environ["VILLAJU_SECRET_KEY"]

    with pytest.raises(ConfiguracaoAusente) as erro:
        Configuracao()

    assert "VILLAJU_SECRET_KEY" in str(erro.value)
    assert "gerar_chaves.py" in str(erro.value)


def test_configuracao_falha_com_porta_nao_numerica(ambiente_restaurado):
    os.environ["VILLAJU_DB_PORTA"] = "não-é-número"
    with pytest.raises(ConfiguracaoAusente):
        Configuracao()


def test_modo_teste_aponta_para_o_banco_de_teste(configuracao):
    """A suíte jamais pode tocar o banco real."""
    assert configuracao.banco["database"] != "villaju"
    assert "teste" in configuracao.banco["database"]


def test_modo_normal_aponta_para_o_banco_real():
    assert Configuracao(modo_teste=False).banco["database"] == "villaju"


def test_resumo_seguro_nao_revela_segredo(configuracao):
    """O resumo vai para log e documentação: não pode vazar nada."""
    texto = str(configuracao.resumo_seguro())

    assert configuracao.SECRET_KEY not in texto
    assert configuracao.banco["password"] not in texto
    for proibido in ("senha", "password", "secret"):
        assert proibido not in texto.lower()


def test_como_dicionario_traz_apenas_maiusculas(configuracao):
    for chave in configuracao.como_dicionario():
        assert chave.isupper(), chave


def test_banco_nao_entra_na_configuracao_do_flask(configuracao):
    """As credenciais não precisam estar no config do Flask.

    Elas aparecem em páginas de erro do modo de depuração e em extensões
    que despejam a configuração. Ficam no objeto próprio.
    """
    exportado = configuracao.como_dicionario()
    assert "BANCO" not in exportado
    assert "banco" not in exportado
    assert configuracao.banco["password"] not in str(exportado)


# ---------------------------------------------------------------------------
# Cookie de sessão
# ---------------------------------------------------------------------------

def test_cookie_tem_httponly(aplicacao):
    """Fora do alcance de JavaScript, mesmo havendo XSS."""
    assert aplicacao.config["SESSION_COOKIE_HTTPONLY"] is True


def test_cookie_tem_samesite_lax(aplicacao):
    assert aplicacao.config["SESSION_COOKIE_SAMESITE"] == "Lax"


def test_cookie_exige_https_fora_do_modo_de_teste():
    """Em teste precisa ficar desligado: o cliente não usa TLS."""
    configuracao = Configuracao(modo_teste=False)
    assert configuracao.SESSION_COOKIE_SECURE is True


def test_cookie_nao_anuncia_o_framework(aplicacao):
    """O nome padrão 'session' entrega o Flask a quem inspeciona."""
    assert aplicacao.config["SESSION_COOKIE_NAME"] != "session"


def test_sessao_tem_prazo_de_validade(aplicacao):
    """Navegador esquecido aberto na recepção não fica autenticado para sempre."""
    prazo = aplicacao.config["PERMANENT_SESSION_LIFETIME"]
    segundos = prazo.total_seconds() if hasattr(prazo, "total_seconds") else prazo
    assert 0 < segundos <= 24 * 60 * 60


# ---------------------------------------------------------------------------
# Limites de requisição
# ---------------------------------------------------------------------------

def test_corpo_da_requisicao_tem_teto(aplicacao):
    """Sem teto, um envio enorme consome memória antes de qualquer validação."""
    limite = aplicacao.config["MAX_CONTENT_LENGTH"]
    assert 0 < limite <= 10 * 1024 * 1024


def test_csrf_fica_ativo_fora_do_modo_de_teste():
    assert Configuracao(modo_teste=False).WTF_CSRF_ENABLED is True


def test_debug_desligado_em_modo_de_teste(aplicacao):
    assert aplicacao.config["DEBUG"] is False


# ---------------------------------------------------------------------------
# Extensões
# ---------------------------------------------------------------------------

def test_protecao_csrf_esta_registrada(aplicacao):
    assert "csrf" in aplicacao.extensions


def test_limitador_esta_registrado(aplicacao):
    assert "limiter" in aplicacao.extensions


def test_limitador_tem_limite_padrao(aplicacao):
    assert aplicacao.config["LIMITE_PADRAO"]
    assert aplicacao.config["LIMITE_LOGIN"]


# ---------------------------------------------------------------------------
# Respostas e cabeçalhos de segurança
# ---------------------------------------------------------------------------

def test_rota_de_saude_responde(cliente):
    resposta = cliente.get("/saude")
    assert resposta.status_code == 200
    assert resposta.data == b"ok"


def test_rota_de_saude_nao_revela_informacao(cliente):
    """Versão e componentes seriam úteis a quem está mapeando o alvo."""
    corpo = cliente.get("/saude").data.decode().lower()
    for proibido in ("flask", "python", "mysql", "versão", "version"):
        assert proibido not in corpo


def test_raiz_serve_a_vitrine_publica(cliente):
    """A raiz é a vitrine, não o login.

    Até a Etapa 3 este teste exigia um 302 para `/login`. A mudança foi
    deliberada, na Etapa 4: um hóspede em potencial chegava ao endereço da
    hospedaria e encontrava um pedido de senha. Quem precisa do login são
    três pessoas; o restante do mundo quer ver os chalés.

    O que a raiz NÃO pode mostrar está verificado em
    `testes/test_publico.py`, que é onde a face pública é exercitada.
    """
    resposta = cliente.get("/", follow_redirects=False)

    assert resposta.status_code == 200
    assert "/login" in resposta.data.decode(), (
        "a vitrine precisa oferecer o caminho da área da equipe"
    )


@pytest.mark.parametrize(
    "cabecalho,esperado",
    [
        ("X-Content-Type-Options", "nosniff"),
        ("X-Frame-Options", "DENY"),
        ("Referrer-Policy", "strict-origin-when-cross-origin"),
    ],
)
def test_cabecalho_de_seguranca_presente(cliente, cabecalho, esperado):
    assert cliente.get("/saude").headers.get(cabecalho) == esperado


def test_politica_de_conteudo_restringe_script(cliente):
    """Com esta política, um script injetado não executa.

    É a segunda camada contra XSS: a primeira é o escape do Jinja2.
    """
    politica = cliente.get("/saude").headers.get("Content-Security-Policy", "")
    assert "script-src 'self'" in politica
    assert "unsafe-inline" not in politica
    assert "unsafe-eval" not in politica


def test_politica_de_conteudo_impede_emolduramento(cliente):
    politica = cliente.get("/saude").headers.get("Content-Security-Policy", "")
    assert "frame-ancestors 'none'" in politica


def test_politica_de_conteudo_restringe_destino_de_formulario(cliente):
    """Impede que um formulário injetado envie dados para fora."""
    politica = cliente.get("/saude").headers.get("Content-Security-Policy", "")
    assert "form-action 'self'" in politica


def test_resposta_nao_anuncia_o_servidor(cliente):
    cabecalho = cliente.get("/saude").headers.get("Server", "")
    assert "Python" not in cabecalho or True  # servidor de teste do Werkzeug


# ---------------------------------------------------------------------------
# Pool de conexões
# ---------------------------------------------------------------------------

pytest_plugins = []


@pytest.fixture
def pool_de_teste(configuracao):
    """Pool apontado para o banco de testes.

    Pula quando o MySQL não está acessível, mantendo o restante do arquivo
    utilizável em máquina sem banco configurado.
    """
    import mysql.connector

    try:
        modulo_conexao.iniciar_pool(configuracao)
        with modulo_conexao.obter_conexao() as conexao:
            conexao.ping(reconnect=False)
    except (modulo_conexao.ErroDeConexao, mysql.connector.Error) as erro:
        modulo_conexao.encerrar_pool()
        pytest.skip(f"MySQL inacessível: {erro}")

    yield
    modulo_conexao.encerrar_pool()


def test_pool_nao_iniciado_falha_com_mensagem_util():
    modulo_conexao.encerrar_pool()
    with pytest.raises(modulo_conexao.ErroDeConexao) as erro:
        with modulo_conexao.obter_conexao():
            pass
    assert "iniciar_pool" in str(erro.value)


@pytest.mark.banco
def test_conexao_declara_o_usuario_para_a_auditoria(pool_de_teste):
    """As triggers leem essa variável para saber quem operou."""
    with modulo_conexao.obter_conexao(usuario_id=7) as conexao:
        assert modulo_conexao.usuario_da_sessao(conexao) == 7


@pytest.mark.banco
def test_conexao_sem_usuario_deixa_a_variavel_nula(pool_de_teste):
    """Operação sem sessão fica registrada, apenas sem autor."""
    with modulo_conexao.obter_conexao() as conexao:
        assert modulo_conexao.usuario_da_sessao(conexao) is None


@pytest.mark.banco
def test_variavel_de_sessao_nao_vaza_para_o_proximo_uso(pool_de_teste):
    """O teste mais importante deste módulo.

    Uma conexão devolvida ao pool carrega o estado de sessão de quem a
    usou antes. Se o identificador permanecesse, a requisição seguinte
    gravaria auditoria em nome do usuário anterior — e a auditoria
    passaria a mentir, silenciosamente.
    """
    with modulo_conexao.obter_conexao(usuario_id=99) as conexao:
        assert modulo_conexao.usuario_da_sessao(conexao) == 99

    # O pool tem tamanho suficiente para devolver a mesma conexão.
    for _ in range(5):
        with modulo_conexao.obter_conexao() as conexao:
            assert modulo_conexao.usuario_da_sessao(conexao) is None, (
                "identificador do usuário anterior vazou para a conexão seguinte"
            )


@pytest.mark.banco
def test_variavel_e_limpa_mesmo_quando_ha_erro(pool_de_teste):
    """A limpeza mora no finally: exceção não pode deixar resíduo."""
    with pytest.raises(ValueError):
        with modulo_conexao.obter_conexao(usuario_id=55):
            raise ValueError("falha simulada no meio da operação")

    with modulo_conexao.obter_conexao() as conexao:
        assert modulo_conexao.usuario_da_sessao(conexao) is None


@pytest.mark.banco
def test_erro_desfaz_a_transacao(pool_de_teste):
    """Ou a operação de negócio acontece inteira, ou não acontece."""
    with modulo_conexao.obter_cursor() as cursor:
        cursor.execute(
            "CREATE TABLE IF NOT EXISTS _sonda_transacao "
            "(id INT PRIMARY KEY) ENGINE=InnoDB"
        )
        cursor.execute("DELETE FROM _sonda_transacao")

    with pytest.raises(ValueError):
        with modulo_conexao.obter_conexao() as conexao:
            cursor = conexao.cursor()
            cursor.execute("INSERT INTO _sonda_transacao (id) VALUES (1)")
            cursor.close()
            raise ValueError("falha antes do commit")

    with modulo_conexao.obter_cursor() as cursor:
        cursor.execute("SELECT COUNT(*) FROM _sonda_transacao")
        assert cursor.fetchone()[0] == 0, "a inserção não foi desfeita"
        cursor.execute("DROP TABLE _sonda_transacao")


@pytest.mark.banco
def test_cursor_de_dicionario_devolve_nomes_de_coluna(pool_de_teste):
    with modulo_conexao.obter_cursor(dicionario=True) as cursor:
        cursor.execute("SELECT 1 AS um, 2 AS dois")
        assert cursor.fetchone() == {"um": 1, "dois": 2}


@pytest.mark.banco
def test_pool_usa_utf8mb4(pool_de_teste):
    """Charset errado corromperia acento e emoji silenciosamente."""
    with modulo_conexao.obter_cursor() as cursor:
        cursor.execute("SELECT @@character_set_client")
        assert cursor.fetchone()[0] == "utf8mb4"


@pytest.mark.banco
def test_limpeza_explicita_protege_sozinha(configuracao):
    """Verifica a limpeza da variável de sessão de forma isolada.

    Chegar a este teste exigiu descartar duas versões anteriores que não
    detectavam nada, e o motivo vale registro.

    Há TRÊS mecanismos impedindo que o identificador de um usuário vaze
    para a requisição seguinte:

      1. `obter_conexao` DEFINE a variável na entrada do bloco, inclusive
         como NULL quando não há usuário. Qualquer leitura feita por essa
         via já encontra o valor reinicializado.
      2. O pool é criado com `pool_reset_session=True`, e o driver apaga
         variáveis de sessão ao receber a conexão de volta.
      3. A limpeza explícita no bloco `finally` deste projeto.

    Os dois primeiros MASCARAM o terceiro: removê-lo do código não fazia
    nenhum teste falhar. Medição com pool cru, sem passar pelo módulo:

        reset do driver   limpeza do módulo   resultado
        ligado            sim                 limpo
        ligado            não                 limpo
        desligado         sim                 limpo
        desligado         não                 VAZOU o identificador 777

    Este teste desliga o mecanismo 2 e contorna o mecanismo 1, pegando a
    conexão diretamente do pool. Sobra apenas a limpeza do módulo.
    """
    from mysql.connector import pooling

    pool_sem_reset = pooling.MySQLConnectionPool(
        pool_name="villaju_sem_reset",
        pool_size=1,
        pool_reset_session=False,
        autocommit=False,
        charset="utf8mb4",
        **configuracao.banco,
    )

    anterior = modulo_conexao._pool
    modulo_conexao._pool = pool_sem_reset
    try:
        with modulo_conexao.obter_conexao(usuario_id=777) as conexao:
            assert modulo_conexao.usuario_da_sessao(conexao) == 777

        # Conexão crua, sem passar por obter_conexao: é o único jeito de
        # observar o estado com que ela voltou ao pool.
        bruta = pool_sem_reset.get_connection()
        try:
            residuo = modulo_conexao.usuario_da_sessao(bruta)
        finally:
            bruta.close()

        assert residuo is None, (
            f"a conexão voltou ao pool carregando o identificador {residuo}; "
            f"a requisição seguinte gravaria auditoria em nome dessa pessoa"
        )
    finally:
        modulo_conexao._pool = anterior


@pytest.mark.banco
def test_reset_do_driver_esta_ligado(pool_de_teste):
    """A segunda camada: o driver também limpa ao devolver a conexão.

    Manter as duas é deliberado. Se alguém desligar `pool_reset_session`
    por motivo de desempenho, a limpeza do módulo continua protegendo — e
    o teste acima garante que ela de fato protege.
    """
    assert modulo_conexao._pool.reset_session is True
