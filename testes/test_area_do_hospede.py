"""O hóspede vê apenas as próprias reservas, e o CRUD de hóspede completo.

Este arquivo cobre os dois assuntos do último bloco da Etapa 3:

**A área do hóspede.** O plano lista como caso obrigatório "hóspede vê
apenas as próprias reservas". Não basta a tela mostrar as reservas certas:
é preciso que não exista caminho pelo qual ela mostre as de outro. Os
testes aqui atacam por três lados — o endereço sem parâmetro, a lista
completa que o hóspede não alcança, e o detalhe de uma reserva alheia.

**O CRUD de hóspede.** Edição e exclusão, com a cifragem no caminho: o
dado editado precisa voltar legível na tela e ilegível no banco. A
exclusão precisa ser recusada para quem tem reserva — e a recusa precisa
dizer por quê, não devolver erro de integridade referencial.
"""

import re

import pytest

from app import create_app
from app.config import Configuracao
from app.dados import conexao, hospede_repo, reserva_repo

pytestmark = pytest.mark.banco

ADMIN = ("admin", "Villa@Admin2026")
RECEPCAO = ("recepcao", "Villa@Recep2026")
HOSPEDE = ("joao", "Villa@Hospede2026")

# A conta 'joao' é vinculada ao hóspede 1 na massa de dados.
HOSPEDE_DO_JOAO = 1


@pytest.fixture
def banco(conexao_banco):
    """Massa recarregada a cada teste: estes testes apagam e editam."""
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
    yield cursor
    conexao.encerrar_pool()


@pytest.fixture
def cliente(banco):
    aplicacao = create_app(Configuracao(modo_teste=True))

    def entrar(credenciais=HOSPEDE):
        sessao = aplicacao.test_client()
        login, senha = credenciais
        sessao.post("/login", data={"login": login, "senha": senha})
        return sessao

    return entrar


def _aviso(resposta) -> str:
    achados = re.findall(
        r'<p class="aviso aviso-(?:erro|sucesso)">(.*?)</p>',
        resposta.data.decode(),
        re.S,
    )
    return achados[0].strip() if achados else ""


def _numeros_de_reserva(html: str) -> set:
    """Os identificadores de reserva que a tabela da página exibe.

    Só a primeira célula de cada linha. A primeira versão desta função
    varria toda célula numérica da tabela, e as colunas de noites e de
    pessoas entravam no conjunto como se fossem identificadores — o que
    fazia o teste de vazamento acusar interseção por coincidência
    aritmética, não por reserva alheia exibida.
    """
    corpo = re.search(r"<tbody>(.*?)</tbody>", html, re.S)
    if not corpo:
        return set()

    numeros = set()
    for linha in re.findall(r"<tr>(.*?)</tr>", corpo.group(1), re.S):
        primeira = re.search(r"<td>\s*(\d+)\s*</td>", linha)
        if primeira:
            numeros.add(int(primeira.group(1)))
    return numeros


def _hospede_existe(identificador) -> bool:
    """Existência por SQL, sem decifrar.

    Os hóspedes da massa fictícia têm `RANDOM_BYTES` nas colunas cifradas,
    e `buscar_por_id` recusa lê-los — corretamente. Para saber se a linha
    continua lá, basta contá-la.
    """
    with conexao.obter_conexao() as ligacao:
        cursor = ligacao.cursor()
        cursor.execute("SELECT COUNT(*) FROM hospede WHERE id = %s", (identificador,))
        quantas = cursor.fetchone()[0]
        cursor.close()
    return quantas == 1


# ---------------------------------------------------------------------------
# O caso obrigatório: o hóspede vê apenas as próprias reservas
# ---------------------------------------------------------------------------

def test_hospede_ve_as_proprias_reservas(cliente):
    resposta = cliente().get("/reservas/minhas")

    assert resposta.status_code == 200

    minhas = {
        reserva["id"] for reserva in reserva_repo.listar_por_hospede(HOSPEDE_DO_JOAO)
    }
    assert minhas, "a massa de dados precisa dar reservas ao hóspede 1"
    assert minhas <= _numeros_de_reserva(resposta.data.decode())


