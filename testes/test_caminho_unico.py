"""Caminho único de cadastro, e descarte do que não foi confirmado.

Etapa 5, Bloco 4. Duas regras, e as duas são de **ausência** — o que é
mais difícil de verificar do que presença.

**A primeira: nenhuma tela da face interna cria hóspede.** Dado pessoal é
digitado por quem responde por ele. Antes deste bloco havia dois caminhos:
o autocadastro público e a conversão de um pedido de contato, em que a
equipe completava o documento obtido por telefone.

Verificar que um botão saiu da tela não basta — a rota continuaria
acessível a quem digitasse o endereço. Verificar que a rota saiu também
não basta: outra poderia chamar `hospede_repo.criar`. Por isso há três
níveis de verificação aqui: a rota não responde, nenhuma rota registrada
aponta para criação, e nenhum módulo de rota da face interna chama
`hospede_repo.criar`.

**A segunda: o cadastro não confirmado é descartado.** Um endereço
digitado errado prende o e-mail e o documento, e a segunda tentativa
seria recusada em silêncio pela regra de resposta uniforme.

O prazo mora no **token**, e não na data de criação da conta. Os testes
envelhecem o token, porque `trg_usuario_antes_alterar` protege
`usuario.criado_em` de qualquer alteração — uma tentativa de envelhecer a
conta é desfeita pelo banco sem erro algum, e foi assim que a primeira
versão deste bloco passou sem nunca descartar nada.
"""

import datetime as dt
import pathlib
import re
import tempfile

import pytest

from app import create_app
from app.config import Configuracao
from app.dados import (
    conexao,
    hospede_repo,
    reserva_repo,
    token_repo,
    usuario_repo,
)
from app.seguranca import senhas
from app.servicos import cadastro, correio

ADMIN = ("admin", "Villa@Admin2026")
RECEPCAO = ("recepcao", "Villa@Recep2026")

DOCUMENTO = "529.982.247-25"
OUTRO_DOCUMENTO = "111.444.777-35"
SENHA = "jureia-praia-2026"

NOVO = {
    "nome": "Mariana Prado",
    "documento": DOCUMENTO,
    "email": "mariana.prado@exemplo.com",
    "telefone": "(12) 99888-7766",
    "senha": SENHA,
    "senha_confirmacao": SENHA,
}

RAIZ = pathlib.Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# Massa
# ---------------------------------------------------------------------------

@pytest.fixture
def pasta_de_emails():
    return pathlib.Path(tempfile.mkdtemp(prefix="villaju-b4-"))


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
    configuracao.CADASTRO_MINUTOS = 5

    conexao.iniciar_pool(configuracao)
    app = create_app(configuracao)
    app.config["SESSION_COOKIE_SECURE"] = False
    app.villaju = configuracao

    yield app
    conexao.encerrar_pool()


@pytest.fixture
def visitante(aplicacao):
    return aplicacao.test_client()


def _entrar(aplicacao, credenciais=RECEPCAO):
    cliente = aplicacao.test_client()
    pagina = cliente.get("/login").data.decode()
    dados = {"login": credenciais[0], "senha": credenciais[1]}
    achado = re.search(r'name="csrf_token" value="([^"]+)"', pagina)
    if achado:
        dados["csrf_token"] = achado.group(1)
    cliente.post("/login", data=dados)
    return cliente


def _vencer_tokens(aplicacao, minutos: int = 10):
    """Empurra o prazo dos tokens de confirmação pendentes para trás.

    **No token, e não em `usuario.criado_em`.** A trigger
    `trg_usuario_antes_alterar` faz `SET NEW.criado_em = OLD.criado_em`, e
    qualquer tentativa de envelhecer a conta é desfeita pelo banco em
    silêncio — sem erro, sem aviso, e com o teste passando.
    """
    with aplicacao.app_context():
        with conexao.obter_conexao() as ligacao:
            cursor = ligacao.cursor()
            try:
                cursor.execute(
                    "UPDATE token_acesso "
                    "SET expira_em = NOW() - INTERVAL %s MINUTE "
                    "WHERE tipo = 'confirmacao' AND consumido_em IS NULL",
                    (minutos,),
                )
            finally:
                cursor.close()


def _enviadas(pasta):
    class _Configuracao:
        EMAIL_PASTA = pasta

    return correio.listar_enviadas(_Configuracao)


def _token_do_email(pasta):
    for mensagem in _enviadas(pasta):
        achado = re.search(r"/confirmar/([A-Za-z0-9_\-]+)", mensagem["corpo"])
        if achado:
            return achado.group(1)
    return None


