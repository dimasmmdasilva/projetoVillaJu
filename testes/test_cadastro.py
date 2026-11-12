"""Autocadastro público do hóspede, com confirmação por e-mail.

Etapa 5, Bloco 3. O que estes testes guardam, em ordem de importância:

1. **A resposta é idêntica** para e-mail novo e para e-mail já cadastrado.
   É o que impede que o formulário seja usado para levantar a lista de
   clientes da hospedaria.
2. **A conta não autentica antes da confirmação.** Criar conta e poder
   entrar são coisas separadas, e o que as separa é a prova de posse do
   endereço.
3. **A equipe nunca conhece a senha**, e a senha não aparece em lugar
   nenhum fora do hash: nem no e-mail, nem no HTML devolvido, nem no log.
4. **Ninguém cria conta sobre o hóspede de outra pessoa**, nem quando
   essa pessoa já tem conta com login de apelido.
5. **O cadastro público não edita dado de hóspede existente.**

Os testes de token em si estão em `test_token.py`; aqui verifica-se o
fluxo que os usa.
"""

import pathlib
import re
import secrets
import tempfile

import pytest

from app import create_app
from app.config import Configuracao
from app.dados import conexao, hospede_repo, token_repo, usuario_repo
from app.seguranca import senhas
from app.servicos import cadastro, correio

# ---------------------------------------------------------------------------
# Massa
# ---------------------------------------------------------------------------

ADMIN = ("admin", "Villa@Admin2026")

# Documentos válidos quanto ao dígito verificador e distintos entre si, para
# que um teste não colida com a unicidade deixada por outro.
DOCUMENTO = "529.982.247-25"
OUTRO_DOCUMENTO = "111.444.777-35"

SENHA = "jureia-praia-2026"

NOVO = {
    # Com maiúsculas e espaços de propósito: a normalização do índice tem
    # de deixar este cadastro e um futuro `mariana.prado@exemplo.com`
    # caírem no mesmo login.
    "nome": "  Mariana Prado  ",
    "documento": DOCUMENTO,
    "email": "Mariana.Prado@Exemplo.COM",
    "telefone": "(12) 99888-7766",
    "senha": SENHA,
    "senha_confirmacao": SENHA,
}


@pytest.fixture
def pasta_de_emails():
    return pathlib.Path(tempfile.mkdtemp(prefix="villaju-emails-"))


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

    # A fábrica guarda a configuração em `app.villaju`, e é de lá que a
    # rota a lê. Reafirmado aqui para que a troca de pasta e de modo feita
    # acima seja a que a rota vê, e não a do ambiente.
    app.villaju = configuracao

    yield app
    conexao.encerrar_pool()


@pytest.fixture
def visitante(aplicacao):
    return aplicacao.test_client()


# ---------------------------------------------------------------------------
# Auxiliares
# ---------------------------------------------------------------------------

def _enviadas(pasta):
    """As mensagens gravadas, já decodificadas.

    Pela função do produto, e não lendo o arquivo cru. `set_content`
    codifica o corpo em base64 quando há acento — e há, em toda mensagem
    em português. Um teste que lesse o arquivo direto procuraria o link
    no base64 e não o acharia, concluindo que o envio falhou quando ele
    funcionou.
    """
    class _Configuracao:
        EMAIL_PASTA = pasta

    return correio.listar_enviadas(_Configuracao)


def _link_de_confirmacao(pasta):
    for mensagem in _enviadas(pasta):
        achado = re.search(r"/confirmar/([A-Za-z0-9_\-]+)", mensagem["corpo"])
        if achado:
            return achado.group(1)
    return None


def _cadastrar(visitante, **mudancas):
    return visitante.post("/cadastro", data={**NOVO, **mudancas})


def _entrar(aplicacao, login, senha):
    cliente = aplicacao.test_client()
    return cliente, cliente.post(
        "/login", data={"login": login, "senha": senha}
    )


# ---------------------------------------------------------------------------
# A tela
# ---------------------------------------------------------------------------

def test_a_tela_de_cadastro_e_publica(visitante):
    """Sem sessão, sem redirecionamento para o login.

    É a porta de entrada de quem ainda não tem conta: exigir conta para
    criar conta seria a contradição óbvia, e já esteve no sistema — até a
    Etapa 5 não havia tela alguma que criasse usuário.
    """
    resposta = visitante.get("/cadastro")

    assert resposta.status_code == 200
    assert "Criar conta" in resposta.data.decode()


def test_a_tela_pede_documento_e_a_de_contato_nao(visitante):
    """As duas portas da face pública, e a diferença entre elas.

    Quem só quer perguntar o preço não dá CPF; quem cria conta para
    reservar, dá. A decisão está na Etapa 5 e este teste a prende: se um
    dia o campo sair daqui, ou entrar lá, a verificação acusa.
    """
    cadastro_html = visitante.get("/cadastro").data.decode()
    contato_html = visitante.get("/contato").data.decode()

    assert 'name="documento"' in cadastro_html
    assert 'name="documento"' not in contato_html