def test_hospede_nao_ve_reserva_de_outro(cliente):
    """O centro do caso: o que NÃO pode aparecer.

    Um filtro escrito ao contrário, ou esquecido, passaria no teste
    anterior — as reservas do próprio hóspede estariam todas lá, junto com
    as dos outros.
    """
    html = cliente().get("/reservas/minhas").data.decode()
    exibidos = _numeros_de_reserva(html)

    alheias = {
        reserva["id"]
        for reserva in reserva_repo.listar()
        if reserva["hospede_id"] != HOSPEDE_DO_JOAO
    }
    assert alheias, "a massa precisa ter reservas de outros hóspedes"
    assert not (exibidos & alheias)


def test_a_tela_do_hospede_nao_recebe_identificador_pela_url(cliente):
    """Não há número no endereço para trocar.

    É a diferença entre esta tela e o furo de autorização mais comum em
    sistema web: `/reservas/minhas?hospede_id=2` não existe como caminho,
    e o parâmetro é simplesmente ignorado.
    """
    primeira = cliente().get("/reservas/minhas").data.decode()
    segunda = cliente().get("/reservas/minhas?hospede_id=2").data.decode()

    assert _numeros_de_reserva(primeira) == _numeros_de_reserva(segunda)


def test_hospede_nao_alcanca_a_lista_completa(cliente):
    assert cliente().get("/reservas/").status_code == 403


def test_hospede_nao_alcanca_o_detalhe_de_reserva_alheia(cliente):
    alheia = next(
        reserva
        for reserva in reserva_repo.listar()
        if reserva["hospede_id"] != HOSPEDE_DO_JOAO
    )
    assert cliente().get(f"/reservas/{alheia['id']}").status_code == 403


def test_hospede_nao_alcanca_nem_o_detalhe_da_propria_reserva(cliente):
    """Por ora, não.

    A rota de detalhe permite mudar situação e editar. Abri-la ao hóspede
    exigiria separar leitura de escrita dentro dela, e o plano da Etapa 3
    não prevê isso — o valor da estadia já aparece na linha da tela dele.
    """
    minha = reserva_repo.listar_por_hospede(HOSPEDE_DO_JOAO)[0]
    assert cliente().get(f"/reservas/{minha['id']}").status_code == 403


def test_hospede_nao_alcanca_o_cadastro_de_hospedes(cliente):
    sessao = cliente()
    assert sessao.get("/hospedes/").status_code == 403
    assert sessao.get("/hospedes/novo").status_code == 403
    assert sessao.post(f"/hospedes/{HOSPEDE_DO_JOAO}/excluir").status_code == 403


def test_recepcao_nao_usa_a_area_do_hospede(cliente):
    """A rota é do perfil `hospede`, e de mais ninguém.

    A recepção tem a lista completa; deixá-la entrar aqui criaria uma
    segunda tela de reservas com regra de visibilidade própria, e duas
    regras para a mesma coisa divergem.
    """
    assert cliente(RECEPCAO).get("/reservas/minhas").status_code == 403
    assert cliente(ADMIN).get("/reservas/minhas").status_code == 403


def test_a_tela_do_hospede_mostra_o_valor_da_estadia(cliente):
    html = cliente().get("/reservas/minhas").data.decode()
    assert "R$" in html


def test_o_banco_nao_deixa_a_conta_de_hospede_perder_o_vinculo(banco):
    """A primeira linha de defesa é a trigger, e ela não negocia.

    Este teste nasceu de uma tentativa frustrada: eu quis produzir uma
    conta de hóspede sem `hospede_id` para exercitar o ramo defensivo da
    tela, e o banco recusou o UPDATE — feito por root, por fora da
    aplicação, sem passar por validação alguma do Python. É exatamente o
    argumento central do projeto, verificado por acidente: a regra está no
    banco, e quem contorna a aplicação continua esbarrando nela.
    """
    import mysql.connector

    with conexao.obter_conexao() as ligacao:
        cursor = ligacao.cursor()
        with pytest.raises(mysql.connector.Error) as erro:
            cursor.execute(
                "UPDATE usuario SET hospede_id = NULL WHERE login = 'joao'"
            )
        cursor.close()

    assert erro.value.errno == 1644
    assert "vinculado" in str(erro.value)