def _criar_hospede_com_conta(ativo: bool, login: str, email: str, documento: str):
    """Hóspede com conta, como a recepção criava antes do Bloco 4."""
    hospede_id = hospede_repo.criar({
        "nome": "Pessoa Cadastrada",
        "documento": documento,
        "email": email,
    })
    usuario_id = usuario_repo.criar(
        login=login,
        nome="Pessoa Cadastrada",
        senha_hash=senhas.gerar_hash("senha-longa-o-bastante"),
        perfil="hospede",
        hospede_id=hospede_id,
        ativo=ativo,
    )
    return hospede_id, usuario_id


# ---------------------------------------------------------------------------
# Primeira regra: nenhuma tela interna cria hóspede
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "rota",
    ["/hospedes/novo", "/contatos/1/converter"],
)
def test_as_rotas_de_criacao_pela_equipe_nao_existem(aplicacao, rota):
    """Nem para quem digita o endereço, e nem para o administrador.

    Autenticado como administrador — o perfil de maior poder do sistema —
    para que a ausência não seja confundida com falta de autorização. 404
    é "não existe"; 403 seria "existe e você não pode".
    """
    sessao = _entrar(aplicacao, ADMIN)

    assert sessao.get(rota).status_code == 404
    assert sessao.post(rota, data={}).status_code == 404


def test_nenhuma_rota_registrada_cria_hospede(aplicacao):
    """A verificação no mapa de rotas, não na tela.

    Um botão removido do molde deixa a rota viva para quem digitar o
    endereço. Este teste olha o que a aplicação de fato registrou.
    """
    nomes = {
        regra.endpoint for regra in aplicacao.url_map.iter_rules()
    }

    assert "hospedes.novo" not in nomes
    assert "contatos.converter" not in nomes

    # O contraste: as rotas que DEVEM existir continuam lá. Sem isso, um
    # mapa vazio — por erro de registro de blueprint — passaria.
    assert "hospedes.listar" in nomes
    assert "hospedes.editar" in nomes
    assert "contatos.ligar" in nomes, (
        "ligar sobrevive: a recepção associa o pedido a um cadastro que a "
        "pessoa fez, sem digitar documento de ninguém"
    )
    assert "publico.criar_conta" in nomes


def test_nenhum_modulo_de_rota_chama_a_criacao_de_hospede():
    """A verificação no código, que é a que não envelhece.

    As duas anteriores confirmam que *estas* rotas saíram. Esta confirma
    que **nenhuma** rota tem como criar hóspede — inclusive uma escrita
    amanhã, por quem não leu esta decisão.

    A exceção é `app/servicos/cadastro.py`, que não é rota: é o serviço do
    autocadastro, e é por desenho o único ponto de criação no sistema.
    """
    import ast

    culpados = []

    for caminho in sorted((RAIZ / "app" / "rotas").glob("*.py")):
        arvore = ast.parse(caminho.read_text(encoding="utf-8"))

        for no in ast.walk(arvore):
            if not isinstance(no, ast.Call):
                continue
            alvo = no.func
            if (
                isinstance(alvo, ast.Attribute)
                and alvo.attr == "criar"
                and isinstance(alvo.value, ast.Name)
                and alvo.value.id == "hospede_repo"
            ):
                culpados.append(f"{caminho.name}:{no.lineno}")

    assert not culpados, (
        f"um módulo de rota cria hóspede, e nenhum deveria: {culpados}"
    )


def test_o_servico_de_cadastro_e_o_unico_que_cria_hospede():
    """O contraste do teste anterior.

    Se ninguém no sistema chamasse `hospede_repo.criar`, a verificação
    acima passaria sem provar nada — e o autocadastro estaria quebrado.
    """
    fonte = (RAIZ / "app" / "servicos" / "cadastro.py").read_text(
        encoding="utf-8"
    )

    assert "hospede_repo.criar(" in fonte


def test_a_tela_de_hospedes_nao_oferece_cadastro(aplicacao):
    sessao = _entrar(aplicacao, ADMIN)
    html = sessao.get("/hospedes/").data.decode()

    assert "Cadastrar hóspede" not in html
    assert "/cadastro" in html, (
        "a tela precisa dizer onde o cadastro é feito, senão a equipe fica "
        "sem resposta para quem liga pedindo"
    )