def test_a_tela_tem_as_tres_defesas_do_formulario_publico(visitante):
    """Token CSRF, campo-armadilha e — fora do teste — limitação de taxa.

    O mesmo conjunto do formulário de contato. Esta porta cria conta, o
    que a torna mais atraente para abuso, não menos.
    """
    html = visitante.get("/cadastro").data.decode()

    assert 'name="csrf_token"' in html
    assert f'name="{cadastro.CAMPO_ARMADILHA if hasattr(cadastro, "CAMPO_ARMADILHA") else "assunto"}"' in html
    assert 'class="armadilha"' in html


def test_a_tela_nao_mostra_preco(visitante):
    """A decisão de não publicar valor vale nesta tela também.

    Seria o lugar tentador: alguém criando conta para reservar quer saber
    quanto custa. A resposta continua sendo o pedido de contato, que a
    recepção responde caso a caso.
    """
    assert "R$" not in visitante.get("/cadastro").data.decode()


# ---------------------------------------------------------------------------
# O caminho feliz
# ---------------------------------------------------------------------------

def test_o_cadastro_cria_hospede_e_conta_inativa(visitante, aplicacao):
    with aplicacao.app_context():
        antes = hospede_repo.contar()

    resposta = _cadastrar(visitante)

    assert resposta.status_code == 302
    assert "/cadastro/enviado" in resposta.headers["Location"]

    with aplicacao.app_context():
        assert hospede_repo.contar() == antes + 1

        conta = usuario_repo.buscar_por_login(
            usuario_repo.login_de_email(NOVO["email"])
        )

    assert conta is not None
    assert conta["perfil"] == "hospede"
    assert not conta["ativo"], "a conta tem de nascer inativa"
    assert conta["hospede_id"] is not None, (
        "a trigger exige o vínculo, e é ele que dá à conta as reservas que "
        "ela pode consultar"
    )


def test_a_senha_e_gravada_apenas_como_hash_argon2(visitante, aplicacao):
    """A equipe nunca conhece a senha do hóspede.

    Requisito explícito da Etapa 5. A verificação é direta: o que está na
    coluna é uma string de Argon2id, e a senha digitada não aparece nela.
    """
    _cadastrar(visitante)

    with aplicacao.app_context():
        conta = usuario_repo.buscar_por_login(
            usuario_repo.login_de_email(NOVO["email"])
        )

    assert conta["senha_hash"].startswith("$argon2id$")
    assert SENHA not in conta["senha_hash"]
    assert senhas.verificar(conta["senha_hash"], SENHA), (
        "o hash gravado precisa reconhecer a senha escolhida"
    )


def test_o_email_leva_o_link_e_nao_a_senha(visitante, pasta_de_emails):
    _cadastrar(visitante)

    enviadas = _enviadas(pasta_de_emails)
    assert len(enviadas) == 1

    mensagem = enviadas[0]
    assert "/confirmar/" in mensagem["corpo"]
    assert SENHA not in mensagem["corpo"], (
        "a senha não tem o que fazer no e-mail: ela vai para o hash e para "
        "lugar nenhum mais"
    )
    assert NOVO["documento"] not in mensagem["corpo"], (
        "nem o documento: o e-mail trafega em rede de terceiros"
    )


def test_a_conta_nao_autentica_antes_da_confirmacao(visitante, aplicacao):
    """A separação entre existir e funcionar.

    O mecanismo que recusa já existia desde a Etapa 2 — `usuario.ativo` é
    conferido no login. O que a Etapa 5 acrescenta é um caminho que o usa.
    """
    _cadastrar(visitante)

    _, resposta = _entrar(aplicacao, NOVO["email"], SENHA)

    assert resposta.status_code == 401


def test_o_link_confirma_e_libera_a_entrada(
    visitante, aplicacao, pasta_de_emails
):
    """O percurso inteiro, que é o critério de conclusão do bloco."""
    _cadastrar(visitante)

    token = _link_de_confirmacao(pasta_de_emails)
    assert token, "o e-mail precisa trazer o token"

    confirmacao = visitante.get(f"/confirmar/{token}")
    assert confirmacao.status_code == 200
    assert "sucesso" in confirmacao.data.decode().lower()

    _, entrada = _entrar(aplicacao, NOVO["email"], SENHA)
    assert entrada.status_code == 302
    assert "/painel" in entrada.headers["Location"]


def test_o_link_vale_uma_vez_so(visitante, pasta_de_emails):
    _cadastrar(visitante)
    token = _link_de_confirmacao(pasta_de_emails)

    assert visitante.get(f"/confirmar/{token}").status_code == 200
    assert visitante.get(f"/confirmar/{token}").status_code == 400


