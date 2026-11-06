"""O critério de conclusão da Etapa 4, executado do começo ao fim.

O plano define o critério em uma frase:

> A página pública roda com a sessão administrativa encerrada e não expõe
> nenhuma informação além dos chalés.

Duas metades, e a segunda é a que importa. A primeira se verifica abrindo
a página; a segunda exige perguntar o que a face pública **consegue**
alcançar, não apenas o que ela mostrou desta vez, com esta massa.

O arquivo tem três partes:

**O percurso.** Um visitante anônimo chega ao sítio, escolhe um chalé,
pede reserva — e a recepção, do outro lado, converte o pedido em cadastro.
É a jornada completa das duas faces, pelo HTTP, contra o banco real.

**O isolamento.** Nenhuma rota pública pode alcançar `hospede`, `reserva`,
`usuario` ou `log_auditoria`. Verificado pela árvore de importações,
incluindo o que os módulos importados importam por sua vez — porque um
repositório proibido alcançado de segunda mão é alcançado do mesmo jeito.

**A separação das sessões.** A face pública não compartilha sessão com a
área autenticada.
"""

import ast
import pathlib
import re

import pytest

from app import create_app
from app.config import Configuracao
from app.dados import conexao, contato_repo, hospede_repo, reserva_repo

pytestmark = pytest.mark.banco

RAIZ = pathlib.Path(__file__).resolve().parents[1]

RECEPCAO = ("recepcao", "Villa@Recep2026")
DUNAS = 5