def test_o_painel_nao_oferece_o_atalho_de_cadastrar_hospede(aplicacao):
    sessao = _entrar(aplicacao, ADMIN)
    html = sessao.get("/painel/").data.decode()

    assert "Cadastrar hóspede" not in html


def test_o_pedido_de_contato_ainda_pode_ser_ligado_a_um_cadastro(aplicacao):
    """O que sobreviveu, e por que precisava sobreviver.

    `contato.hospede_id` e o estado `convertido` existem para o relatório
    de origem dos contatos, previsto na Etapa 6. Remover a conversão sem
    deixar nenhum caminho de ligação mataria o relatório e deixaria uma
    coluna e um valor de ENUM inalcançáveis no esquema.

    A diferença entre as duas é o ponto do bloco: **ligar associa, não
    cria.** O cadastro é o que a pessoa fez.
    """
    with aplicacao.app_context():
        hospede_id, _ = _criar_hospede_com_conta(
            True, "pessoa-cadastrada", "pessoa@exemplo.com", DOCUMENTO
        )

    visitante = aplicacao.test_client()
    visitante.post("/contato", data={
        "nome": "Pessoa Cadastrada",
        "email": "pessoa@exemplo.com",
        "mensagem": "Tem vaga em dezembro?",
    })

    sessao = _entrar(aplicacao, RECEPCAO)

    with aplicacao.app_context():
        from app.dados import contato_repo

        pedidos = contato_repo.listar()
        pedido_id = pedidos[0]["id"]

    pagina = sessao.get(f"/contatos/{pedido_id}").data.decode()
    achado = re.search(r'name="csrf_token" value="([^"]+)"', pagina)

    resposta = sessao.post(
        f"/contatos/{pedido_id}/ligar",
        data={
            "hospede_id": hospede_id,
            "csrf_token": achado.group(1) if achado else "",
        },
    )

    assert resposta.status_code == 302

    with aplicacao.app_context():
        depois = contato_repo.buscar_por_id(pedido_id)

    assert depois["hospede_id"] == hospede_id


# ---------------------------------------------------------------------------
# Segunda regra: o cadastro não confirmado é descartado
# ---------------------------------------------------------------------------

def test_o_token_do_cadastro_vale_a_janela_configurada(aplicacao, visitante):
    """O prazo do link é o da configuração, e não as 48 horas do padrão."""
    visitante.post("/cadastro", data=NOVO)

    with aplicacao.app_context():
        with conexao.obter_cursor(dicionario=True) as cursor:
            cursor.execute(
                "SELECT TIMESTAMPDIFF(MINUTE, criado_em, expira_em) AS minutos "
                "FROM token_acesso WHERE tipo = 'confirmacao' "
                "ORDER BY id DESC LIMIT 1"
            )
            minutos = cursor.fetchone()["minutos"]

    assert minutos == aplicacao.villaju.CADASTRO_MINUTOS


def test_o_email_anuncia_o_prazo_e_o_descarte(visitante, pasta_de_emails):
    """A pessoa precisa saber dos dois, e o segundo é o que a tranquiliza.

    Um e-mail que dissesse apenas "o link vale 5 minutos" assustaria quem
    o abre no minuto 6. Dizer que o cadastro é descartado e pode ser
    refeito transforma um impasse aparente numa instrução.
    """
    visitante.post("/cadastro", data=NOVO)

    corpo = _enviadas(pasta_de_emails)[0]["corpo"]

    assert "5 minutos" in corpo
    assert "descartado" in corpo
    assert "de novo" in corpo


def test_dentro_do_prazo_nada_e_descartado(aplicacao, visitante):
    visitante.post("/cadastro", data=NOVO)

    with aplicacao.app_context():
        assert cadastro.descartar_vencidos() == 0

        conta = usuario_repo.buscar_por_login(
            usuario_repo.login_de_email(NOVO["email"])
        )
    assert conta is not None


def test_passado_o_prazo_o_cadastro_e_descartado(aplicacao, visitante):
    visitante.post("/cadastro", data=NOVO)

    with aplicacao.app_context():
        antes = hospede_repo.contar()

    _vencer_tokens(aplicacao)

    with aplicacao.app_context():
        assert cadastro.descartar_vencidos() == 1
        assert hospede_repo.contar() == antes - 1

        conta = usuario_repo.buscar_por_login(
            usuario_repo.login_de_email(NOVO["email"])
        )
    assert conta is None