# ---------------------------------------------------------------------------
# A resposta idêntica
# ---------------------------------------------------------------------------

def test_a_resposta_e_identica_para_email_novo_e_para_email_ja_cadastrado(
    visitante, aplicacao
):
    """O requisito central do bloco.

    Comparados o código de estado, o destino do redirecionamento e o
    corpo da página de confirmação. Se qualquer um dos três diferisse,
    submeter endereços e ler as respostas levantaria a lista de clientes.
    """
    primeira = _cadastrar(visitante)
    segunda = _cadastrar(visitante)

    assert primeira.status_code == segunda.status_code
    assert primeira.headers["Location"] == segunda.headers["Location"]

    pagina_um = visitante.get(primeira.headers["Location"]).data.decode()
    pagina_dois = visitante.get(segunda.headers["Location"]).data.decode()
    assert pagina_um == pagina_dois


def test_o_segundo_cadastro_nao_cria_segunda_conta(visitante, aplicacao):
    _cadastrar(visitante)
    _cadastrar(visitante)

    with aplicacao.app_context():
        contas = [
            c for c in usuario_repo.listar()
            if c["login"] == usuario_repo.login_de_email(NOVO["email"])
        ]

    assert len(contas) == 1


def test_quem_ja_tem_conta_recebe_aviso_e_nao_convite(
    visitante, pasta_de_emails
):
    """O e-mail distingue; a resposta HTTP não.

    É a assimetria que torna o autocadastro seguro: quem é dono do
    endereço fica sabendo da tentativa, e quem submeteu o formulário não
    aprende nada.

    O aviso **não leva link de ativação nem de troca de senha**. Se
    levasse, submeter o endereço de outra pessoa provocaria o envio de um
    link capaz de mexer na conta dela, e a defesa viraria a ferramenta do
    ataque.
    """
    _cadastrar(visitante)
    _cadastrar(visitante)

    enviadas = _enviadas(pasta_de_emails)
    assert len(enviadas) == 2

    # `listar_enviadas` devolve da mais recente para a mais antiga.
    aviso = enviadas[0]
    assert "Tentativa de cadastro" in aviso["assunto"]
    assert "/confirmar/" not in aviso["corpo"], (
        "o aviso não pode carregar link de ativação"
    )


def test_a_pagina_de_confirmacao_nao_afirma_que_a_conta_foi_criada(visitante):
    """A redação condicional, verificada como texto.

    "Se este endereço estiver disponível" é verdadeiro nos dois casos e
    informativo em nenhum. Afirmar "cadastro criado" revelaria que o
    endereço não tinha conta.
    """
    html = visitante.get("/cadastro/enviado").data.decode()

    assert "Se este endereço estiver disponível" in html
    assert "cadastro criado" not in html.lower()
    assert "já está em uso" not in html.lower()
    assert "já existe" not in html.lower()


# ---------------------------------------------------------------------------
# Ninguém cria conta sobre o hóspede de outra pessoa
# ---------------------------------------------------------------------------

def test_hospede_com_conta_de_apelido_nao_ganha_segunda_conta(
    visitante, aplicacao, pasta_de_emails
):
    """O ataque que a verificação por login sozinha deixaria passar.

    Cenário: a recepção cadastrou o hóspede e criou o acesso dele com um
    apelido — `mariana`, e não o HMAC do e-mail. É o que o Bloco 4 fará,
    e é o que a massa fictícia já tem com a conta `joao`.

    Alguém que soubesse o endereço poderia então cadastrá-lo outra vez: o
    login do autocadastro é o HMAC e não colide com o apelido, de modo
    que uma verificação feita só por login não veria nada e uma SEGUNDA
    conta nasceria apontando para o mesmo hóspede.

    A conta nasceria inativa — mas o link de ativação iria para o
    endereço do hóspede de verdade, que, achando ser o próprio cadastro,
    abriria o link e ativaria uma conta cuja senha quem atacou escolheu.
    Dali em diante, as reservas dele estariam visíveis.

    Por isso o cadastro pergunta se o **hóspede** já tem conta, e não
    apenas se o login existe.
    """
    with aplicacao.app_context():
        hospede_id = hospede_repo.criar({
            "nome": "Mariana Prado",
            "documento": DOCUMENTO,
            "email": "mariana.prado@exemplo.com",
            "telefone": "(12) 99888-7766",
        })
        usuario_repo.criar(
            login="mariana",
            nome="Mariana Prado",
            senha_hash=senhas.gerar_hash("senha-da-recepcao-2026"),
            perfil="hospede",
            hospede_id=hospede_id,
            ativo=True,
        )

    resposta = _cadastrar(
        visitante,
        email="mariana.prado@exemplo.com",
        documento=OUTRO_DOCUMENTO,
    )

    # A resposta é a de sempre.
    assert resposta.status_code == 302
    assert "/cadastro/enviado" in resposta.headers["Location"]

    with aplicacao.app_context():
        contas = [
            c for c in usuario_repo.listar() if c["hospede_id"] == hospede_id
        ]

    assert len(contas) == 1, (
        "nasceu uma segunda conta para o mesmo hóspede; quem souber o "
        "endereço de e-mail dele consegue acesso às reservas"
    )
    assert contas[0]["login"] == "mariana"

    # E a pessoa foi avisada, sem receber link algum.
    enviadas = _enviadas(pasta_de_emails)
    assert len(enviadas) == 1
    assert "Tentativa de cadastro" in enviadas[0]["assunto"]
    assert "/confirmar/" not in enviadas[0]["corpo"]