VISITANTE = {
    "nome": "Helena Queirós",
    "email": "helena@exemplo.test",
    "telefone": "12 97777-3333",
    "mensagem": "Somos duas pessoas. O Dunas está livre em julho?",
    "data_entrada_desejada": "2027-07-10",
    "data_saida_desejada": "2027-07-15",
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

    conexao.iniciar_pool(Configuracao(modo_teste=True))
    app = create_app(Configuracao(modo_teste=True))
    app.config["SESSION_COOKIE_SECURE"] = False
    yield app
    conexao.encerrar_pool()


# ---------------------------------------------------------------------------
# O percurso
# ---------------------------------------------------------------------------

def test_percurso_do_visitante_ao_cadastro_de_hospede(aplicacao):
    """Um teste só, longo, porque é um percurso.

    Os outros arquivos verificam peças. Cada peça pode estar correta e o
    percurso quebrar: um botão que aponta para a rota errada, um campo
    cujo nome não é o que a rota lê, um redirecionamento que perde o
    identificador. Nada disso aparece em teste de unidade, e tudo isso
    aparece na demonstração.
    """
    visitante = aplicacao.test_client()

    # --- 1. Chega ao sítio, sem sessão -------------------------------
    inicio = visitante.get("/")
    assert inicio.status_code == 200

    pagina = inicio.data.decode()
    assert "Dunas" in pagina
    assert f"/contato?chale={DUNAS}" in pagina, (
        "a vitrine precisa oferecer o pedido por chalé"
    )

    # --- 2. Clica em Solicitar reserva no Dunas ----------------------
    formulario = visitante.get(f"/contato?chale={DUNAS}")
    assert formulario.status_code == 200

    html = formulario.data.decode()
    assert re.search(rf'<option value="{DUNAS}"\s*\n?\s*selected', html), (
        "o chalé escolhido precisa chegar pré-selecionado"
    )
    assert "csrf_token" in html
    assert 'name="assunto"' in html, "o campo-armadilha precisa estar lá"

    # O formulário público não pede documento: não se pede o CPF de quem
    # ainda está perguntando o preço.
    assert "documento" not in html

    # --- 3. Envia o pedido -------------------------------------------
    envio = visitante.post(
        "/contato",
        data=dict(VISITANTE, chale_interesse_id=str(DUNAS)),
    )
    assert envio.status_code == 302
    assert "/contato/enviado" in envio.headers["Location"]

    confirmacao = visitante.get(envio.headers["Location"]).data.decode()
    assert "Pedido enviado" in confirmacao
    assert "Nada foi reservado ainda" in confirmacao, (
        "o visitante precisa saber que ainda não há reserva"
    )

    # --- 4. Nada foi criado além do pedido ---------------------------
    assert not reserva_repo.listar_por_hospede(0)
    pedido = next(
        c for c in contato_repo.listar() if c["email"] == VISITANTE["email"]
    )
    assert pedido["situacao"] == "novo"
    assert pedido["hospede_id"] is None
    assert pedido["chale_interesse_id"] == DUNAS

    # E o visitante continua anônimo: a sessão guarda o token CSRF e
    # nada mais. Ver CHAVES_PERMITIDAS, mais abaixo.
    with visitante.session_transaction() as sessao:
        assert "usuario_id" not in dict(sessao)
        assert set(dict(sessao)) <= {"csrf_token"}

    # --- 5. A recepção entra e vê o pedido ---------------------------
    recepcao = aplicacao.test_client()
    pagina_login = recepcao.get("/login").data.decode()
    dados = {"login": RECEPCAO[0], "senha": RECEPCAO[1]}
    achado = re.search(r'name="csrf_token" value="([^"]+)"', pagina_login)
    if achado:
        dados["csrf_token"] = achado.group(1)
    recepcao.post("/login", data=dados)

    lista = recepcao.get("/contatos/?situacao=novo").data.decode()
    assert "Helena Queirós" in lista
    assert "Dunas" in lista

    # --- 6. Abre o pedido --------------------------------------------
    detalhe = recepcao.get(f"/contatos/{pedido['id']}").data.decode()
    assert "helena@exemplo.test" in detalhe
    assert "O Dunas está livre em julho?" in detalhe
    assert "primeira vez" in detalhe

    # --- 7. Marca em atendimento e converte --------------------------
    recepcao.post(
        f"/contatos/{pedido['id']}/situacao",
        data={"situacao": "em_atendimento"},
        follow_redirects=True,
    )

    cadastro = recepcao.get(
        f"/contatos/{pedido['id']}/converter"
    ).data.decode()
    assert 'value="Helena Queirós"' in cadastro
    assert 'value="12 97777-3333"' in cadastro

    documento = re.search(r'id="documento"[^>]*value="([^"]*)"', cadastro)
    assert documento and documento.group(1) == "", (
        "o documento é o único campo que a recepção precisa obter"
    )

    recepcao.post(
        f"/contatos/{pedido['id']}/converter",
        data={
            "nome": "Helena Queirós",
            "documento": "321.654.987-00",
            "telefone": "12 97777-3333",
            "email": "helena@exemplo.test",
        },
        follow_redirects=True,
    )

    # --- 8. O pedido virou cadastro, e continua existindo ------------
    convertido = contato_repo.buscar_por_id(pedido["id"])
    assert convertido["situacao"] == "convertido"
    assert convertido["hospede_id"] is not None

    hospede = hospede_repo.buscar_por_id(convertido["hospede_id"])
    assert hospede["nome"] == "Helena Queirós"
    assert hospede["documento"] == "321.654.987-00"

    # --- 9. Nada disso ficou legível no banco ------------------------
    with conexao.obter_conexao() as ligacao:
        cursor = ligacao.cursor()
        cursor.execute("SELECT * FROM contato WHERE id = %s", (pedido["id"],))
        linha_contato = cursor.fetchone()
        cursor.execute("SELECT * FROM hospede WHERE id = %s", (hospede["id"],))
        linha_hospede = cursor.fetchone()
        cursor.close()

    def _bytes(linha):
        return b"".join(
            v if isinstance(v, bytes) else str(v).encode("utf-8")
            for v in linha
            if v is not None
        )

    gravado = _bytes(linha_contato) + _bytes(linha_hospede)
    for claro in (b"Helena", b"Queir", b"helena@", b"97777", b"321.654"):
        assert claro not in gravado, f"{claro!r} ficou legível no banco"

    # --- 10. A recepção sai, e a vitrine continua de pé --------------
    recepcao.post("/logout", follow_redirects=True)

    assert recepcao.get("/contatos/", follow_redirects=False).status_code == 302
    assert recepcao.get("/").status_code == 200


# ---------------------------------------------------------------------------
# Isolamento: o que a face pública consegue alcançar
# ---------------------------------------------------------------------------

MODULOS_PUBLICOS = ("app/rotas/publico.py",)

REPOSITORIOS_PROIBIDOS = {
    "hospede_repo",
    "reserva_repo",
    "usuario_repo",
    "auditoria_repo",
    "tarifa_repo",
    "calculo",
}


def _importados(caminho: pathlib.Path) -> set:
    """Módulos da aplicação que este arquivo importa.

    `from app.dados import contato_repo` precisa virar
    `app.dados.contato_repo`, e não `app.dados` — a primeira versão desta
    função guardava só o módulo do `from`, que é um pacote e não existe
    como arquivo. A travessia parava no primeiro nível, e o teste de
    isolamento passava tendo olhado dois arquivos.

    Quem acusou foi `test_o_alcance_cobre_mais_de_um_nivel`, escrito
    justamente para que uma travessia quebrada não passasse por
    verificação.
    """
    arvore = ast.parse(caminho.read_text(encoding="utf-8"))
    nomes = set()

    for no in ast.walk(arvore):
        if isinstance(no, ast.ImportFrom):
            if not no.module:
                continue
            nomes.add(no.module)
            # `from app.dados import X` alcança `app.dados.X`.
            for alvo in no.names:
                nomes.add(f"{no.module}.{alvo.name}")
        elif isinstance(no, ast.Import):
            for alvo in no.names:
                nomes.add(alvo.name)

    return nomes


def _alcance(inicial: str) -> dict:
    """Todos os módulos da aplicação alcançáveis a partir de um deles.

    Percorre as importações em largura. Um repositório proibido alcançado
    de segunda mão é alcançado do mesmo jeito: se `publico.py` importa
    um módulo que importa `hospede_repo`, a rota pública tem como chegar
    lá, e basta uma linha mal escrita para que chegue.
    """
    visitados = {}
    fila = [(inicial, [inicial])]

    while fila:
        atual, caminho_ate_aqui = fila.pop(0)
        if atual in visitados:
            continue

        arquivo = RAIZ / atual
        if not arquivo.exists():
            continue

        visitados[atual] = caminho_ate_aqui

        for nome in _importados(arquivo):
            if not nome.startswith("app."):
                continue
            proximo = nome.replace(".", "/") + ".py"
            if (RAIZ / proximo).exists():
                fila.append((proximo, caminho_ate_aqui + [proximo]))

    return visitados


@pytest.mark.parametrize("modulo", MODULOS_PUBLICOS)
def test_a_face_publica_nao_alcanca_repositorio_proibido(modulo):
    """A regra verificada na árvore inteira, não só no primeiro nível.

    O teste do Bloco 1 olhava apenas as importações diretas de
    `publico.py`. Este segue a cadeia: se um módulo importado importar
    `hospede_repo`, a verificação acusa e mostra o caminho.
    """
    alcancaveis = _alcance(modulo)

    culpados = []
    for caminho, trilha in alcancaveis.items():
        nome = pathlib.Path(caminho).stem
        if nome in REPOSITORIOS_PROIBIDOS:
            culpados.append(" -> ".join(trilha))

    assert not culpados, (
        "a face pública alcança repositório proibido:\n  "
        + "\n  ".join(culpados)
    )


def test_o_alcance_cobre_mais_de_um_nivel():
    """Prova de que a travessia acima funciona.

    Sem este teste, uma função `_alcance` que devolvesse apenas o módulo
    inicial passaria no anterior sem verificar coisa alguma.
    """
    alcancaveis = _alcance("app/rotas/publico.py")

    assert "app/dados/contato_repo.py" in alcancaveis, "primeiro nível"
    assert "app/dados/conexao.py" in alcancaveis, (
        "segundo nível: contato_repo importa conexao"
    )
    assert len(alcancaveis) > 3


def test_a_area_interna_alcanca_o_que_a_publica_nao(aplicacao):
    """O contraste que dá sentido ao teste anterior.

    Se nenhum módulo do sistema importasse `hospede_repo`, a verificação
    de isolamento passaria sem provar nada sobre a face pública.
    """
    alcancaveis = _alcance("app/rotas/reservas.py")
    nomes = {pathlib.Path(c).stem for c in alcancaveis}

    assert "reserva_repo" in nomes
    assert nomes & REPOSITORIOS_PROIBIDOS, (
        "a área interna precisa alcançar os repositórios que a pública não"
    )


# ---------------------------------------------------------------------------
# As sessões não se misturam
# ---------------------------------------------------------------------------

# Chaves que a face pública pode legitimamente guardar na sessão.
#
# Apenas o token CSRF. Ele **precisa** estar ali: a proteção funciona
# comparando o que a página trouxe com o que o servidor guardou, e sem
# estado do lado do servidor não há comparação a fazer.
#
# A primeira versão deste teste exigia sessão vazia e falhou por isso.
# Exigir vazio seria exigir que a defesa contra CSRF não existisse.
CHAVES_PERMITIDAS = {"csrf_token"}


def test_a_face_publica_nao_guarda_identidade_na_sessao(aplicacao):
    """O visitante é anônimo e continua anônimo.

    O token CSRF é o único estado que a face pública mantém. Nada que
    identifique a pessoa, nada que a siga entre visitas, nada que a área
    autenticada reconheça.
    """
    visitante = aplicacao.test_client()

    for rota in ("/", "/contato", "/contato/enviado"):
        visitante.get(rota)

    visitante.post("/contato", data=dict(VISITANTE, chale_interesse_id="5"))

    with visitante.session_transaction() as sessao:
        guardado = dict(sessao)

    excedente = set(guardado) - CHAVES_PERMITIDAS
    assert not excedente, (
        f"a face pública guardou na sessão: {sorted(excedente)}"
    )

    # E, em especial, nada que a área autenticada reconheceria.
    assert "usuario_id" not in guardado


def test_a_sessao_do_visitante_nao_abre_a_area_interna(aplicacao):
    """A prova de que o token de visitante não vira credencial."""
    visitante = aplicacao.test_client()
    visitante.get("/contato")
    visitante.post("/contato", data=dict(VISITANTE, chale_interesse_id="5"))

    for rota in ("/painel", "/contatos/", "/reservas/", "/hospedes/"):
        resposta = visitante.get(rota, follow_redirects=False)
        assert resposta.status_code in (302, 401, 403), (
            f"{rota} respondeu {resposta.status_code} a um visitante"
        )


def test_sessao_da_equipe_nao_muda_o_que_a_vitrine_mostra(aplicacao):
    """Nenhuma decisão de conteúdo público depende de quem está olhando.

    Só o link da equipe muda. Se o corpo da página variasse por sessão,
    haveria ali lógica de autorização — e a face pública não deveria ter
    nenhuma.
    """
    anonimo = aplicacao.test_client().get("/").data.decode()

    equipe = aplicacao.test_client()
    pagina = equipe.get("/login").data.decode()
    dados = {"login": RECEPCAO[0], "senha": RECEPCAO[1]}
    achado = re.search(r'name="csrf_token" value="([^"]+)"', pagina)
    if achado:
        dados["csrf_token"] = achado.group(1)
    equipe.post("/login", data=dados)
    autenticado = equipe.get("/").data.decode()

    for chale in ("Embaré", "Camburi", "Asturias", "Boracéia", "Dunas"):
        assert (chale in anonimo) == (chale in autenticado)

    # E a navegação interna não vaza para a vitrine.
    for rota in ("/reservas/", "/hospedes/", "/contatos/", "/auditoria"):
        assert rota not in autenticado


def test_a_vitrine_nao_mostra_dado_de_pedido_algum(aplicacao):
    """Os pedidos são de terceiros, e a vitrine é aberta ao mundo."""
    contato_repo.criar(dict(VISITANTE, chale_interesse_id=str(DUNAS)))

    html = aplicacao.test_client().get("/").data.decode()

    for sigiloso in ("Helena", "helena@exemplo.test", "97777"):
        assert sigiloso not in html