def test_o_descarte_libera_o_email_e_o_documento(aplicacao, visitante):
    """A razão de ser do descarte, verificada pelo efeito.

    Sem ele, uma letra errada no endereço trancaria o CPF da pessoa para
    sempre — e a segunda tentativa seria recusada em silêncio, pela regra
    de resposta uniforme, sem que nem ela nem a recepção vissem o motivo.
    """
    visitante.post("/cadastro", data=NOVO)
    _vencer_tokens(aplicacao)

    with aplicacao.app_context():
        cadastro.descartar_vencidos()

    # Mesmo e-mail, mesmo documento, cadastro novo.
    visitante.post("/cadastro", data=NOVO)

    with aplicacao.app_context():
        conta = usuario_repo.buscar_por_login(
            usuario_repo.login_de_email(NOVO["email"])
        )
        hospede = hospede_repo.buscar_por_documento(NOVO["documento"])

    assert conta is not None, "o endereço continuou preso depois do descarte"
    assert hospede is not None, "o documento continuou preso depois do descarte"


def test_o_cadastro_confirmado_nunca_e_descartado(
    aplicacao, visitante, pasta_de_emails
):
    """A condição `ativo = FALSE`, verificada pelo que ela protege."""
    visitante.post("/cadastro", data=NOVO)
    token = _token_do_email(pasta_de_emails)

    assert visitante.get(f"/confirmar/{token}").status_code == 200

    # Envelhece tudo o que restar, e varre.
    _vencer_tokens(aplicacao, minutos=60 * 24 * 30)

    with aplicacao.app_context():
        assert cadastro.descartar_vencidos() == 0

        conta = usuario_repo.buscar_por_login(
            usuario_repo.login_de_email(NOVO["email"])
        )
    assert conta is not None
    assert conta["ativo"]


def test_hospede_com_reserva_nunca_e_descartado(aplicacao):
    """A condição mais importante da seleção.

    Uma conta inativa pode pertencer a um hóspede com histórico — por
    exemplo alguém que a recepção desativou. Apagá-la arrastaria o
    registro de hóspede, e com ele a reserva.

    A chave estrangeira `ON DELETE RESTRICT` é a segunda rede: ainda que a
    seleção errasse, a remoção falharia em vez de apagar histórico.
    """
    with aplicacao.app_context():
        hospede_id, usuario_id = _criar_hospede_com_conta(
            False, "com-reserva", "com.reserva@exemplo.com", DOCUMENTO
        )
        token_repo.gerar(usuario_id, token_repo.CONFIRMACAO)

        reserva_repo.criar({
            "chale_id": 1,
            "hospede_id": hospede_id,
            "data_entrada": dt.date(2027, 6, 1),
            "data_saida": dt.date(2027, 6, 5),
            "num_pessoas": 2,
        })

    _vencer_tokens(aplicacao)

    with aplicacao.app_context():
        assert cadastro.descartar_vencidos() == 0
        assert hospede_repo.buscar_por_id(hospede_id) is not None
        assert usuario_repo.buscar_por_id(usuario_id) is not None


def test_conta_de_equipe_nunca_e_descartada(aplicacao):
    """Perfil que não é hóspede não tem prazo de confirmação correndo."""
    with aplicacao.app_context():
        usuario_id = usuario_repo.criar(
            login="recepcionista-novo",
            nome="Quem Chegou",
            senha_hash=senhas.gerar_hash("senha-longa-o-bastante"),
            perfil="recepcionista",
            ativo=False,
        )
        token_repo.gerar(usuario_id, token_repo.CONFIRMACAO)

    _vencer_tokens(aplicacao)

    with aplicacao.app_context():
        assert cadastro.descartar_vencidos() == 0
        assert usuario_repo.buscar_por_id(usuario_id) is not None


def test_conta_inativa_sem_token_nunca_e_descartada(aplicacao):
    """A conta que o administrador cria e ativa à mão.

    É a saída prevista pela Forma B: se o e-mail não chegar, a recepção
    fala com a pessoa e ativa a conta. Essa conta nasce inativa e **sem
    token**, porque não há confirmação a fazer — e não pode ser confundida
    com um cadastro abandonado.

    Sem a condição `EXISTS (token)` na seleção, ela seria descartada na
    primeira varredura.
    """
    with aplicacao.app_context():
        hospede_id, usuario_id = _criar_hospede_com_conta(
            False, "sem-token", "sem.token@exemplo.com", DOCUMENTO
        )

        assert cadastro.descartar_vencidos() == 0
        assert usuario_repo.buscar_por_id(usuario_id) is not None
        assert hospede_repo.buscar_por_id(hospede_id) is not None