def test_hospede_sem_conta_tem_a_conta_vinculada_ao_registro_existente(
    visitante, aplicacao
):
    """O caminho de quem veio pelo formulário de contato.

    A recepção converteu o pedido num cadastro de hóspede, sem criar
    acesso. Quando a pessoa cria conta, ela precisa cair **no mesmo**
    registro — não num segundo hóspede com os mesmos dados.
    """
    with aplicacao.app_context():
        hospede_id = hospede_repo.criar({
            "nome": "Mariana Prado",
            "documento": DOCUMENTO,
            "email": "mariana.prado@exemplo.com",
        })
        antes = hospede_repo.contar()

    _cadastrar(
        visitante,
        email="mariana.prado@exemplo.com",
        documento=OUTRO_DOCUMENTO,
    )

    with aplicacao.app_context():
        assert hospede_repo.contar() == antes, (
            "o cadastro criou um hóspede novo em vez de usar o que existia"
        )
        contas = [
            c for c in usuario_repo.listar() if c["hospede_id"] == hospede_id
        ]

    assert len(contas) == 1
    assert not contas[0]["ativo"]


def test_o_cadastro_publico_nao_altera_dado_de_hospede_existente(
    visitante, aplicacao
):
    """Cria, não edita.

    A tentação seria "manter os dados em dia" com o que veio do
    formulário. Seria falha grave: quem soubesse o endereço de e-mail de
    um hóspede reescreveria os dados pessoais dele sem autenticar-se.
    """
    with aplicacao.app_context():
        hospede_id = hospede_repo.criar({
            "nome": "Mariana Prado",
            "documento": DOCUMENTO,
            "email": "mariana.prado@exemplo.com",
            "telefone": "(12) 99888-7766",
        })

    _cadastrar(
        visitante,
        nome="Outro Nome Qualquer",
        email="mariana.prado@exemplo.com",
        documento=OUTRO_DOCUMENTO,
        telefone="(11) 90000-0000",
    )

    with aplicacao.app_context():
        depois = hospede_repo.buscar_por_id(hospede_id)

    assert depois["nome"] == "Mariana Prado"
    assert depois["telefone"] == "(12) 99888-7766"
    assert depois["documento"] == DOCUMENTO


def test_documento_de_outra_pessoa_nao_cria_cadastro(visitante, aplicacao):
    """Documento já usado: nada é criado, e a resposta é a de sempre.

    Decidir aqui quem é o legítimo dono do documento não é possível e não
    é o lugar. A recepção resolve, e o visitante não descobre nada.
    """
    with aplicacao.app_context():
        hospede_repo.criar({
            "nome": "Outra Pessoa",
            "documento": DOCUMENTO,
            "email": "outra.pessoa@exemplo.com",
        })
        antes = hospede_repo.contar()

    resposta = _cadastrar(visitante, email="terceiro@exemplo.com")

    assert resposta.status_code == 302
    assert "/cadastro/enviado" in resposta.headers["Location"]

    with aplicacao.app_context():
        assert hospede_repo.contar() == antes


# ---------------------------------------------------------------------------
# Validação, e o que não volta à tela
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "rotulo,mudanca",
    [
        ("sem nome", {"nome": ""}),
        ("sem documento", {"documento": ""}),
        ("sem e-mail", {"email": ""}),
        ("e-mail sem arroba", {"email": "naoeumendereco"}),
        ("e-mail sem ponto no domínio", {"email": "alguem@exemplo"}),
        ("sem senha", {"senha": "", "senha_confirmacao": ""}),
        ("senha curta", {"senha": "curta1", "senha_confirmacao": "curta1"}),
        ("confirmação diferente", {"senha_confirmacao": "outra-senha-longa"}),
    ],
)
def test_formulario_invalido_e_recusado_com_mensagem(
    visitante, aplicacao, rotulo, mudanca
):
    with aplicacao.app_context():
        antes = hospede_repo.contar()

    resposta = _cadastrar(visitante, **mudanca)

    assert resposta.status_code == 400, rotulo
    assert "aviso-erro" in resposta.data.decode(), (
        f"{rotulo}: a recusa precisa dizer o que está errado"
    )

    with aplicacao.app_context():
        assert hospede_repo.contar() == antes, rotulo