def test_conta_sem_vinculo_nao_mostra_reserva_alguma(banco):
    """O ramo defensivo da tela, com a trigger removida.

    O estado não é alcançável enquanto a trigger existe — o teste anterior
    prova isso. Mas a tela não pode depender de a trigger estar lá: se um
    dia alguém recriar o schema sem ela, ou restaurar um dump antigo, a
    resposta certa é não mostrar nada. Mostrar tudo seria o oposto.

    A trigger é derrubada só aqui; a massa é recarregada a cada teste.
    """
    with conexao.obter_conexao() as ligacao:
        cursor = ligacao.cursor()
        cursor.execute("DROP TRIGGER trg_usuario_antes_alterar")
        cursor.execute("UPDATE usuario SET hospede_id = NULL WHERE login = 'joao'")
        cursor.close()

    aplicacao = create_app(Configuracao(modo_teste=True))
    sessao = aplicacao.test_client()
    sessao.post("/login", data={"login": HOSPEDE[0], "senha": HOSPEDE[1]})
    resposta = sessao.get("/reservas/minhas")

    assert resposta.status_code == 200
    assert not _numeros_de_reserva(resposta.data.decode())
    assert "não está vinculada" in resposta.data.decode()


# ---------------------------------------------------------------------------
# Edição de hóspede
# ---------------------------------------------------------------------------

def test_formulario_de_edicao_vem_preenchido_e_decifrado(cliente):
    identificador = hospede_repo.criar(
        {
            "nome": "Marcos Araújo",
            "documento": "111.222.333-44",
            "telefone": "12 99999-1111",
            "email": "marcos@exemplo.test",
        }
    )

    html = cliente(RECEPCAO).get(f"/hospedes/{identificador}/editar").data.decode()

    assert "Marcos Araújo" in html
    assert "111.222.333-44" in html
    assert "marcos@exemplo.test" in html


def test_edicao_regrava_e_o_banco_continua_ilegivel(cliente):
    identificador = hospede_repo.criar(
        {"nome": "Rita Nogueira", "documento": "555.666.777-88"}
    )

    resposta = cliente(RECEPCAO).post(
        f"/hospedes/{identificador}/editar",
        data={
            "nome": "Rita Nogueira Prado",
            "documento": "555.666.777-88",
            "telefone": "12 98888-2222",
            "email": "rita@exemplo.test",
        },
        follow_redirects=True,
    )

    assert resposta.status_code == 200

    gravado = hospede_repo.buscar_por_id(identificador)
    assert gravado["nome"] == "Rita Nogueira Prado"
    assert gravado["telefone"] == "12 98888-2222"

    # E no banco, os bytes. A edição não pode ter gravado texto simples.
    with conexao.obter_conexao() as ligacao:
        cursor = ligacao.cursor()
        cursor.execute(
            "SELECT nome_cif, telefone_cif FROM hospede WHERE id = %s",
            (identificador,),
        )
        nome_cif, telefone_cif = cursor.fetchone()
        cursor.close()

    assert b"Rita" not in nome_cif
    assert b"Nogueira" not in nome_cif
    assert b"98888" not in telefone_cif


def test_edicao_atualiza_o_indice_de_busca(cliente):
    """Trocar o e-mail precisa trocar o HMAC, ou a busca fica no valor antigo."""
    identificador = hospede_repo.criar(
        {
            "nome": "Paulo Rezende",
            "documento": "999.888.777-66",
            "email": "antigo@exemplo.test",
        }
    )

    cliente(RECEPCAO).post(
        f"/hospedes/{identificador}/editar",
        data={
            "nome": "Paulo Rezende",
            "documento": "999.888.777-66",
            "telefone": "",
            "email": "novo@exemplo.test",
        },
        follow_redirects=True,
    )

    assert hospede_repo.buscar_por_email("novo@exemplo.test") is not None
    assert hospede_repo.buscar_por_email("antigo@exemplo.test") is None