def test_hospede_com_duas_contas_nao_perde_a_conta_boa(aplicacao):
    """A condição de contagem, e o estrago que ela evita.

    Um hóspede pode ter duas contas — foi o caminho do ataque que
    `existe_para_hospede` fechou. Se uma delas estiver inativa com token
    vencido, descartá-la apagaria o registro de hóspede, e com ele a conta
    **ativa** da pessoa.

    Aqui o `ON DELETE RESTRICT` não salvaria o suficiente: a conta inativa
    já teria sido apagada quando a remoção do hóspede falhasse, deixando o
    banco num estado meio desfeito.
    """
    with aplicacao.app_context():
        hospede_id, conta_boa = _criar_hospede_com_conta(
            True, "conta-boa", "pessoa@exemplo.com", DOCUMENTO
        )
        conta_abandonada = usuario_repo.criar(
            login="conta-abandonada",
            nome="Pessoa Cadastrada",
            senha_hash=senhas.gerar_hash("senha-longa-o-bastante"),
            perfil="hospede",
            hospede_id=hospede_id,
            ativo=False,
        )
        token_repo.gerar(conta_abandonada, token_repo.CONFIRMACAO)

    _vencer_tokens(aplicacao)

    with aplicacao.app_context():
        assert cadastro.descartar_vencidos() == 0
        assert usuario_repo.buscar_por_id(conta_boa) is not None
        assert usuario_repo.buscar_por_id(conta_abandonada) is not None
        assert hospede_repo.buscar_por_id(hospede_id) is not None


def test_o_link_vencido_nao_ativa_depois_do_descarte(
    aplicacao, visitante, pasta_de_emails
):
    """O percurso de quem clica tarde.

    Recebe a mesma tela de falha de sempre — a resposta não distingue
    token descartado de token vencido ou inexistente, que é o requisito do
    Bloco 2.
    """
    visitante.post("/cadastro", data=NOVO)
    token = _token_do_email(pasta_de_emails)

    _vencer_tokens(aplicacao)
    with aplicacao.app_context():
        cadastro.descartar_vencidos()

    resposta = visitante.get(f"/confirmar/{token}")
    inexistente = visitante.get(f"/confirmar/{'z' * 43}")

    assert resposta.status_code == 400
    assert resposta.data == inexistente.data, (
        "a resposta a um link descartado precisa ser idêntica à de um link "
        "que nunca existiu"
    )


# ---------------------------------------------------------------------------
# A varredura no gancho de requisição
# ---------------------------------------------------------------------------

def test_qualquer_requisicao_dispara_a_varredura(
    aplicacao, visitante, pasta_de_emails
):
    """Sem agendador, e sem depender de alguém se cadastrar.

    Com a janela em 5 minutos, "descartado em 5 minutos" precisa ser
    verdade. Uma varredura que só rodasse na tentativa de cadastro
    deixaria os dados parados pelo tempo que ninguém se cadastrasse —
    horas, numa noite sem acesso.

    A requisição usada aqui é a **vitrine pública**, que é o acesso mais
    comum e o que menos tem a ver com cadastro. É justamente o ponto: a
    varredura não depende de quem acessa nem de para quê.
    """
    visitante.post("/cadastro", data=NOVO)
    _vencer_tokens(aplicacao)

    with aplicacao.app_context():
        conta = usuario_repo.buscar_por_login(
            usuario_repo.login_de_email(NOVO["email"])
        )
    assert conta is not None, "a conta precisa existir antes da varredura"

    # A trava é zerada de propósito.
    #
    # O POST de cadastro, acima, já disparou uma varredura e consumiu o
    # minuto. Sem zerar, o GET seguinte seria barrado pela trava, e o
    # teste acusaria a varredura como quebrada quando o que estaria
    # funcionando era a trava. Foi o que aconteceu na primeira versão
    # deste teste.
    #
    # A trava tem teste próprio, logo abaixo.
    aplicacao.varredura_de_cadastros["ultima"] = 0.0

    # Nenhuma chamada explícita a `descartar_vencidos`: só um GET.
    aplicacao.test_client().get("/")

    with aplicacao.app_context():
        depois = usuario_repo.buscar_por_login(
            usuario_repo.login_de_email(NOVO["email"])
        )
    assert depois is None, (
        "o gancho de requisição não descartou o cadastro vencido"
    )