def test_a_senha_nao_volta_preenchida_num_envio_recusado(visitante):
    """O campo de senha fica em branco, e isso é deliberado.

    Devolvê-lo escreveria a senha no HTML, e dali ela chegaria ao
    histórico do navegador, ao cache da página e a qualquer registro de
    proxy no caminho. Os outros campos voltam, para poupar a digitação.
    """
    resposta = _cadastrar(visitante, senha_confirmacao="diferente-e-longa")
    html = resposta.data.decode()

    assert SENHA not in html, "a senha voltou escrita no HTML"
    assert "diferente-e-longa" not in html
    assert "Mariana Prado" in html, (
        "os campos sem risco voltam preenchidos: recusar o envio não deve "
        "obrigar a digitar tudo de novo"
    )


def test_a_mensagem_de_recusa_nunca_fala_do_email_existir(
    visitante, aplicacao
):
    """Nenhuma validação pode virar oráculo por via indireta.

    Um cadastro repetido com senha curta tem de reclamar da senha — e
    nunca do endereço —, porque a recusa aparece antes de qualquer
    consulta ao banco.
    """
    _cadastrar(visitante)

    resposta = _cadastrar(visitante, senha="curta1", senha_confirmacao="curta1")
    texto = resposta.data.decode().lower()

    for revelador in ("já existe", "já está em uso", "já cadastrado", "em uso"):
        assert revelador not in texto, (
            f"a mensagem de recusa revela que o endereço existe: {revelador}"
        )


# ---------------------------------------------------------------------------
# Campo-armadilha
# ---------------------------------------------------------------------------

def test_a_armadilha_descarta_em_silencio(
    visitante, aplicacao, pasta_de_emails
):
    """Resposta idêntica à de um envio bem-sucedido, e nada criado.

    O silêncio é a parte que importa: responder "você parece um robô"
    entrega ao autor do robô a informação de que ele precisa para
    ajustá-lo.
    """
    with aplicacao.app_context():
        antes = hospede_repo.contar()

    resposta = _cadastrar(visitante, assunto="promoção imperdível")

    assert resposta.status_code == 302
    assert "/cadastro/enviado" in resposta.headers["Location"]

    with aplicacao.app_context():
        assert hospede_repo.contar() == antes

    assert _enviadas(pasta_de_emails) == [], (
        "o envio barrado pela armadilha não deve custar um e-mail"
    )


def test_a_armadilha_nao_enfileira_mensagem_na_sessao(visitante):
    """O vazamento que escapou de uma sabotagem na Etapa 4.

    Um `flash()` antes do redirecionamento não aparece na página de
    confirmação, que não renderiza mensagens — mas fica na sessão e
    aparece na página SEGUINTE. A verificação é na sessão, não na tela.
    """
    _cadastrar(visitante, assunto="promoção")

    with visitante.session_transaction() as sessao:
        assert "_flashes" not in sessao


# ---------------------------------------------------------------------------
# A rota de confirmação
# ---------------------------------------------------------------------------

# O caso "bem formado" é GERADO, e não escrito como literal.
#
# A primeira versão trazia uma cadeia de 39 caracteres alfanuméricos
# escrita à mão, para ter o aspecto de um token de verdade. O gitleaks a
# leu como `generic-api-key` e passou a reportar um vazamento no
# repositório — falso, mas indistinguível de um verdadeiro na saída da
# ferramenta.
#
# O custo não é o aviso: é que "gitleaks sem achados" deixa de ser um
# sinal utilizável. Um relatório de segurança que precisa explicar os
# seus próprios falsos positivos já perdeu metade do valor.
#
# `secrets.token_urlsafe(32)` resolve melhor em dois sentidos. Não há
# literal na fonte para nenhuma ferramenta confundir com credencial, e o
# valor é um token **genuinamente bem formado** — 256 bits de
# aleatoriedade, no mesmo formato que `token_repo.gerar` produz — que
# com certeza nunca foi emitido. É um caso de teste mais honesto do que
# a cadeia inventada.
TOKEN_BEM_FORMADO_MAS_NUNCA_EMITIDO = secrets.token_urlsafe(32)


