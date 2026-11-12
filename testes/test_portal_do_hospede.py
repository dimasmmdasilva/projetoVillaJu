"""Portal do hóspede: perfil, troca de senha e reservas separadas.

Etapa 5, Bloco 6. A conta criada no Bloco 3 ganha onde ser usada.

**Os hóspedes destes testes são criados por `hospede_repo.criar`**, e não
vêm da massa fictícia. A massa traz os campos pessoais cifrados com a
chave de quem a gerou, e numa máquina com outra chave eles não decifram —
é o que `scripts/regerar_dados_pessoais.py` existe para resolver. Um
teste de perfil precisa de um registro que a máquina consiga abrir.

Essa limitação também é verificada aqui, do outro lado: um cadastro que
não decifra produz aviso e não página em branco.
"""

import datetime as dt
import pathlib
import re

import pytest

from app import create_app
from app.config import Configuracao
from app.dados import (
    auditoria_repo,
    conexao,
    hospede_repo,
    reserva_repo,
    usuario_repo,
)
from app.rotas.reservas import SITUACOES_VIVAS, _separar_reservas
from app.seguranca import senhas

SENHA = "jureia-praia-2026"
ADMIN = ("admin", "Villa@Admin2026")
RECEPCAO = ("recepcao", "Villa@Recep2026")