def test_edicao_recusa_documento_de_outro_hospede(cliente):
    primeiro = hospede_repo.criar(
        {"nome": "Ana Prado", "documento": "123.123.123-12"}
    )
    segundo = hospede_repo.criar(
        {"nome": "Bruno Lemos", "documento": "321.321.321-32"}
    )

    resposta = cliente(RECEPCAO).post(
        f"/hospedes/{segundo}/editar",
        data={
            "nome": "Bruno Lemos",
            "documento": "123.123.123-12",
            "telefone": "",
            "email": "",
        },
    )

    assert resposta.status_code == 409
    assert "documento ou e-mail" in _aviso(resposta)

    # E o registro do primeiro continua intacto.
    assert hospede_repo.buscar_por_id(primeiro)["nome"] == "Ana Prado"


def test_edicao_sem_nome_e_recusada(cliente):
    identificador = hospede_repo.criar(
        {"nome": "Clara Vidal", "documento": "444.444.444-44"}
    )

    resposta = cliente(RECEPCAO).post(
        f"/hospedes/{identificador}/editar",
        data={"nome": "", "documento": "444.444.444-44", "telefone": "", "email": ""},
    )

    assert resposta.status_code == 400
    assert hospede_repo.buscar_por_id(identificador)["nome"] == "Clara Vidal"


def test_editar_hospede_inexistente_devolve_404(cliente):
    assert cliente(RECEPCAO).get("/hospedes/9999/editar").status_code == 404


# ---------------------------------------------------------------------------
# Exclusão de hóspede
# ---------------------------------------------------------------------------

def test_hospede_sem_reserva_e_excluido(cliente):
    identificador = hospede_repo.criar(
        {"nome": "Sem Reserva", "documento": "000.111.222-33"}
    )

    resposta = cliente(ADMIN).post(
        f"/hospedes/{identificador}/excluir", follow_redirects=True
    )

    assert resposta.status_code == 200
    assert hospede_repo.buscar_por_id(identificador) is None


def test_hospede_com_reserva_nao_e_excluido(cliente):
    """O histórico de ocupação depende do cadastro.

    A chave estrangeira recusaria de qualquer modo; o que se verifica aqui
    é que a tela diz quantas reservas existem, em vez de devolver erro de
    integridade referencial.
    """
    resposta = cliente(ADMIN).post(f"/hospedes/{HOSPEDE_DO_JOAO}/excluir")

    assert resposta.status_code == 409
    assert "reserva" in _aviso(resposta)
    assert _hospede_existe(HOSPEDE_DO_JOAO)


def test_recepcionista_nao_exclui_hospede(cliente):
    identificador = hospede_repo.criar(
        {"nome": "Alvo Qualquer", "documento": "777.777.777-77"}
    )

    assert (
        cliente(RECEPCAO).post(f"/hospedes/{identificador}/excluir").status_code == 403
    )
    assert hospede_repo.buscar_por_id(identificador) is not None


def test_exclusao_nao_atende_a_get(cliente):
    """Um link de exclusão seria disparado por qualquer pré-carregamento."""
    identificador = hospede_repo.criar(
        {"nome": "Por Get", "documento": "666.666.666-66"}
    )

    assert cliente(ADMIN).get(f"/hospedes/{identificador}/excluir").status_code == 405
    assert hospede_repo.buscar_por_id(identificador) is not None


def test_a_lista_oferece_o_caminho_da_edicao(cliente):
    identificador = hospede_repo.criar(
        {"nome": "Teresa Campos", "documento": "888.888.888-88"}
    )

    html = cliente(RECEPCAO).get("/hospedes/").data.decode()

    assert f"/hospedes/{identificador}/editar" in html


def test_registro_ilegivel_nao_oferece_edicao(cliente):
    """Não se regrava por cima do que não se conseguiu ler.

    Os hóspedes da massa fictícia têm bytes aleatórios nas colunas
    cifradas. A lista os marca como não legíveis e não oferece o link: um
    formulário vazio salvo em cima apagaria a evidência do problema.
    """
    html = cliente(RECEPCAO).get("/hospedes/").data.decode()

    assert "não legível" in html
    assert f"/hospedes/{HOSPEDE_DO_JOAO}/editar" not in html


def test_edicao_de_registro_ilegivel_e_recusada(cliente):
    resposta = cliente(RECEPCAO).get(
        f"/hospedes/{HOSPEDE_DO_JOAO}/editar", follow_redirects=True
    )

    assert resposta.status_code == 200
    assert "integridade" in resposta.data.decode()