@pytest.mark.parametrize(
    "rotulo,token",
    [
        ("inexistente", "x" * 43),
        ("texto qualquer", "nao-existe"),
        ("curto", "a"),
        ("bem formado, nunca emitido", TOKEN_BEM_FORMADO_MAS_NUNCA_EMITIDO),
    ],
)
def test_token_que_nao_serve_e_recusado(visitante, rotulo, token):
    """Recusa com 400, para qualquer coisa que não seja um token válido.

    **O requisito não é que a página se cale sobre as causas** — é que
    ela não diga em QUAL delas o visitante caiu. A tela lista as três
    possibilidades sem apontar nenhuma, que é a redação certa: informa o
    bastante para a pessoa saber o que fazer, e não distingue os casos.

    Quem prova a indistinguibilidade é
    `test_os_casos_de_falha_dao_exatamente_a_mesma_pagina`, comparando
    bytes. Este aqui garante apenas que nada disso é aceito.
    """
    resposta = visitante.get(f"/confirmar/{token}")

    assert resposta.status_code == 400, rotulo


def test_a_pagina_de_falha_nao_aponta_a_causa(visitante, pasta_de_emails):
    """A redação verificada, e não só a igualdade entre as respostas.

    Duas páginas idênticas que ambas dissessem "este link venceu"
    passariam no teste de igualdade e mentiriam para quem caiu no caso de
    token inexistente — além de confirmar, a quem está sondando, que o
    token existia e apenas venceu.

    A tela precisa falar no condicional. O teste procura as afirmações
    categóricas que não pode haver.
    """
    _cadastrar(visitante)
    token = _link_de_confirmacao(pasta_de_emails)
    visitante.get(f"/confirmar/{token}")

    texto = visitante.get(f"/confirmar/{token}").data.decode().lower()

    for afirmacao in (
        "este link venceu",
        "link expirado",
        "já foi usado",
        "token inexistente",
        "este link não existe",
    ):
        assert afirmacao not in texto, (
            f"a página aponta a causa da falha: {afirmacao!r}"
        )

    assert "pode ter" in texto, (
        "a página precisa apresentar as causas como possibilidades"
    )


def test_os_casos_de_falha_dao_exatamente_a_mesma_pagina(
    visitante, pasta_de_emails
):
    """Requisito do Bloco 2, verificado aqui onde ele aparece ao visitante.

    Token inexistente e token já consumido produzem o mesmo HTML. Se
    diferissem, seria possível descobrir que um token existe.
    """
    _cadastrar(visitante)
    token = _link_de_confirmacao(pasta_de_emails)

    visitante.get(f"/confirmar/{token}")           # queima
    consumido = visitante.get(f"/confirmar/{token}")
    inexistente = visitante.get(f"/confirmar/{'z' * 43}")

    assert consumido.status_code == inexistente.status_code
    assert consumido.data == inexistente.data


def test_token_de_recuperacao_nao_confirma_cadastro(visitante, aplicacao):
    """Os tipos não se substituem.

    Um token de recuperação de senha que ativasse conta transformaria o
    fluxo de "esqueci a senha" num atalho para ativar cadastro não
    confirmado.
    """
    _cadastrar(visitante)

    with aplicacao.app_context():
        conta = usuario_repo.buscar_por_login(
            usuario_repo.login_de_email(NOVO["email"])
        )
        outro = token_repo.gerar(conta["id"], token_repo.RECUPERACAO)

    assert visitante.get(f"/confirmar/{outro}").status_code == 400

    with aplicacao.app_context():
        depois = usuario_repo.buscar_por_id(conta["id"])
    assert not depois["ativo"]


def test_a_pagina_de_confirmacao_nao_exibe_dado_da_conta(
    visitante, pasta_de_emails
):
    """A rota é pública, e a resposta dela não carrega dado de pessoa.

    É por isso que `cadastro.confirmar` devolve um booleano e não o
    registro: o máximo que a rota tem em mãos é "ativou" ou "não ativou".
    """
    _cadastrar(visitante)
    token = _link_de_confirmacao(pasta_de_emails)

    html = visitante.get(f"/confirmar/{token}").data.decode()

    assert "Mariana" not in html
    assert NOVO["email"] not in html
    assert NOVO["documento"] not in html


# ---------------------------------------------------------------------------
# O login passou a aceitar e-mail
# ---------------------------------------------------------------------------

def test_o_login_da_equipe_continua_sendo_o_apelido(aplicacao):
    """A alteração no login não pode ter quebrado a entrada da equipe.

    `admin` e `recepcao` não têm arroba, seguem o caminho literal.
    """
    _, resposta = _entrar(aplicacao, *ADMIN)

    assert resposta.status_code == 302
    assert "/painel" in resposta.headers["Location"]


