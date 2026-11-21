"""Pedidos de contato: a tabela que o formulário público gravava.

Desde o Bloco 17 o formulário não existe mais; a tabela e o repositório
ficam, com os pedidos já gravados. O que segue continua valendo para eles.

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

import pytest

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
# A tela pública saiu no Bloco 17
# ---------------------------------------------------------------------------
# O formulário público de contato e a tela "Contatos" da equipe foram
# removidos (decisão do Dimas em 20/11): quem tem reserva conversa com a
# pousada pelo painel, e quem não tem vê os contatos em "Fale com a
# pousada". A tabela e este repositório ficam — os pedidos já gravados não
# se apagam, e `scripts/regerar_dados_pessoais.py` ainda os regrava. Os
# testes de que as rotas sumiram estão em `test_conversa.py`.

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