def test_a_trava_evita_varrer_em_toda_requisicao(aplicacao, visitante):
    """A trava, verificada pelo comportamento e não pela fonte.

    Sem ela, o gancho faria uma consulta de seleção a cada imagem, folha
    de estilo e página servida — e com a janela em minutos, isso é uma
    consulta por recurso de cada página aberta.

    O teste é indireto porque a trava é, por natureza, a **ausência** de
    uma ação: cadastra, vence, varre uma vez, e então cadastra e vence de
    novo. O segundo cadastro vencido tem de **sobreviver** ao GET
    seguinte, porque o minuto ainda não passou.
    """
    visitante.post("/cadastro", data=NOVO)
    _vencer_tokens(aplicacao)

    aplicacao.varredura_de_cadastros["ultima"] = 0.0
    aplicacao.test_client().get("/")   # varre, e trava

    # Um segundo cadastro, também vencido.
    visitante.post("/cadastro", data={
        **NOVO,
        "email": "segunda.pessoa@exemplo.com",
        "documento": OUTRO_DOCUMENTO,
    })
    _vencer_tokens(aplicacao)

    with aplicacao.app_context():
        segunda = usuario_repo.buscar_por_login(
            usuario_repo.login_de_email("segunda.pessoa@exemplo.com")
        )
    assert segunda is not None

    # Mais requisições, sem zerar a trava.
    for _ in range(3):
        aplicacao.test_client().get("/")

    with aplicacao.app_context():
        depois = usuario_repo.buscar_por_login(
            usuario_repo.login_de_email("segunda.pessoa@exemplo.com")
        )

    assert depois is not None, (
        "a trava não funcionou: a varredura rodou de novo dentro do minuto"
    )


def test_a_trava_usa_relogio_monotonico():
    """Por que não `time.time()`.

    O relógio de parede pode andar para trás — ajuste de horário de
    verão, sincronização com servidor de tempo, correção manual. Se ele
    recuasse, `agora - ultima` ficaria negativo e menor que o intervalo, e
    a varredura **pararia** até o relógio alcançar o valor antigo. Com
    uma correção de uma hora, pararia por uma hora.

    Verificado na fonte porque provocar um recuo de relógio num teste
    exigiria mexer no relógio da máquina.
    """
    fonte = (RAIZ / "app" / "__init__.py").read_text(encoding="utf-8")

    assert "monotonic" in fonte
    assert "time.time()" not in fonte


def test_a_falha_da_varredura_nao_derruba_a_resposta(aplicacao, monkeypatch):
    """Varredura é manutenção; a resposta é o que a pessoa pediu.

    Se o banco estiver indisponível no momento da varredura, a vitrine
    ainda precisa abrir.
    """
    def explodir():
        raise RuntimeError("banco fora do ar")

    monkeypatch.setattr(cadastro, "descartar_vencidos", explodir)

    resposta = aplicacao.test_client().get("/")

    assert resposta.status_code == 200


