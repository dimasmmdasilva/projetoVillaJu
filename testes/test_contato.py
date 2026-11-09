"""Pedidos de contato: a única escrita que um desconhecido faz no sistema.

Dois assuntos, que o arquivo separa:

**A tabela é cifrada.** Decidido em 06/11, revendo a primeira versão do
schema, que guardava nome, e-mail, telefone e mensagem em texto simples.
O argumento de que pedido de contato é dado de menor sensibilidade não se
sustenta: é a mesma pessoa, com o mesmo nome e o mesmo telefone, e quem
obtiver acesso de leitura ao banco não vai achar a distinção relevante.

**O formulário não cria reserva nem hóspede.** Grava em `contato`, que é
tabela de quarentena. Os testes de isolamento verificam isso pelo que a
rota pública consegue alcançar, não só pelo que ela de fato fez.

As três defesas do formulário — token CSRF, limitação de taxa e
campo-armadilha — são o assunto do Bloco 3 e têm arquivo próprio.
"""

import datetime as dt
import re

import pytest

from app import create_app
from app.config import Configuracao
from app.dados import conexao, contato_repo
from app.dados.contato_repo import ErroDeContato

pytestmark = pytest.mark.banco

DUNAS = 5

PEDIDO = {
    "nome": "Helena Queirós",
    "email": "helena@exemplo.test",
    "telefone": "12 97777-3333",
    "mensagem": "Gostaria de saber a disponibilidade do Dunas em julho.",
    "chale_interesse_id": str(DUNAS),
    "data_entrada_desejada": "2027-07-10",
    "data_saida_desejada": "2027-07-15",
}


@pytest.fixture
def banco(conexao_banco):
    """Massa recarregada a cada teste: estes testes gravam e alteram."""
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
def visitante(banco):
    aplicacao = create_app(Configuracao(modo_teste=True))
    aplicacao.config["SESSION_COOKIE_SECURE"] = False
    return aplicacao.test_client()


def _aviso(resposta) -> str:
    achados = re.findall(
        r'<p class="aviso aviso-\w+">(.*?)</p>', resposta.data.decode(), re.S
    )
    return achados[0].strip() if achados else ""


def _bytes_da_linha(identificador) -> bytes:
    """Todos os valores da linha, concatenados, para varrer texto claro."""
    with conexao.obter_conexao() as ligacao:
        cursor = ligacao.cursor()
        cursor.execute("SELECT * FROM contato WHERE id = %s", (identificador,))
        linha = cursor.fetchone()
        cursor.close()

    return b"".join(
        valor if isinstance(valor, bytes) else str(valor).encode("utf-8")
        for valor in linha
        if valor is not None
    )


# ---------------------------------------------------------------------------
# A cifragem
# ---------------------------------------------------------------------------

def test_o_pedido_grava_e_volta_legivel(banco):
    identificador = contato_repo.criar(PEDIDO)
    lido = contato_repo.buscar_por_id(identificador)

    assert lido["nome"] == "Helena Queirós"
    assert lido["email"] == "helena@exemplo.test"
    assert lido["telefone"] == "12 97777-3333"
    assert lido["mensagem"].startswith("Gostaria de saber")
    assert lido["situacao"] == "novo"


def test_nada_do_pedido_fica_legivel_no_banco(banco):
    """A verificação central deste arquivo.

    Varre a linha inteira, não só as colunas `_cif`: um vazamento pelo
    índice de busca não altera nenhuma coluna cifrada, e uma varredura
    restrita a elas passaria batido. Foi assim que um defeito igual
    escapou na Etapa 3.
    """
    identificador = contato_repo.criar(PEDIDO)
    gravado = _bytes_da_linha(identificador)

    for claro in (b"Helena", b"Queir", b"helena@", b"97777", b"disponibilidade"):
        assert claro not in gravado, f"{claro!r} foi gravado em texto claro"


def test_o_mesmo_email_cifra_diferente_a_cada_vez(banco):
    """AES-GCM é não determinístico, e é por isso que o índice existe.

    Duas pessoas com o mesmo e-mail produzem ciphertext diferente — o que
    impede que alguém com acesso ao banco descubra repetições apenas
    comparando bytes.
    """
    primeiro = contato_repo.criar(PEDIDO)
    segundo = contato_repo.criar(PEDIDO)

    with conexao.obter_conexao() as ligacao:
        cursor = ligacao.cursor()
        cursor.execute(
            "SELECT email_cif, email_busca FROM contato WHERE id IN (%s, %s) "
            "ORDER BY id",
            (primeiro, segundo),
        )
        (cif_a, busca_a), (cif_b, busca_b) = cursor.fetchall()
        cursor.close()

    assert cif_a != cif_b, "o ciphertext não pode repetir"
    assert busca_a == busca_b, "o índice precisa repetir, ou a busca não acha"