def test_o_login_do_hospede_e_o_email_em_qualquer_caixa(
    visitante, aplicacao, pasta_de_emails
):
    """Normalização de ponta a ponta.

    O cadastro veio com `Mariana.Prado@Exemplo.COM`; a entrada é feita
    com tudo em minúsculas e com espaços nas pontas. Precisa dar no mesmo
    login, porque `cripto.indice` normaliza antes do HMAC — e se não
    desse, a pessoa não conseguiria entrar com o próprio e-mail.
    """
    _cadastrar(visitante)
    visitante.get(f"/confirmar/{_link_de_confirmacao(pasta_de_emails)}")

    _, resposta = _entrar(aplicacao, "  mariana.prado@exemplo.com  ", SENHA)

    assert resposta.status_code == 302


def test_a_tabela_de_usuario_nunca_guarda_o_endereco_em_claro(
    visitante, aplicacao
):
    """A razão de o login ser o HMAC e não o e-mail.

    A coluna `login` é texto claro e tem restrição de unicidade. Guardar
    o endereço nela desfaria a cifragem que a tabela `hospede` aplica ao
    mesmo dado, e um UNIQUE sobre e-mail em claro é por si um oráculo de
    enumeração.
    """
    _cadastrar(visitante)

    with aplicacao.app_context():
        logins = [c["login"] for c in usuario_repo.listar()]

    assert NOVO["email"] not in logins
    assert NOVO["email"].lower() not in logins
    assert not any("@" in login for login in logins), (
        f"há arroba num login, e portanto possivelmente um e-mail em "
        f"claro: {logins}"
    )


def test_o_login_derivado_cabe_na_coluna():
    """VARCHAR(50), e o HMAC em base64url ocupa 43.

    Sem o `rstrip`, o preenchimento levaria a 44 — ainda caberia, mas a
    margem é o que permite não pensar nisso de novo. O teste existe
    porque um login que não couber é truncado pelo MySQL, e dois
    endereços diferentes poderiam colidir no mesmo login truncado.
    """
    longo = "a" * 100 + "@exemplo.com.br"
    derivado = usuario_repo.login_de_email(longo)

    assert len(derivado) == 43
    assert len(derivado) <= usuario_repo.TAMANHO_MAXIMO_DO_LOGIN
    assert "=" not in derivado


def test_enderecos_diferentes_dao_logins_diferentes():
    """O contraste que dá sentido ao teste de normalização.

    Se `login_de_email` devolvesse sempre o mesmo valor, o teste de caixa
    alta passaria sem verificar nada — e duas pessoas colidiriam na mesma
    conta.
    """
    um = usuario_repo.login_de_email("alguem@exemplo.com")
    outro = usuario_repo.login_de_email("outro@exemplo.com")

    assert um != outro
    assert usuario_repo.login_de_email("ALGUEM@EXEMPLO.COM") == um


# ---------------------------------------------------------------------------
# O repositório de usuário
# ---------------------------------------------------------------------------

def test_criar_recusa_senha_em_claro(aplicacao):
    """A proteção contra o erro silencioso mais fácil de cometer.

    Um `senha_hash` que chegasse em claro seria gravado e não
    autenticaria ninguém, porque `senhas.verificar` não reconheceria o
    formato: a conta ficaria inutilizável e a senha, exposta na coluna.
    """
    with aplicacao.app_context():
        with pytest.raises(usuario_repo.ErroDeUsuario):
            usuario_repo.criar(
                login="tentativa",
                nome="Alguém",
                senha_hash="minha-senha-em-claro",
                perfil="recepcionista",
            )


def test_criar_recusa_perfil_desconhecido(aplicacao):
    with aplicacao.app_context():
        with pytest.raises(usuario_repo.ErroDeUsuario):
            usuario_repo.criar(
                login="tentativa",
                nome="Alguém",
                senha_hash=senhas.gerar_hash("senha-longa-o-bastante"),
                perfil="gerente",
            )


def test_criar_nasce_inativo_por_omissao(aplicacao):
    """O padrão fica do lado seguro.

    Uma chamada que esqueça o parâmetro cria conta que não entra, e não
    conta que entra sem o endereço ter sido comprovado.
    """
    with aplicacao.app_context():
        identificador = usuario_repo.criar(
            login="novo-recepcionista",
            nome="Quem Chegou",
            senha_hash=senhas.gerar_hash("senha-longa-o-bastante"),
            perfil="recepcionista",
        )
        criado = usuario_repo.buscar_por_id(identificador)

    assert not criado["ativo"]


def test_criar_recusa_login_repetido(aplicacao):
    with aplicacao.app_context():
        with pytest.raises(usuario_repo.LoginEmUso):
            usuario_repo.criar(
                login="admin",
                nome="Impostor",
                senha_hash=senhas.gerar_hash("senha-longa-o-bastante"),
                perfil="administrador",
            )