def test_a_face_publica_nao_ganhou_alcance_novo():
    """A varredura não afrouxou a regra de isolamento.

    O gancho vive na fábrica da aplicação, que alcança tudo por
    construção, e chama o módulo de serviço. `app/rotas/publico.py`
    continua sem importar repositório.

    Era a alternativa tentadora e errada: chamar a varredura dentro de uma
    rota pública teria sido uma linha, e teria dado à face pública escrita
    em `usuario` e `hospede` por um caminho que nenhuma porta declara.
    """
    import ast

    arvore = ast.parse(
        (RAIZ / "app" / "rotas" / "publico.py").read_text(encoding="utf-8")
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

    proibidos = {"hospede_repo", "usuario_repo", "reserva_repo", "auditoria_repo"}
    achados = {n for n in importados if n.split(".")[-1] in proibidos}

    assert not achados, f"a face pública passou a importar: {sorted(achados)}"


# ---------------------------------------------------------------------------
# A configuração
# ---------------------------------------------------------------------------

def test_a_janela_e_configuravel():
    """Trocar 5 por 15 é uma linha no .env, e nenhuma no código.

    Importa para a demonstração: 5 minutos é apertado para a entrega de
    e-mail, e se na hora da banca a mensagem demorar, o cadastro expira
    enquanto o professor assiste.
    """
    import os

    anterior = os.environ.get("VILLAJU_CADASTRO_MINUTOS")
    try:
        os.environ["VILLAJU_CADASTRO_MINUTOS"] = "15"
        assert Configuracao(modo_teste=True).CADASTRO_MINUTOS == 15
    finally:
        if anterior is None:
            os.environ.pop("VILLAJU_CADASTRO_MINUTOS", None)
        else:
            os.environ["VILLAJU_CADASTRO_MINUTOS"] = anterior


def test_a_janela_aparece_no_resumo_seguro():
    """Faz parte do que se quer ver em log e na documentação técnica."""
    resumo = Configuracao(modo_teste=True).resumo_seguro()

    assert "cadastro_minutos" in resumo


# ---------------------------------------------------------------------------
# As condições do SQL, uma por uma
#
# Três sabotagens do Bloco 4 escaparam, e as três apontavam para o mesmo
# ponto cego: **as condições de proteção estavam duplicadas, e cada metade
# cobria a ausência da outra.** Um teste que só observa o resultado final
# passa com qualquer uma das duas no lugar.
#
# A resposta não foi remover a duplicação — ela é desejável, e as duas
# metades protegem coisas diferentes. Foi descer um nível e testar cada
# comando SQL diretamente, que é onde a proteção mora.
# ---------------------------------------------------------------------------

def test_a_selecao_nao_considera_conta_ativa(aplicacao):
    """A condição `ativo = FALSE` da SELEÇÃO, isolada da do DELETE.

    Com ela removida, nada de errado acontece — o `DELETE` recusa a conta
    ativa e o laço a ignora. **Por isso a sabotagem escapou.**

    Então por que ela importa? Porque o ramo que ignora a conta é o
    tratamento da **corrida**: ele deve disparar na situação rara de
    alguém confirmar no último segundo, e nunca na operação normal. Se
    disparasse para toda conta ativa em toda varredura, o log encheria de
    falso alarme e uma corrida de verdade ficaria invisível no meio dele.

    O teste executa a consulta de seleção diretamente e exige que a conta
    ativa não esteja entre as candidatas.
    """
    with aplicacao.app_context():
        hospede_id, conta_ativa = _criar_hospede_com_conta(
            True, "conta-ativa", "ativa@exemplo.com", DOCUMENTO
        )
        token_repo.gerar(conta_ativa, token_repo.CONFIRMACAO)

    _vencer_tokens(aplicacao)

    with aplicacao.app_context():
        with conexao.obter_cursor(dicionario=True) as cursor:
            cursor.execute(usuario_repo._SQL_CANDIDATAS_AO_DESCARTE)
            candidatas = {linha["usuario_id"] for linha in cursor.fetchall()}

    assert conta_ativa not in candidatas, (
        "a seleção considerou uma conta ativa; o DELETE a recusaria, mas o "
        "log ganharia um falso alarme de corrida em toda varredura"
    )


def test_o_delete_recusa_conta_ativa(aplicacao):
    """A condição `ativo = FALSE` do DELETE, isolada da da seleção.

    Esta é a guarda de corrida, e a corrida é real: entre a seleção das
    candidatas e a remoção, a pessoa pode clicar no link de confirmação.
    Sem a condição, a conta de quem **acabou de confirmar** seria apagada
    — e com ela o registro de hóspede.

    Testado executando o comando contra uma conta ativa e exigindo zero
    linhas afetadas. É o único jeito de verificar esta metade sem
    orquestrar duas requisições simultâneas.
    """
    with aplicacao.app_context():
        _, conta_ativa = _criar_hospede_com_conta(
            True, "recem-confirmada", "recem@exemplo.com", DOCUMENTO
        )

        with conexao.obter_conexao() as ligacao:
            cursor = ligacao.cursor()
            try:
                cursor.execute(usuario_repo._SQL_APAGAR_CONTA, (conta_ativa,))
                afetadas = cursor.rowcount
            finally:
                cursor.close()

        assert afetadas == 0, (
            "o DELETE apagou uma conta ativa: quem confirmasse o cadastro "
            "no último segundo perderia a conta recém-ativada"
        )
        assert usuario_repo.buscar_por_id(conta_ativa) is not None


def test_a_trigger_garante_que_so_hospede_tem_hospede_id():
    """A invariante que torna `perfil = 'hospede'` redundante na seleção.

    A sabotagem que removeu essa condição não quebrou teste nenhum, e a
    razão não é descuido: `trg_usuario_antes_inserir` recusa
    `perfil <> 'hospede'` com `hospede_id` preenchido. Como a seleção já
    exige `hospede_id IS NOT NULL`, o banco garante o resto.

    Montar um teste que provasse a condição necessária seria montar um
    teste impossível — a linha que ele precisaria criar o banco não
    aceita.

    O que se pode fazer, e é o que este teste faz, é **prender a
    invariante**. Se a regra sair da trigger, a condição em Python deixa
    de ser enfeite e passa a ser a única proteção — e é melhor descobrir
    isso por um teste vermelho do que por uma conta de administrador
    apagada.
    """
    sql = (RAIZ / "banco" / "02_triggers.sql").read_text(encoding="utf-8")

    assert "NEW.perfil <> 'hospede' AND NEW.hospede_id IS NOT NULL" in sql, (
        "a trigger deixou de garantir que só perfil hospede tem hospede_id; "
        "a condição `perfil = 'hospede'` em _SQL_CANDIDATAS_AO_DESCARTE "
        "passou a ser carga, e precisa de teste próprio"
    )


def test_a_selecao_e_o_delete_tem_a_condicao_cada_um(aplicacao):
    """As duas metades existem, e o teste diz por que cada uma.

    Verificado no texto dos comandos porque é o que as sabotagens 1 e 7
    mostraram solto: remover qualquer uma das duas não mudava resultado
    nenhum, e nenhum teste de comportamento as distinguia.
    """
    assert "u.ativo = FALSE" in usuario_repo._SQL_CANDIDATAS_AO_DESCARTE, (
        "a seleção precisa filtrar conta ativa, para que o ramo de corrida "
        "não dispare na operação normal"
    )
    assert "ativo = FALSE" in usuario_repo._SQL_APAGAR_CONTA, (
        "o DELETE precisa da condição, para que quem confirme entre a "
        "seleção e a remoção não perca a conta"
    )


def test_a_segunda_tentativa_funciona_na_propria_tentativa(
    aplicacao, visitante, pasta_de_emails
):
    """A varredura roda ANTES das consultas de `registrar`, e a ordem e o ponto.

    A sabotagem que tirou a varredura de `registrar` nao quebrou teste
    nenhum: o gancho de requisicao varre de todo modo, e o resultado final
    era o mesmo. **O que mudava era a ordem**, e a ordem e a razao de ser
    do descarte.

    O percurso real e este. A pessoa erra uma letra no endereco. O prazo
    vence. Ela volta e se cadastra de novo, com o endereco certo e o mesmo
    documento. Se a varredura rodasse depois das consultas, essa tentativa
    seria recusada pelo cadastro vencido dela mesma — e recusada **em
    silencio**, pela regra de resposta uniforme. Ela veria a pagina de
    sempre, nunca receberia o e-mail, e nao teria como descobrir o motivo.
    So a TERCEIRA tentativa funcionaria.

    O teste distingue os dois casos pelo e-mail: convite tem link de
    confirmacao, aviso de endereco ja cadastrado nao tem.

    A trava do gancho ajuda a isolar: o primeiro POST a consome, e o
    segundo depende apenas da varredura de `registrar`.
    """
    visitante.post("/cadastro", data=NOVO)
    _vencer_tokens(aplicacao)

    antes = len(_enviadas(pasta_de_emails))

    resposta = visitante.post("/cadastro", data=NOVO)
    assert resposta.status_code == 302

    enviadas = _enviadas(pasta_de_emails)
    assert len(enviadas) == antes + 1, "a segunda tentativa nao gerou e-mail"

    ultima = enviadas[0]
    assert "/confirmar/" in ultima["corpo"], (
        "a segunda tentativa recebeu AVISO e nao CONVITE: a varredura rodou "
        "depois das consultas, e o cadastro vencido da propria pessoa a "
        "bloqueou em silencio"
    )

    with aplicacao.app_context():
        conta = usuario_repo.buscar_por_login(
            usuario_repo.login_de_email(NOVO["email"])
        )
    assert conta is not None
    assert not conta["ativo"]


def test_a_varredura_de_registrar_vem_antes_das_consultas():
    """A ordem verificada na fonte, porque e ordem e nao presenca.

    Um teste de comportamento consegue distinguir os dois casos — e o
    anterior distingue —, mas so quando a trava do gancho esta no lugar
    certo. Esta verificacao e direta e nao depende de temporizacao.
    """
    fonte = (
        RAIZ / "app" / "servicos" / "cadastro.py"
    ).read_text(encoding="utf-8")

    corpo = fonte[fonte.index("def registrar("):]

    posicao_varredura = corpo.index("descartar_vencidos()")
    posicao_consulta = corpo.index("buscar_por_login")

    assert posicao_varredura < posicao_consulta, (
        "a varredura precisa rodar antes de consultar se o endereco ja tem "
        "conta, senao o cadastro vencido da propria pessoa a bloqueia"
    )