def test_o_indice_acha_os_pedidos_da_mesma_pessoa(banco):
    """A recepção atende melhor sabendo se é a primeira vez."""
    primeiro = contato_repo.criar(PEDIDO)
    contato_repo.criar(PEDIDO)
    contato_repo.criar(dict(PEDIDO, email="outra@exemplo.test"))

    assert contato_repo.contatos_anteriores(primeiro, PEDIDO["email"]) == 1


def test_bloco_cifrado_movido_de_linha_nao_decifra(banco):
    """O dado adicional autenticado amarra o bloco à linha.

    Sem ele, quem tivesse escrita no banco copiaria o nome cifrado de um
    pedido para outro e a decifragem funcionaria, exibindo a pessoa
    errada sem que nada falhasse.
    """
    from app.seguranca.cripto import FalhaDeAutenticacao

    origem = contato_repo.criar(PEDIDO)
    destino = contato_repo.criar(dict(PEDIDO, email="outra@exemplo.test"))

    with conexao.obter_conexao() as ligacao:
        cursor = ligacao.cursor()
        cursor.execute(
            "UPDATE contato AS d "
            "JOIN contato AS o ON o.id = %s "
            "SET d.nome_cif = o.nome_cif, d.nome_nonce = o.nome_nonce, "
            "    d.nome_tag = o.nome_tag "
            "WHERE d.id = %s",
            (origem, destino),
        )
        cursor.close()

    with pytest.raises(FalhaDeAutenticacao):
        contato_repo.buscar_por_id(destino)

    # E a listagem não cai por causa dele: marca como não legível.
    marcados = [c for c in contato_repo.listar() if not c["legivel"]]
    assert any(c["id"] == destino for c in marcados)


# ---------------------------------------------------------------------------
# Validação no servidor
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "mudanca,trecho",
    [
        ({"nome": ""}, "nome é obrigatório"),
        ({"nome": "   "}, "nome é obrigatório"),
        ({"email": ""}, "email é obrigatório"),
        ({"email": "sem-arroba"}, "e-mail válido"),
        ({"email": "sem@dominio"}, "e-mail válido"),
        ({"nome": "x" * 151}, "no máximo 150"),
        ({"mensagem": "x" * 1001}, "no máximo 1000"),
        ({"data_entrada_desejada": "10/07/2027"}, "AAAA-MM-DD"),
        (
            {"data_entrada_desejada": "2027-07-15",
             "data_saida_desejada": "2027-07-10"},
            "posterior",
        ),
    ],
)
def test_dado_invalido_e_recusado_com_mensagem(banco, mudanca, trecho):
    with pytest.raises(ErroDeContato) as erro:
        contato_repo.criar(dict(PEDIDO, **mudanca))
    assert trecho in str(erro.value)


def test_o_pedido_aceita_vir_sem_chale_e_sem_datas(banco):
    """O caso mais comum: quem só quer saber o preço.

    Exigir período transformaria uma pergunta simples em formulário de
    reserva, e perderia o pedido de quem ainda está decidindo.
    """
    identificador = contato_repo.criar(
        {"nome": "Curioso", "email": "curioso@exemplo.test"}
    )
    lido = contato_repo.buscar_por_id(identificador)

    assert lido["chale_interesse_id"] is None
    assert lido["data_entrada_desejada"] is None
    assert lido["telefone"] is None


def test_chale_inexistente_e_recusado_com_mensagem(banco):
    with pytest.raises(ErroDeContato) as erro:
        contato_repo.criar(dict(PEDIDO, chale_interesse_id="9999"))
    assert "não existe" in str(erro.value)


def test_o_endereco_de_origem_e_gravado(banco):
    identificador = contato_repo.criar(PEDIDO, ip_origem="203.0.113.45")

    with conexao.obter_cursor() as cursor:
        cursor.execute(
            "SELECT INET6_NTOA(ip_origem) FROM contato WHERE id = %s",
            (identificador,),
        )
        assert cursor.fetchone()[0] == "203.0.113.45"