def test_criar_respeita_a_trigger_de_integridade_de_perfil(aplicacao):
    """A regra mora no banco, e a aplicação não a duplica.

    Perfil `hospede` sem `hospede_id` é recusado por
    `trg_usuario_antes_inserir`, e não por uma verificação em Python —
    que teria de ser mantida em sincronia com a trigger e valeria apenas
    para quem passasse por este código.
    """
    from app.dados import erros

    with aplicacao.app_context():
        with pytest.raises(erros.RegraDoBanco):
            usuario_repo.criar(
                login="hospede-solto",
                nome="Sem Vínculo",
                senha_hash=senhas.gerar_hash("senha-longa-o-bastante"),
                perfil="hospede",
                hospede_id=None,
            )


def test_ativar_e_condicional_e_nao_reativa(aplicacao):
    """Devolve se mudou algo, e a condição está no comando.

    Duas aberturas simultâneas do mesmo link não podem ambas ativar, e a
    segunda descobre isso pelo número de linhas afetadas em vez de por
    uma leitura que já envelheceu.
    """
    with aplicacao.app_context():
        identificador = usuario_repo.criar(
            login="para-ativar",
            nome="Quem Chegou",
            senha_hash=senhas.gerar_hash("senha-longa-o-bastante"),
            perfil="recepcionista",
        )

        assert usuario_repo.ativar(identificador) is True
        assert usuario_repo.ativar(identificador) is False

    assert "ativo = FALSE" in usuario_repo._SQL_ATIVAR, (
        "a condição precisa estar no UPDATE, não numa leitura anterior"
    )


def test_existe_para_hospede_ve_conta_de_qualquer_login(aplicacao):
    """A pergunta que fecha o ataque da segunda conta."""
    with aplicacao.app_context():
        hospede_id = hospede_repo.criar({
            "nome": "Mariana Prado",
            "documento": DOCUMENTO,
            "email": "mariana.prado@exemplo.com",
        })

        assert usuario_repo.existe_para_hospede(hospede_id) is False

        usuario_repo.criar(
            login="apelido-qualquer",
            nome="Mariana Prado",
            senha_hash=senhas.gerar_hash("senha-longa-o-bastante"),
            perfil="hospede",
            hospede_id=hospede_id,
            ativo=True,
        )

        assert usuario_repo.existe_para_hospede(hospede_id) is True


# ---------------------------------------------------------------------------
# O serviço, isolado da rota
# ---------------------------------------------------------------------------

def test_registrar_nao_devolve_nada(aplicacao, pasta_de_emails):
    """O retorno vazio é parte da defesa.

    Se devolvesse o identificador criado, a rota poderia — numa alteração
    futura — ramificar sobre ele, e a diferença entre e-mail novo e
    e-mail existente apareceria na tela.
    """
    with aplicacao.app_context():
        assert cadastro.registrar(dict(NOVO), aplicacao.villaju) is None


def test_confirmar_devolve_apenas_booleano(aplicacao, pasta_de_emails):
    with aplicacao.app_context():
        cadastro.registrar(dict(NOVO), aplicacao.villaju)

    token = _link_de_confirmacao(pasta_de_emails)

    with aplicacao.app_context():
        assert cadastro.confirmar(token, aplicacao.villaju) is True
        assert cadastro.confirmar(token, aplicacao.villaju) is False
        assert cadastro.confirmar("inexistente", aplicacao.villaju) is False


def test_a_falha_do_correio_nao_desfaz_a_conta(
    aplicacao, pasta_de_emails, monkeypatch
):
    """A razão de ter sido escolhida a Forma B.

    Um erro de SMTP não pode desfazer a conta já criada: ela existe
    inativa, e a recepção consegue ativá-la depois de falar com a pessoa.
    Na Forma A — senha definida pelo link —, um e-mail perdido deixaria a
    pessoa sem nada.
    """
    def explodir(*args, **kwargs):
        raise correio.ErroDeCorreio("servidor fora do ar")

    monkeypatch.setattr(correio, "enviar", explodir)

    with aplicacao.app_context():
        cadastro.registrar(dict(NOVO), aplicacao.villaju)

        conta = usuario_repo.buscar_por_login(
            usuario_repo.login_de_email(NOVO["email"])
        )

    assert conta is not None, "a conta precisa sobreviver à falha do correio"
    assert not conta["ativo"]


def test_o_token_nao_vai_para_o_log(aplicacao, pasta_de_emails, caplog):
    """Token em log é token vazado.

    A verificação é pelo prefixo do link e não pelo token em si: o token
    muda a cada execução, e procurar pelo valor exato exigiria capturá-lo
    antes — o que este teste não precisa fazer para provar o ponto.
    """
    import logging

    with caplog.at_level(logging.DEBUG):
        with aplicacao.app_context():
            cadastro.registrar(dict(NOVO), aplicacao.villaju)

    assert "/confirmar/" not in caplog.text, (
        "o link de confirmação apareceu no log"
    )
    assert SENHA not in caplog.text, "a senha apareceu no log"