DADOS = {
    "nome": "Mariana Prado",
    "documento": "529.982.247-25",
    "email": "mariana.prado@exemplo.com",
    "telefone": "(12) 99888-7766",
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

    configuracao = Configuracao(modo_teste=True)
    conexao.iniciar_pool(configuracao)
    app = create_app(configuracao)
    app.config["SESSION_COOKIE_SECURE"] = False
    app.villaju = configuracao
    yield app
    conexao.encerrar_pool()


@pytest.fixture
def conta(aplicacao):
    """Hóspede com conta, criados com a chave desta máquina."""
    with aplicacao.app_context():
        hospede_id = hospede_repo.criar(dict(DADOS))
        usuario_id = usuario_repo.criar(
            login=usuario_repo.login_de_email(DADOS["email"]),
            nome=DADOS["nome"],
            senha_hash=senhas.gerar_hash(SENHA),
            perfil="hospede",
            hospede_id=hospede_id,
            ativo=True,
        )
    return {"hospede_id": hospede_id, "usuario_id": usuario_id}


def _entrar(aplicacao, login, senha):
    cliente = aplicacao.test_client()
    pagina = cliente.get("/login").data.decode()
    dados = {"login": login, "senha": senha}
    achado = re.search(r'name="csrf_token" value="([^"]+)"', pagina)
    if achado:
        dados["csrf_token"] = achado.group(1)
    cliente.post("/login", data=dados)
    return cliente


def _token(cliente, rota):
    pagina = cliente.get(rota).data.decode()
    achado = re.search(r'name="csrf_token" value="([^"]+)"', pagina)
    return achado.group(1) if achado else ""


@pytest.fixture
def hospede(aplicacao, conta):
    return _entrar(aplicacao, DADOS["email"], SENHA)


# ---------------------------------------------------------------------------
# O perfil
# ---------------------------------------------------------------------------

def test_o_hospede_ve_os_proprios_dados_decifrados(hospede):
    html = hospede.get("/reservas/perfil").data.decode()

    assert DADOS["nome"] in html
    assert DADOS["documento"] in html
    assert DADOS["email"] in html
    assert DADOS["telefone"] in html


def test_o_perfil_nao_recebe_identificador_no_caminho(aplicacao, hospede):
    """A rota é `/reservas/perfil`, e não `/reservas/perfil/<id>`.

    **Isto sozinho não basta**, e a sabotagem provou: um endereço sem
    parâmetro de caminho continua podendo ler um parâmetro de CONSULTA.
    O par deste teste é
    `test_o_perfil_ignora_identificador_no_parametro_de_consulta`.
    """
    regras = {
        str(regra) for regra in aplicacao.url_map.iter_rules()
        if "perfil" in str(regra)
    }

    for regra in regras:
        assert "<" not in regra, (
            f"a rota de perfil aceita parâmetro no endereço: {regra}"
        )


def test_o_perfil_ignora_identificador_no_parametro_de_consulta(
    aplicacao, conta
):
    """O furo de autorização mais comum que existe em sistema web.

    A sabotagem que fez o perfil ler `?hospede=N` **não quebrou teste
    nenhum**: as verificações olhavam o mapa de rotas, que não tem
    parâmetro, e comparavam duas contas, que de fato veem dados
    distintos. Nenhuma tentou trocar o número no endereço.

    O identificador vem da linha do usuário no banco, lida a cada
    requisição. Tudo o que chega pela requisição — caminho, consulta,
    formulário, cabeçalho — é de quem está pedindo, e quem está pedindo
    não decide de quem é o cadastro que vai ver.
    """
    with aplicacao.app_context():
        alheio = hospede_repo.criar({
            "nome": "Pessoa Alheia",
            "documento": "111.444.777-35",
            "email": "pessoa.alheia@exemplo.com",
            "telefone": "(11) 90000-0000",
        })

    cliente = _entrar(aplicacao, DADOS["email"], SENHA)

    for parametro in ("hospede", "hospede_id", "id"):
        html = cliente.get(
            f"/reservas/perfil?{parametro}={alheio}"
        ).data.decode()

        assert "111.444.777-35" not in html, (
            f"o perfil obedeceu a `?{parametro}=` e mostrou o cadastro de "
            f"outra pessoa"
        )
        assert "Pessoa Alheia" not in html
        assert DADOS["documento"] in html, (
            "o perfil precisa continuar mostrando o cadastro de quem está "
            "conectado"
        )


def test_o_perfil_ignora_identificador_no_formulario(aplicacao, conta):
    """O mesmo, pelo outro caminho que a requisição oferece.

    Um GET com corpo é incomum, mas `request.values` o alcança — e
    trocar `request.args` por `request.values` numa alteração futura
    reabriria o furo por um caminho que o teste anterior não cobre.
    """
    with aplicacao.app_context():
        alheio = hospede_repo.criar({
            "nome": "Pessoa Alheia",
            "documento": "111.444.777-35",
            "email": "pessoa.alheia@exemplo.com",
        })

    cliente = _entrar(aplicacao, DADOS["email"], SENHA)

    html = cliente.get(
        "/reservas/perfil", data={"hospede": alheio}
    ).data.decode()

    assert "111.444.777-35" not in html
    assert DADOS["documento"] in html


def test_o_perfil_de_outra_pessoa_nao_e_alcancavel(aplicacao, conta):
    """Duas contas, e cada uma vê a sua.

    O contraste que dá sentido ao teste anterior: se o perfil mostrasse
    sempre o mesmo registro, a ausência de parâmetro na URL não provaria
    nada.
    """
    with aplicacao.app_context():
        outro_hospede = hospede_repo.criar({
            "nome": "Outra Pessoa",
            "documento": "111.444.777-35",
            "email": "outra.pessoa@exemplo.com",
            "telefone": "(11) 90000-0000",
        })
        usuario_repo.criar(
            login=usuario_repo.login_de_email("outra.pessoa@exemplo.com"),
            nome="Outra Pessoa",
            senha_hash=senhas.gerar_hash(SENHA),
            perfil="hospede",
            hospede_id=outro_hospede,
            ativo=True,
        )

    primeiro = _entrar(aplicacao, DADOS["email"], SENHA)
    segundo = _entrar(aplicacao, "outra.pessoa@exemplo.com", SENHA)

    html_um = primeiro.get("/reservas/perfil").data.decode()
    html_dois = segundo.get("/reservas/perfil").data.decode()

    assert DADOS["documento"] in html_um
    assert "111.444.777-35" not in html_um

    assert "111.444.777-35" in html_dois
    assert DADOS["documento"] not in html_dois


def test_o_perfil_e_somente_leitura(hospede):
    """Documento e e-mail não se editam aqui, e não por esquecimento.

    O **documento** é identidade: deixar trocá-lo sozinho permitiria a
    alguém com a sessão tomada assumir outra identidade no sistema.

    O **e-mail é o login** — o login desta conta é o HMAC do endereço.
    Trocá-lo aqui mudaria a chave de acesso sem prova de posse do
    endereço novo.
    """
    html = hospede.get("/reservas/perfil").data.decode()

    assert '<input type="text" id="documento"' not in html
    assert 'name="documento"' not in html
    assert 'name="email"' not in html


def test_o_perfil_explica_que_o_dado_e_cifrado(hospede):
    html = hospede.get("/reservas/perfil").data.decode()
    assert "cifrados no banco" in html


def test_cadastro_que_nao_decifra_vira_aviso_e_nao_pagina_em_branco(
    aplicacao
):
    """A massa fictícia traz os campos cifrados com a chave de quem a
    gerou, e noutra máquina eles não decifram.

    Para a pessoa dona do registro, mostrar dado alterado seria pior do
    que não mostrar nada — e uma página em branco, sem explicação, seria
    pior ainda.
    """
    cliente = _entrar(aplicacao, "joao", "Villa@Hospede2026")
    resposta = cliente.get("/reservas/perfil")

    assert resposta.status_code == 200

    html = resposta.data.decode()
    assert "Procure a recepção" in html or "Mariana" not in html


# ---------------------------------------------------------------------------
# A separação das reservas
# ---------------------------------------------------------------------------

def _reservar(aplicacao, hospede_id, chale_id, entrada, saida, situacao):
    with aplicacao.app_context():
        identificador = reserva_repo.criar({
            "chale_id": chale_id,
            "hospede_id": hospede_id,
            "data_entrada": entrada,
            "data_saida": saida,
            "num_pessoas": 2,
        })
        # O caminho até a situação pedida, e não um salto.
        #
        # `mudar_situacao` aplica a máquina de estados: uma reserva
        # pendente só passa para confirmada ou cancelada, e `concluida`
        # vem depois de confirmada. Saltar seria contornar a regra que o
        # sistema inteiro se apoia — e o teste passaria a montar um
        # estado que a aplicação não produz.
        caminho = {
            "pendente": [],
            "confirmada": ["confirmada"],
            "cancelada": ["cancelada"],
            "concluida": ["confirmada", "concluida"],
        }[situacao]

        for passo in caminho:
            reserva_repo.mudar_situacao(identificador, passo)
    return identificador


def test_a_separacao_exige_situacao_viva_E_saida_futura(aplicacao, conta):
    """As duas condições, e nenhuma delas basta sozinha.

    **Só a situação não bastaria:** uma reserva confirmada cuja saída foi
    mês passado está confirmada e acabada, e ficaria no topo empurrando
    para baixo a que começa amanhã.

    **Só a data também não:** uma reserva cancelada para a semana que vem
    não está em andamento coisa nenhuma.
    """
    hoje = dt.date.today()
    hospede_id = conta["hospede_id"]

    # Datas bem longe das da massa fictícia, que reserva em dezembro de
    # 2026 e fevereiro de 2027. A primeira versão deste teste usava
    # `hoje + 30` e colidia com elas — e a trigger de não-sobreposição,
    # corretamente, recusava.
    futura_viva = _reservar(
        aplicacao, hospede_id, 1,
        hoje + dt.timedelta(days=300), hoje + dt.timedelta(days=304),
        "confirmada",
    )
    futura_cancelada = _reservar(
        aplicacao, hospede_id, 2,
        hoje + dt.timedelta(days=310), hoje + dt.timedelta(days=314),
        "cancelada",
    )
    passada_confirmada = _reservar(
        aplicacao, hospede_id, 3,
        hoje - dt.timedelta(days=400), hoje - dt.timedelta(days=396),
        "confirmada",
    )

    with aplicacao.app_context():
        lista = reserva_repo.listar_por_hospede(hospede_id)

    em_andamento, historico = _separar_reservas(lista)

    andamento = {r["id"] for r in em_andamento}
    arquivo = {r["id"] for r in historico}

    assert futura_viva in andamento
    assert futura_cancelada in arquivo, (
        "cancelada no futuro não está em andamento"
    )
    assert passada_confirmada in arquivo, (
        "confirmada e já terminada não está em andamento"
    )


def test_a_reserva_que_termina_hoje_ainda_esta_em_andamento(
    aplicacao, conta
):
    """A borda do dia.

    Quem sai hoje ainda é hóspede hoje. `>=` e não `>`, pela mesma lógica
    do intervalo semiaberto: a saída é o último dia da estadia.
    """
    hoje = dt.date.today()
    identificador = _reservar(
        aplicacao, conta["hospede_id"], 5,
        hoje - dt.timedelta(days=3), hoje, "confirmada",
    )

    with aplicacao.app_context():
        lista = reserva_repo.listar_por_hospede(conta["hospede_id"])

    em_andamento, _ = _separar_reservas(lista)

    assert identificador in {r["id"] for r in em_andamento}


def test_as_situacoes_vivas_sao_as_mesmas_que_ocupam_o_chale():
    """Não é coincidência: uma reserva que ocupa o chalé está em andamento.

    Se as duas listas divergissem, uma reserva poderia bloquear o chalé e
    aparecer no histórico da pessoa — ou o contrário.
    """
    from app.dados import vitrine

    assert set(SITUACOES_VIVAS) == set(vitrine.SITUACOES_QUE_OCUPAM)


def test_a_tela_separa_as_duas_listas(aplicacao, conta, hospede):
    hoje = dt.date.today()
    _reservar(
        aplicacao, conta["hospede_id"], 1,
        hoje + dt.timedelta(days=300), hoje + dt.timedelta(days=304),
        "confirmada",
    )
    _reservar(
        aplicacao, conta["hospede_id"], 2,
        hoje - dt.timedelta(days=400), hoje - dt.timedelta(days=396),
        "concluida",
    )

    html = hospede.get("/reservas/minhas").data.decode()

    assert "Em andamento" in html
    assert "Histórico" in html
    assert html.index("Em andamento") < html.index("Histórico"), (
        "o que está por vir vem antes do que já passou"
    )


def test_cada_lista_mostra_apenas_o_que_e_dela(aplicacao, conta, hospede):
    """A separação verificada no CONTEÚDO, e não nos títulos.

    A sabotagem que fez a seção "Em andamento" desenhar
    `em_andamento + historico` **não quebrou teste nenhum**: os dois
    títulos continuavam lá, e na ordem certa. O que mudou foi o que
    aparece debaixo de cada um.

    É a mesma família de sempre — verificar no lugar errado. Título é
    rótulo; o que importa é a linha da tabela.
    """
    hoje = dt.date.today()

    viva = _reservar(
        aplicacao, conta["hospede_id"], 1,
        hoje + dt.timedelta(days=300), hoje + dt.timedelta(days=304),
        "confirmada",
    )
    acabada = _reservar(
        aplicacao, conta["hospede_id"], 2,
        hoje - dt.timedelta(days=400), hoje - dt.timedelta(days=396),
        "concluida",
    )

    html = hospede.get("/reservas/minhas").data.decode()

    corte = html.index("Histórico")
    secao_andamento = html[html.index("Em andamento"):corte]
    secao_historico = html[corte:]

    assert f">{viva}</td>" in secao_andamento
    assert f">{acabada}</td>" not in secao_andamento, (
        "uma reserva terminada apareceu em Em andamento"
    )

    assert f">{acabada}</td>" in secao_historico
    assert f">{viva}</td>" not in secao_historico, (
        "uma reserva em andamento apareceu no Histórico"
    )


def test_a_tela_mostra_que_nao_ha_reserva_em_cada_lista(hospede):
    """Lista vazia diz que está vazia, em vez de simplesmente sumir."""
    html = hospede.get("/reservas/minhas").data.decode()

    assert "Nenhuma reserva em andamento" in html
    assert "Nenhuma reserva anterior" in html


def test_o_hospede_continua_vendo_apenas_as_proprias_reservas(
    aplicacao, conta, hospede
):
    """O caso de teste obrigatório do plano, que o Bloco 6 não afrouxou."""
    hoje = dt.date.today()

    # Uma reserva do hóspede 1 da massa, que não é esta pessoa.
    alheia = _reservar(
        aplicacao, 1, 4,
        hoje + dt.timedelta(days=320), hoje + dt.timedelta(days=324),
        "confirmada",
    )

    html = hospede.get("/reservas/minhas").data.decode()

    assert f">{alheia}</td>" not in html


# ---------------------------------------------------------------------------
# A troca de senha
# ---------------------------------------------------------------------------

def test_a_senha_atual_e_exigida(aplicacao, conta, hospede):
    """Sem ela, uma sessão tomada vira acesso permanente.

    Quem encontrasse um navegador aberto na recepção trocaria a senha e
    expulsaria a dona da conta. A senha atual é a prova de que quem pede
    a troca é quem **sabe** a senha, e não apenas quem está com a sessão.
    """
    resposta = hospede.post("/reservas/perfil/senha", data={
        "senha_atual": "senha-errada-de-proposito",
        "senha_nova": "senha-nova-bem-longa",
        "senha_confirmacao": "senha-nova-bem-longa",
        "csrf_token": _token(hospede, "/reservas/perfil/senha"),
    })

    assert resposta.status_code == 400
    assert "aviso-erro" in resposta.data.decode()

    with aplicacao.app_context():
        atual = usuario_repo.buscar_por_id(conta["usuario_id"])

    assert senhas.verificar(atual["senha_hash"], SENHA), (
        "a senha mudou sem que a atual fosse conferida"
    )


def test_a_senha_atual_e_conferida_ANTES_da_nova(aplicacao, hospede):
    """A ordem importa, e é informação de graça o que ela evita.

    Validar o formato da nova antes de conferir a atual diria a quem
    **não sabe** a senha se a nova proposta seria aceita — e, repetido,
    permitiria mapear a política de senha do sistema sem ter acesso
    nenhum.
    """
    resposta = hospede.post("/reservas/perfil/senha", data={
        "senha_atual": "senha-errada-de-proposito",
        "senha_nova": "x",                       # curta demais
        "senha_confirmacao": "y",                # e diferente
        "csrf_token": _token(hospede, "/reservas/perfil/senha"),
    })

    # A asserção é sobre a MENSAGEM, e não sobre a página.
    #
    # A primeira versão procurava "caracteres" no HTML inteiro e falhava
    # sempre: o próprio formulário traz a nota "Ao menos 10 caracteres"
    # embaixo do campo, que é explicação e não recusa. Um teste que lê a
    # página toda não distingue as duas.
    avisos = re.findall(
        r'class="aviso aviso-erro"[^>]*>(.*?)</p>',
        resposta.data.decode(),
        re.S,
    )
    assert avisos, "a recusa precisa aparecer como aviso"

    mensagem = " ".join(avisos)

    assert "senha atual não confere" in mensagem
    assert "caracteres" not in mensagem, (
        f"a recusa falou do formato da senha nova antes de conferir a "
        f"atual: {mensagem.strip()!r}"
    )


@pytest.mark.parametrize(
    "rotulo,nova,confirmacao",
    [
        ("curta", "curta1", "curta1"),
        ("confirmação diferente", "senha-nova-longa", "outra-senha-longa"),
        ("igual à atual", SENHA, SENHA),
        ("vazia", "", ""),
    ],
)
def test_senha_nova_invalida_e_recusada(
    aplicacao, conta, hospede, rotulo, nova, confirmacao
):
    resposta = hospede.post("/reservas/perfil/senha", data={
        "senha_atual": SENHA,
        "senha_nova": nova,
        "senha_confirmacao": confirmacao,
        "csrf_token": _token(hospede, "/reservas/perfil/senha"),
    })

    assert resposta.status_code == 400, rotulo

    with aplicacao.app_context():
        atual = usuario_repo.buscar_por_id(conta["usuario_id"])
    assert senhas.verificar(atual["senha_hash"], SENHA), rotulo


def test_nenhuma_senha_volta_escrita_no_html(hospede):
    """Devolvê-la a escreveria no HTML, e dali ela chegaria ao histórico
    do navegador e ao cache da página."""
    nova = "senha-que-nao-pode-vazar"

    resposta = hospede.post("/reservas/perfil/senha", data={
        "senha_atual": SENHA,
        "senha_nova": nova,
        "senha_confirmacao": "diferente-de-proposito",
        "csrf_token": _token(hospede, "/reservas/perfil/senha"),
    })

    html = resposta.data.decode()

    assert nova not in html
    assert SENHA not in html


def test_a_troca_bem_sucedida_muda_a_senha(aplicacao, conta, hospede):
    nova = "senha-trocada-com-sucesso"

    resposta = hospede.post("/reservas/perfil/senha", data={
        "senha_atual": SENHA,
        "senha_nova": nova,
        "senha_confirmacao": nova,
        "csrf_token": _token(hospede, "/reservas/perfil/senha"),
    })

    assert resposta.status_code == 302

    with aplicacao.app_context():
        atual = usuario_repo.buscar_por_id(conta["usuario_id"])

    assert senhas.verificar(atual["senha_hash"], nova)
    assert not senhas.verificar(atual["senha_hash"], SENHA)

    # E a entrada passa a exigir a nova.
    with_nova = _entrar(aplicacao, DADOS["email"], nova)
    assert with_nova.get("/reservas/perfil").status_code == 200

    with_antiga = _entrar(aplicacao, DADOS["email"], SENHA)
    assert with_antiga.get("/reservas/perfil").status_code in (302, 401)


def test_a_sessao_continua_valendo_depois_da_troca(hospede):
    """Trocar a senha não expulsa quem a trocou."""
    nova = "senha-trocada-sem-expulsar"

    hospede.post("/reservas/perfil/senha", data={
        "senha_atual": SENHA,
        "senha_nova": nova,
        "senha_confirmacao": nova,
        "csrf_token": _token(hospede, "/reservas/perfil/senha"),
    })

    assert hospede.get("/reservas/perfil").status_code == 200


def test_trocar_a_senha_NAO_derruba_sessoes_ja_abertas(aplicacao, conta):
    """Uma limitação conhecida, **demonstrada** em vez de apenas afirmada.

    A sessão do Flask vive no cookie assinado: não há identificador
    guardado no servidor, e portanto não há lista de quais foram
    revogados. O servidor aceita qualquer cookie bem assinado e dentro do
    prazo.

    A consequência: se a troca de senha está sendo feita porque alguém
    tomou a conta, **quem tomou continua dentro** até a sessão expirar —
    oito horas, pelo `PERMANENT_SESSION_LIFETIME`.

    A primeira versão deste código limpava e reescrevia a sessão, com o
    comentário de que renovava o identificador. Era teatro, e a sabotagem
    provou: removê-la não quebrava nada. O cookie até mudava de valor a
    cada resposta, mas por outro motivo — `flash` altera a sessão, e a
    assinatura carrega carimbo de tempo.

    **Este teste existe para que a limitação não seja esquecida nem
    descoberta tarde.** Se um dia a invalidação de sessão for construída,
    ele falha — e falhar aqui é a notícia de que o problema foi
    resolvido.
    """
    # Duas sessões da mesma conta, como dois navegadores.
    dona = _entrar(aplicacao, DADOS["email"], SENHA)
    outra = _entrar(aplicacao, DADOS["email"], SENHA)

    assert outra.get("/reservas/perfil").status_code == 200

    nova = "senha-trocada-por-suspeita"
    dona.post("/reservas/perfil/senha", data={
        "senha_atual": SENHA,
        "senha_nova": nova,
        "senha_confirmacao": nova,
        "csrf_token": _token(dona, "/reservas/perfil/senha"),
    })

    with aplicacao.app_context():
        atual = usuario_repo.buscar_por_id(conta["usuario_id"])
    assert senhas.verificar(atual["senha_hash"], nova), (
        "a senha precisa ter mudado para o teste dizer algo"
    )

    # E a outra sessão **continua valendo**. É o que se quer registrar.
    assert outra.get("/reservas/perfil").status_code == 200, (
        "a invalidação de sessão foi construída — ótimo, e este teste "
        "precisa ser reescrito: a limitação deixou de existir"
    )


def test_a_regeneracao_no_login_continua_valendo(aplicacao, conta):
    """O contraste que impede a conclusão errada.

    Não renovar a sessão na troca de senha **não** significa que
    `session.clear()` seja inútil em toda parte. No login ele protege
    contra fixação de sessão, e aí funciona: o que importa é descartar o
    que o atacante plantou ANTES da autenticação, e isso o `clear`
    resolve — a sessão plantada não ganha `usuario_id`.

    Sem este teste, alguém leria o comentário do código e removeria
    também a limpeza do login.
    """
    import re as expressao

    fonte = (
        pathlib.Path(__file__).resolve().parents[1] / "app" / "rotas" / "auth.py"
    ).read_text(encoding="utf-8")

    corte = fonte.index("def _iniciar_sessao(")
    corpo = fonte[corte:fonte.index("\ndef ", corte + 10)]

    assert "session.clear()" in corpo, (
        "a limpeza de sessão no login protege contra fixação e não pode "
        "sair junto com a renovação que não renovava"
    )

    # E pelo comportamento: um valor plantado antes do login não sobrevive.
    cliente = aplicacao.test_client()
    with cliente.session_transaction() as sessao:
        sessao["plantado"] = "pelo atacante"

    pagina = cliente.get("/login").data.decode()
    dados = {"login": DADOS["email"], "senha": SENHA}
    achado = expressao.search(r'name="csrf_token" value="([^"]+)"', pagina)
    if achado:
        dados["csrf_token"] = achado.group(1)
    cliente.post("/login", data=dados)

    with cliente.session_transaction() as sessao:
        assert "plantado" not in dict(sessao)


def test_a_troca_fica_no_log_de_auditoria(aplicacao, hospede):
    """É a única operação pela qual um acesso temporário vira permanente.

    Quando acontecer, a linha do log é o que permite datar o momento.
    """
    nova = "senha-que-vai-para-o-log"

    hospede.post("/reservas/perfil/senha", data={
        "senha_atual": SENHA,
        "senha_nova": nova,
        "senha_confirmacao": nova,
        "csrf_token": _token(hospede, "/reservas/perfil/senha"),
    })

    with aplicacao.app_context():
        linhas = [
            linha for linha in auditoria_repo.listar(limite=50)
            if linha["acao"] == auditoria_repo.SENHA_ALTERADA
        ]

    assert linhas, "a troca de senha não deixou linha de auditoria"

    detalhe = str(linhas[0]["detalhe"])
    assert nova not in detalhe, "a senha foi parar no log"
    assert SENHA not in detalhe


def test_a_senha_atual_errada_tambem_fica_no_log(aplicacao, hospede):
    """Tentativa de troca com senha errada é sinal de sessão tomada.

    É exatamente o que alguém faria com um navegador encontrado aberto:
    tentar fixar o acesso. Sem registro, a tentativa não deixa rastro.
    """
    hospede.post("/reservas/perfil/senha", data={
        "senha_atual": "chute",
        "senha_nova": "senha-nova-bem-longa",
        "senha_confirmacao": "senha-nova-bem-longa",
        "csrf_token": _token(hospede, "/reservas/perfil/senha"),
    })

    with aplicacao.app_context():
        linhas = [
            linha for linha in auditoria_repo.listar(limite=50)
            if "troca de senha" in str(linha["detalhe"]).lower()
        ]

    assert linhas


# ---------------------------------------------------------------------------
# Autorização
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("credenciais", [ADMIN, RECEPCAO], ids=["admin", "recepcao"])
@pytest.mark.parametrize("rota", ["/reservas/perfil", "/reservas/perfil/senha"])
def test_o_portal_e_so_do_hospede(aplicacao, credenciais, rota):
    """A equipe não tem perfil de hóspede para ver.

    E a recusa é 403 e não 404: a rota existe, e quem a procura com outro
    perfil precisa saber que o problema é o perfil.
    """
    cliente = _entrar(aplicacao, *credenciais)

    assert cliente.get(rota).status_code == 403


def test_o_portal_exige_sessao(aplicacao):
    visitante = aplicacao.test_client()

    for rota in ("/reservas/perfil", "/reservas/perfil/senha"):
        resposta = visitante.get(rota, follow_redirects=False)
        assert resposta.status_code == 302
        assert "/login" in resposta.headers["Location"]


def test_a_troca_de_senha_exige_token_csrf(aplicacao, conta):
    """Sem o token, um sítio hostil faria o navegador de quem está
    autenticado trocar a senha aproveitando o cookie de sessão.

    Verificado com a proteção ligada de propósito: em modo de teste ela
    fica desligada para não atrapalhar os demais.
    """
    configuracao = Configuracao(modo_teste=True)
    configuracao.WTF_CSRF_ENABLED = True

    conexao.encerrar_pool()
    conexao.iniciar_pool(configuracao)
    app = create_app(configuracao)
    app.config["SESSION_COOKIE_SECURE"] = False
    app.config["WTF_CSRF_ENABLED"] = True

    try:
        cliente = _entrar(app, DADOS["email"], SENHA)

        resposta = cliente.post("/reservas/perfil/senha", data={
            "senha_atual": SENHA,
            "senha_nova": "senha-sem-token-nenhum",
            "senha_confirmacao": "senha-sem-token-nenhum",
        })

        assert resposta.status_code == 400

        with app.app_context():
            atual = usuario_repo.buscar_por_id(conta["usuario_id"])
        assert senhas.verificar(atual["senha_hash"], SENHA)
    finally:
        conexao.encerrar_pool()
        conexao.iniciar_pool(Configuracao(modo_teste=True))


def test_o_menu_do_hospede_oferece_o_perfil(hospede):
    html = hospede.get("/painel/").data.decode()
    barra = html.split("</header>")[0]

    assert "/reservas/perfil" in barra
    assert "Meu perfil" in barra