def test_endereco_malformado_nao_derruba_o_pedido(banco):
    """A origem serve à limitação de taxa, não à validade do pedido.

    Perder um pedido de reserva real porque o cabeçalho veio estranho
    seria o prejuízo maior.
    """
    identificador = contato_repo.criar(PEDIDO, ip_origem="nao-e-um-ip")
    assert contato_repo.buscar_por_id(identificador) is not None


# ---------------------------------------------------------------------------
# A tela pública
# ---------------------------------------------------------------------------

def test_o_formulario_abre(visitante):
    resposta = visitante.get("/contato")
    assert resposta.status_code == 200
    assert "Solicitar reserva" in resposta.data.decode()


def test_o_botao_da_vitrine_pre_seleciona_o_chale(visitante):
    """O visitante escolhe clicando no chalé que está vendo."""
    html = visitante.get("/").data.decode()
    assert f"/contato?chale={DUNAS}" in html

    formulario = visitante.get(f"/contato?chale={DUNAS}").data.decode()
    achado = re.search(
        rf'<option value="{DUNAS}"\s*\n?\s*selected', formulario
    )
    assert achado, "o chalé precisa chegar pré-selecionado"


def test_envio_valido_grava_e_confirma(visitante):
    resposta = visitante.post(
        "/contato", data=PEDIDO, follow_redirects=True
    )

    assert resposta.status_code == 200
    assert "Pedido enviado" in resposta.data.decode()

    pedidos = contato_repo.listar()
    assert any(c["email"] == PEDIDO["email"] for c in pedidos)


def test_envio_valido_redireciona_em_vez_de_responder_ao_post(visitante):
    """Recarregar a confirmação não pode reenviar o pedido."""
    resposta = visitante.post("/contato", data=PEDIDO)

    assert resposta.status_code == 302
    assert "/contato/enviado" in resposta.headers["Location"]


def test_envio_invalido_devolve_400_e_preserva_o_preenchido(visitante):
    resposta = visitante.post("/contato", data=dict(PEDIDO, email="errado"))

    assert resposta.status_code == 400
    assert "e-mail válido" in _aviso(resposta)
    # O que já foi digitado continua na tela: perder tudo por causa de um
    # campo é o modo mais rápido de perder o pedido junto.
    assert "Helena Queirós" in resposta.data.decode()


def test_a_tela_nao_vaza_erro_tecnico(visitante):
    resposta = visitante.post("/contato", data=dict(PEDIDO, email="errado"))
    texto = resposta.data.decode()

    for termo in ("Traceback", "mysql", "SELECT", "INSERT", "1452", "45000"):
        assert termo not in texto


# ---------------------------------------------------------------------------
# Isolamento: o que o formulário não alcança
# ---------------------------------------------------------------------------

def test_o_envio_nao_cria_reserva(visitante):
    from app.dados import reserva_repo

    antes = len(reserva_repo.listar())
    visitante.post("/contato", data=PEDIDO, follow_redirects=True)

    assert len(reserva_repo.listar()) == antes


def test_o_envio_nao_cria_hospede(visitante):
    """A diferença entre lead e hóspede, verificada.

    Se o formulário público escrevesse em `hospede`, o índice HMAC único
    viraria oráculo de enumeração: enviar um e-mail já cadastrado
    revelaria que a pessoa é hóspede da hospedaria.
    """
    from app.dados import hospede_repo

    antes = hospede_repo.contar()
    visitante.post("/contato", data=PEDIDO, follow_redirects=True)

    assert hospede_repo.contar() == antes


def test_o_pedido_nasce_sem_vinculo_com_hospede(visitante):
    visitante.post("/contato", data=PEDIDO, follow_redirects=True)

    novo = contato_repo.listar(situacao="novo")[0]
    assert novo["hospede_id"] is None


def test_o_formulario_nao_pede_documento(visitante):
    """Não se pede o CPF de quem ainda está perguntando o preço.

    É o fato que decidiu a separação entre `contato` e `hospede`:
    `hospede.documento_cif` é NOT NULL, e um formulário público não tem
    como preenchê-lo sem coletar dado sensível de um desconhecido.
    """
    html = visitante.get("/contato").data.decode()

    for termo in ("documento", "CPF", "cpf"):
        assert termo not in html


def test_o_envio_nao_cria_sessao(visitante):
    visitante.post("/contato", data=PEDIDO, follow_redirects=True)

    with visitante.session_transaction() as sessao:
        assert not dict(sessao)


# ---------------------------------------------------------------------------
# Ciclo de vida
# ---------------------------------------------------------------------------

def test_o_pedido_percorre_ate_convertido(banco):
    identificador = contato_repo.criar(PEDIDO)

    contato_repo.mudar_situacao(identificador, "em_atendimento")
    contato_repo.ligar_ao_hospede(identificador, 1)

    lido = contato_repo.buscar_por_id(identificador)
    assert lido["situacao"] == "convertido"
    assert lido["hospede_id"] == 1


@pytest.mark.parametrize("destino", ["convertido", "respondido"])
def test_pedido_descartado_nao_volta(banco, destino):
    identificador = contato_repo.criar(PEDIDO)
    contato_repo.mudar_situacao(identificador, "descartado")

    with pytest.raises(ErroDeContato) as erro:
        contato_repo.mudar_situacao(identificador, destino)
    assert "descartado" in str(erro.value)


def test_o_contato_sobrevive_a_remocao_do_hospede(banco):
    """`ON DELETE SET NULL`, e o motivo dele.

    O contato é o registro de que alguém pediu informação. Apagá-lo junto
    com o cadastro que ele originou destruiria a resposta a "de onde
    vieram os hóspedes deste verão?".
    """
    from app.dados import hospede_repo

    hospede = hospede_repo.criar(
        {"nome": "Helena Queirós", "documento": "321.654.987-00"}
    )
    identificador = contato_repo.criar(PEDIDO)
    contato_repo.ligar_ao_hospede(identificador, hospede)

    hospede_repo.excluir(hospede)

    lido = contato_repo.buscar_por_id(identificador)
    assert lido is not None, "o contato não pode ter sido apagado junto"
    assert lido["hospede_id"] is None


def test_o_contato_sobrevive_a_remocao_do_chale(banco):
    identificador = contato_repo.criar(PEDIDO)

    with conexao.obter_conexao() as ligacao:
        cursor = ligacao.cursor()
        cursor.execute("DELETE FROM tarifa WHERE chale_id = %s", (DUNAS,))
        cursor.execute("DELETE FROM reserva WHERE chale_id = %s", (DUNAS,))
        cursor.execute("DELETE FROM chale WHERE id = %s", (DUNAS,))
        cursor.close()

    lido = contato_repo.buscar_por_id(identificador)
    assert lido is not None
    assert lido["chale_interesse_id"] is None


def test_a_listagem_filtra_por_situacao(banco):
    contato_repo.criar(PEDIDO)
    segundo = contato_repo.criar(dict(PEDIDO, email="outra@exemplo.test"))
    contato_repo.mudar_situacao(segundo, "descartado")

    novos = contato_repo.listar(situacao="novo")
    descartados = contato_repo.listar(situacao="descartado")

    # A massa fictícia já traz um pedido descartado, então o filtro não
    # devolve só o que este teste criou — devolve todos os descartados,
    # que é justamente o que a tela de atendimento precisa.
    assert all(c["situacao"] == "novo" for c in novos)
    assert all(c["situacao"] == "descartado" for c in descartados)
    assert segundo in [c["id"] for c in descartados]
    assert segundo not in [c["id"] for c in novos]


def test_a_contagem_por_situacao_bate_com_a_massa(banco):
    contagem = contato_repo.contar_por_situacao()
    assert sum(contagem.values()) == len(contato_repo.listar())


def test_o_carimbo_de_tempo_vem_da_trigger(banco):
    identificador = contato_repo.criar(PEDIDO)
    criado = contato_repo.buscar_por_id(identificador)["criado_em"]

    assert isinstance(criado, dt.datetime)
    assert abs((dt.datetime.now() - criado).total_seconds()) < 60


def test_pedido_recusado_preserva_o_chale_escolhido(visitante):
    """O mesmo defeito de tipo da tela interna, na face pública.

    O visitante clicou no chalé que queria, preencheu o formulário, errou
    o e-mail — e perdia a escolha, tendo de procurar o chalé outra vez na
    lista. Encontrado em teste prático da interface.
    """
    resposta = visitante.post(
        "/contato",
        data=dict(PEDIDO, email="errado-sem-arroba"),
    )
    html = resposta.data.decode()

    assert resposta.status_code == 400
    assert re.search(rf'<option value="{DUNAS}"\s*\n?\s*selected', html), (
        "o chalé escolhido se perdeu no envio recusado"
    )
    assert "Helena Queirós" in html
    assert PEDIDO["data_entrada_desejada"] in html
