"""Quem vai se hospedar: número de pessoas e acompanhantes — Etapa 5, Bloco 15.

As decisões, aceitas em 13/11/2026:

- **O titular conta como uma das pessoas.** N pessoas, N - 1 acompanhantes.
- **Nome obrigatório para todos; documento obrigatório para adulto**,
  opcional para menor de idade.
- **Sem edição pelo site depois do pedido.** A aplicação tem só SELECT e
  INSERT na tabela.

O que estes testes guardam:

1. **A regra mora no banco.** O gatilho recusa acompanhante além de
   N - 1, o `CHECK` recusa adulto sem documento, e a reserva não pode
   baixar o número de pessoas abaixo da lista gravada.
2. **Tudo ou nada.** A reserva e os acompanhantes entram na mesma
   transação.
3. **Cifrado de verdade.** Os bytes no banco não contêm o texto, e o
   bloco cifrado não se move de linha nem de campo.
4. **Nenhum nome nem documento no log, nem na URL.**
5. **A tela oferece só o que cabe** — e o servidor recusa o resto.
"""

import logging
import re

import mysql.connector
import pytest

from app.dados import acompanhante_repo, conexao, reserva_repo
from app.dados.erros import ErroDeDados
from app.seguranca import cripto
from app.servicos import pedido

# As fixtures e auxiliares do pedido do Bloco 7, sem cópia.
from test_pedido_do_hospede import (  # noqa: F401
    DADOS,
    OUTRA,
    _entrar,
    _escolha,
    _token,
    aplicacao,
    chale,
    conta,
    hospede,
    outra,
)

pytestmark = pytest.mark.banco

ERRO_DE_CHECK = 3819
ERRO_DE_SIGNAL = 1644
ERRO_DE_DUPLICIDADE = 1062
ERRO_DE_REFERENCIA_EM_USO = 1451

NOME_1 = "Helena Prado Teixeira"
NOME_2 = "Joaquim Prado Teixeira"
DOC_1 = "RG4455667"
DOC_2 = "PB9988776"


def _com(escolha, *pessoas):
    """Troca os acompanhantes de uma escolha. Cada pessoa é
    (nome, documento, menor)."""
    dados = {
        chave: valor
        for chave, valor in escolha.items()
        if not chave.startswith("acompanhante_")
    }
    for numero, (nome, documento, menor) in enumerate(pessoas, start=1):
        dados[f"acompanhante_{numero}_nome"] = nome
        dados[f"acompanhante_{numero}_documento"] = documento
        if menor:
            dados[f"acompanhante_{numero}_menor"] = "sim"
    return dados


def _familia(chale):
    """Três pessoas: o titular, um adulto e uma criança sem documento."""
    return _com(
        _escolha(chale, pessoas=3),
        (NOME_1, DOC_1, False),
        (NOME_2, "", True),
    )


def _cru(conexao_banco, comando, parametros=()):
    """Comando direto no banco de teste, sem a camada de dados."""
    cursor = conexao_banco.cursor()
    try:
        cursor.execute(comando, parametros)
        if cursor.with_rows:
            return cursor.fetchall()
        return None
    finally:
        cursor.close()


def _erro(conexao_banco, comando, parametros=()):
    with pytest.raises(mysql.connector.Error) as erro:
        _cru(conexao_banco, comando, parametros)
    return erro.value


def _reserva_crua(conexao_banco, chale, conta, pessoas):
    """Uma reserva da equipe, direto no banco."""
    _cru(
        conexao_banco,
        "INSERT INTO reserva (chale_id, hospede_id, data_entrada, data_saida, "
        "num_pessoas, situacao) VALUES (%s, %s, CURDATE() + INTERVAL 60 DAY, "
        "CURDATE() + INTERVAL 62 DAY, %s, 'confirmada')",
        (chale["id"], conta["hospede_id"], pessoas),
    )
    return _cru(conexao_banco, "SELECT LAST_INSERT_ID()")[0][0]


_INSERIR = (
    "INSERT INTO acompanhante (reserva_id, ordem, nome_cif, nome_nonce, "
    "nome_tag, documento_cif, documento_nonce, documento_tag, menor_de_idade) "
    "VALUES (%s, %s, 'x', 'nnnnnnnnnnnn', 'tttttttttttttttt', %s, %s, %s, %s)"
)


def _acompanhante_cru(conexao_banco, reserva_id, ordem, documento=True,
                      menor=False):
    partes = ("d", "nnnnnnnnnnnn", "tttttttttttttttt") if documento else (
        None, None, None)
    _cru(conexao_banco, _INSERIR, (reserva_id, ordem, *partes, menor))


def _linhas(aplicacao):
    with aplicacao.app_context():
        with conexao.obter_cursor(dicionario=True) as cursor:
            cursor.execute("SELECT * FROM acompanhante ORDER BY id")
            return cursor.fetchall()


def _reservas_de(aplicacao, conta):
    with aplicacao.app_context():
        return reserva_repo.listar_por_hospede(conta["hospede_id"])


# ===========================================================================
# 1. O banco: CHECK, gatilho, chave estrangeira
# ===========================================================================

def test_adulto_sem_documento_e_recusado_pelo_banco(aplicacao, conexao_banco,
                                                    chale, conta):
    reserva_id = _reserva_crua(conexao_banco, chale, conta, 3)
    erro = _erro(
        conexao_banco, _INSERIR,
        (reserva_id, 1, None, None, None, False),
    )
    assert erro.errno == ERRO_DE_CHECK
    assert "ck_acompanhante_documento_ou_menor" in erro.msg


def test_menor_sem_documento_e_aceito_pelo_banco(aplicacao, conexao_banco,
                                                 chale, conta):
    reserva_id = _reserva_crua(conexao_banco, chale, conta, 3)
    _acompanhante_cru(conexao_banco, reserva_id, 1, documento=False, menor=True)
    assert len(_linhas(aplicacao)) == 1


def test_documento_pela_metade_e_recusado(aplicacao, conexao_banco, chale,
                                          conta):
    """Cifra sem nonce é um bloco que nunca se decifra."""
    reserva_id = _reserva_crua(conexao_banco, chale, conta, 3)
    erro = _erro(
        conexao_banco, _INSERIR,
        (reserva_id, 1, "d", None, "tttttttttttttttt", True),
    )
    assert erro.errno == ERRO_DE_CHECK
    assert "ck_acompanhante_documento_inteiro" in erro.msg


def test_a_ordem_comeca_em_um(aplicacao, conexao_banco, chale, conta):
    reserva_id = _reserva_crua(conexao_banco, chale, conta, 3)
    erro = _erro(
        conexao_banco, _INSERIR,
        (reserva_id, 0, "d", "nnnnnnnnnnnn", "tttttttttttttttt", False),
    )
    assert erro.errno == ERRO_DE_CHECK


def test_a_mesma_ordem_nao_se_repete_na_reserva(aplicacao, conexao_banco,
                                                chale, conta):
    """A etiqueta da cifra usa a ordem; repetida, ela deixaria de ser única."""
    reserva_id = _reserva_crua(conexao_banco, chale, conta, 4)
    _acompanhante_cru(conexao_banco, reserva_id, 1)
    erro = _erro(
        conexao_banco, _INSERIR,
        (reserva_id, 1, "d", "nnnnnnnnnnnn", "tttttttttttttttt", False),
    )
    assert erro.errno == ERRO_DE_DUPLICIDADE


def test_o_gatilho_aceita_ate_n_menos_um(aplicacao, conexao_banco, chale,
                                         conta):
    reserva_id = _reserva_crua(conexao_banco, chale, conta, 3)
    _acompanhante_cru(conexao_banco, reserva_id, 1)
    _acompanhante_cru(conexao_banco, reserva_id, 2)

    erro = _erro(
        conexao_banco, _INSERIR,
        (reserva_id, 3, "d", "nnnnnnnnnnnn", "tttttttttttttttt", False),
    )
    assert erro.errno == ERRO_DE_SIGNAL
    assert "todos os acompanhantes" in erro.msg
    assert len(_linhas(aplicacao)) == 2


def test_reserva_de_uma_pessoa_nao_tem_acompanhante(aplicacao, conexao_banco,
                                                    chale, conta):
    """O titular conta como uma das pessoas."""
    reserva_id = _reserva_crua(conexao_banco, chale, conta, 1)
    erro = _erro(
        conexao_banco, _INSERIR,
        (reserva_id, 1, "d", "nnnnnnnnnnnn", "tttttttttttttttt", False),
    )
    assert erro.errno == ERRO_DE_SIGNAL


def test_o_gatilho_conta_e_nao_olha_a_ordem(aplicacao, conexao_banco, chale,
                                            conta):
    """Ordens 7 e 9 numa reserva de 3: o limite é a quantidade."""
    reserva_id = _reserva_crua(conexao_banco, chale, conta, 3)
    _acompanhante_cru(conexao_banco, reserva_id, 7)
    _acompanhante_cru(conexao_banco, reserva_id, 9)
    erro = _erro(
        conexao_banco, _INSERIR,
        (reserva_id, 2, "d", "nnnnnnnnnnnn", "tttttttttttttttt", False),
    )
    assert erro.errno == ERRO_DE_SIGNAL


def test_dois_envios_simultaneos_nao_passam_do_limite(aplicacao, conexao_banco,
                                                      chale, conta):
    """O `FOR UPDATE` do gatilho, provado com duas conexões.

    Reserva de 2 pessoas, um acompanhante possível. A conexão A grava o
    primeiro e não confirma. A conexão B tenta gravar outro: sem a trava,
    B contaria zero (o de A ainda não é visível para ela), gravaria, e
    as duas confirmariam dois acompanhantes. Com a trava, B espera por A
    — e, com espera de 1 segundo, desiste (1205).
    """
    from conftest import _configuracao

    reserva_id = _reserva_crua(conexao_banco, chale, conta, 2)
    cfg = _configuracao()

    def _nova():
        return mysql.connector.connect(
            host=cfg["VILLAJU_DB_HOST"], port=int(cfg["VILLAJU_DB_PORTA"]),
            user=cfg["VILLAJU_DB_USUARIO_TESTE"],
            password=cfg["VILLAJU_DB_SENHA_TESTE"],
            database=cfg["VILLAJU_DB_NOME_TESTE"], autocommit=False,
        )

    a, b = _nova(), _nova()
    try:
        ca, cb = a.cursor(), b.cursor()
        cb.execute("SET SESSION innodb_lock_wait_timeout = 1")
        ca.execute(_INSERIR, (reserva_id, 1, "d", "nnnnnnnnnnnn",
                              "tttttttttttttttt", False))

        with pytest.raises(mysql.connector.Error) as erro:
            cb.execute(_INSERIR, (reserva_id, 2, "d", "nnnnnnnnnnnn",
                                  "tttttttttttttttt", False))
            b.commit()
        assert erro.value.errno == 1205, erro.value.msg

        a.commit()
    finally:
        a.rollback()
        b.rollback()
        a.close()
        b.close()

    assert len(_linhas(aplicacao)) == 1


def test_o_banco_preenche_criado_em(aplicacao, conexao_banco, chale, conta):
    reserva_id = _reserva_crua(conexao_banco, chale, conta, 2)
    _acompanhante_cru(conexao_banco, reserva_id, 1)
    assert _linhas(aplicacao)[0]["criado_em"] is not None


def test_a_recepcao_nao_baixa_as_pessoas_abaixo_da_lista(aplicacao,
                                                          conexao_banco,
                                                          chale, conta):
    """A recepção edita `num_pessoas`. Com 2 acompanhantes, o mínimo é 3."""
    reserva_id = _reserva_crua(conexao_banco, chale, conta, 3)
    _acompanhante_cru(conexao_banco, reserva_id, 1)
    _acompanhante_cru(conexao_banco, reserva_id, 2)

    erro = _erro(
        conexao_banco,
        "UPDATE reserva SET num_pessoas = 2 WHERE id = %s", (reserva_id,),
    )
    assert erro.errno == ERRO_DE_SIGNAL
    assert "acompanhantes registrados" in erro.msg


def test_a_recepcao_ainda_edita_a_reserva_com_lista(aplicacao, conexao_banco,
                                                    chale, conta):
    """A recusa não pode travar a edição comum: o mesmo número, ou mais."""
    reserva_id = _reserva_crua(conexao_banco, chale, conta, 3)
    _acompanhante_cru(conexao_banco, reserva_id, 1)
    _acompanhante_cru(conexao_banco, reserva_id, 2)

    _cru(conexao_banco,
         "UPDATE reserva SET observacoes = 'berço' WHERE id = %s", (reserva_id,))
    _cru(conexao_banco,
         "UPDATE reserva SET num_pessoas = 4 WHERE id = %s", (reserva_id,))
    assert _cru(
        conexao_banco, "SELECT num_pessoas FROM reserva WHERE id = %s",
        (reserva_id,),
    )[0][0] == 4


def test_reserva_com_acompanhante_nao_se_apaga(aplicacao, conexao_banco, chale,
                                               conta):
    reserva_id = _reserva_crua(conexao_banco, chale, conta, 2)
    _acompanhante_cru(conexao_banco, reserva_id, 1)
    erro = _erro(
        conexao_banco, "DELETE FROM reserva WHERE id = %s", (reserva_id,)
    )
    assert erro.errno == ERRO_DE_REFERENCIA_EM_USO


# ===========================================================================
# 2. A validação do serviço — sem banco, exceto a do titular
# ===========================================================================

def _ler(dados, pessoas):
    return pedido.ler_acompanhantes(dados, pessoas)


def _forma(*pessoas):
    dados = {}
    for numero, (nome, documento, menor) in enumerate(pessoas, start=1):
        dados[f"acompanhante_{numero}_nome"] = nome
        dados[f"acompanhante_{numero}_documento"] = documento
        if menor:
            dados[f"acompanhante_{numero}_menor"] = "sim"
    return dados


def test_a_lista_valida_volta_limpa():
    lista = _ler(
        _forma(("  Helena   Prado ", "529.982.247-25", False),
               ("Joaquim", "", True)),
        3,
    )
    assert lista == [
        {"nome": "Helena Prado", "documento": "52998224725",
         "menor_de_idade": False},
        {"nome": "Joaquim", "documento": None, "menor_de_idade": True},
    ]


def test_adulto_sem_documento_e_recusado():
    with pytest.raises(pedido.ErroDePedido, match="Acompanhante 1: informe o documento"):
        _ler(_forma((NOME_1, "", False)), 2)


def test_menor_com_documento_guarda_o_documento():
    lista = _ler(_forma((NOME_1, DOC_1, True)), 2)
    assert lista[0]["documento"] == DOC_1
    assert lista[0]["menor_de_idade"] is True


def test_acompanhante_a_menos_e_recusado():
    """Quatro pessoas pedem três acompanhantes; chegaram dois."""
    with pytest.raises(pedido.ErroDePedido, match="Acompanhante 3: informe o nome"):
        _ler(_forma((NOME_1, DOC_1, False), (NOME_2, DOC_2, False)), 4)


def test_acompanhante_a_mais_e_recusado():
    """O campo além de N - 1 não é ignorado em silêncio: é envio forjado."""
    with pytest.raises(pedido.ErroDePedido, match="mais acompanhantes"):
        _ler(_forma((NOME_1, DOC_1, False), (NOME_2, DOC_2, False)), 2)


def test_campo_extra_vazio_nao_conta_como_acompanhante():
    dados = _forma((NOME_1, DOC_1, False))
    dados["acompanhante_2_nome"] = "   "
    assert len(_ler(dados, 2)) == 1


def test_numero_zero_tambem_e_campo_a_mais():
    dados = _forma((NOME_1, DOC_1, False))
    dados["acompanhante_0_nome"] = "Fantasma"
    with pytest.raises(pedido.ErroDePedido, match="mais acompanhantes"):
        _ler(dados, 2)


@pytest.mark.parametrize("documento", ["1234", "A" * 21, "123<45", "12345;6", "ÁBC123"])
def test_documento_fora_do_formato_e_recusado(documento):
    with pytest.raises(pedido.ErroDePedido, match="de 5 a 20 letras ou números"):
        _ler(_forma((NOME_1, documento, False)), 2)


@pytest.mark.parametrize("documento", ["12345", "A" * 20, "12.345.678-x", "AB 123 456"])
def test_documento_no_limite_e_aceito(documento):
    assert _ler(_forma((NOME_1, documento, False)), 2)[0]["documento"]


def test_documento_repetido_na_lista_e_recusado():
    """Pontuação diferente, mesmo documento."""
    with pytest.raises(pedido.ErroDePedido, match="Acompanhante 2: o documento se repete"):
        _ler(_forma((NOME_1, "529.982.247-25", False),
                    (NOME_2, "52998224725", False)), 3)


@pytest.mark.parametrize("nome", ["", "   ", "A", "12345", "-- --"])
def test_nome_vazio_ou_curto_e_recusado(nome):
    with pytest.raises(pedido.ErroDePedido, match="Acompanhante 1"):
        _ler(_forma((nome, DOC_1, False)), 2)


def test_nome_longo_demais_e_recusado():
    with pytest.raises(pedido.ErroDePedido, match="passa de 120"):
        _ler(_forma(("A" * 121, DOC_1, False)), 2)


def test_nome_no_limite_e_aceito():
    assert _ler(_forma(("A" * 120, DOC_1, False)), 2)[0]["nome"] == "A" * 120


def test_nome_com_caractere_de_controle_e_recusado():
    with pytest.raises(pedido.ErroDePedido, match="não são letras"):
        _ler(_forma(("Helena\x00Prado", DOC_1, False)), 2)


def test_a_mensagem_de_erro_nao_repete_o_que_foi_digitado():
    """A tela mostra a posição, não o valor — nem o documento."""
    with pytest.raises(pedido.ErroDePedido) as erro:
        _ler(_forma((NOME_1, "999<888", False)), 2)
    assert "999" not in str(erro.value)
    assert NOME_1 not in str(erro.value)


def test_o_documento_do_titular_e_recusado(aplicacao, conta, chale):
    escolha = _com(
        _escolha(chale, pessoas=2), (NOME_1, DADOS["documento"], False)
    )
    with aplicacao.app_context():
        with pytest.raises(pedido.ErroDePedido, match="este é o seu documento"):
            pedido.pedir(conta, escolha)
    assert _reservas_de(aplicacao, conta) == []


def test_o_documento_do_titular_sem_pontuacao_tambem(aplicacao, conta, chale):
    sem_pontos = re.sub(r"\D", "", DADOS["documento"])
    escolha = _com(_escolha(chale, pessoas=2), (NOME_1, sem_pontos, False))
    with aplicacao.app_context():
        with pytest.raises(pedido.ErroDePedido, match="este é o seu documento"):
            pedido.pedir(conta, escolha)


def test_o_documento_de_outro_hospede_cadastrado_e_aceito(aplicacao, conta,
                                                          outra, chale):
    """Um parente com conta própria pode ir como acompanhante."""
    escolha = _com(_escolha(chale, pessoas=2), (NOME_1, OUTRA["documento"], False))
    with aplicacao.app_context():
        assert pedido.pedir(conta, escolha)


def test_mascara_mostra_so_os_tres_ultimos():
    assert pedido.mascarar_documento("52998224725") == "•••725"
    assert pedido.mascarar_documento("12345") == "•••345"
    assert pedido.mascarar_documento(None) == ""


def test_a_mascara_nao_conta_o_tamanho():
    assert len(pedido.mascarar_documento("12345")) == len(
        pedido.mascarar_documento("A" * 20)
    )


# ===========================================================================
# 3. A gravação: tudo ou nada, e cifrada
# ===========================================================================

def test_o_pedido_grava_os_acompanhantes(aplicacao, conta, chale):
    with aplicacao.app_context():
        identificador = pedido.pedir(conta, _familia(chale))
        lista = acompanhante_repo.listar_da_reserva(identificador)

    assert [(p["ordem"], p["nome"], p["documento"], p["menor_de_idade"])
            for p in lista] == [
        (1, NOME_1, DOC_1, False),
        (2, NOME_2, None, True),
    ]


def test_os_bytes_no_banco_nao_contem_o_texto(aplicacao, conta, chale):
    with aplicacao.app_context():
        pedido.pedir(conta, _familia(chale))

    linhas = _linhas(aplicacao)
    assert len(linhas) == 2
    for linha in linhas:
        for coluna, valor in linha.items():
            if isinstance(valor, (bytes, bytearray)):
                for texto in (NOME_1, NOME_2, DOC_1, "Helena", "4455667"):
                    assert texto.encode("utf-8") not in bytes(valor), (
                        f"{texto!r} em claro na coluna {coluna}"
                    )
        assert len(linha["nome_nonce"]) == 12
        assert len(linha["nome_tag"]) == 16


def test_a_reserva_nao_fica_se_um_acompanhante_e_recusado(aplicacao, conta,
                                                          chale):
    """Adulto sem documento passa direto ao repositório: o CHECK recusa
    o segundo acompanhante, e a reserva e o primeiro saem junto."""
    with aplicacao.app_context():
        with pytest.raises(ErroDeDados):
            reserva_repo.criar_pedido(
                {
                    "chale_id": chale["id"],
                    "hospede_id": conta["hospede_id"],
                    "data_entrada": _escolha(chale)["entrada"],
                    "data_saida": _escolha(chale)["saida"],
                    "num_pessoas": 3,
                    "valor_total": "600.00",
                },
                modalidade="sinal",
                minutos_para_o_pix=90,
                prazo_restante=None,
                limite_em_aberto=1,
                usuario_id=conta["id"],
                acompanhantes=[
                    {"nome": NOME_1, "documento": DOC_1, "menor_de_idade": False},
                    {"nome": NOME_2, "documento": None, "menor_de_idade": False},
                ],
            )

    assert _reservas_de(aplicacao, conta) == []
    assert _linhas(aplicacao) == []


def test_a_reserva_nao_fica_se_ha_acompanhante_demais(aplicacao, conta, chale):
    """O serviço foi contornado; o gatilho segura, e nada fica."""
    with aplicacao.app_context():
        with pytest.raises(ErroDeDados, match="todos os acompanhantes"):
            reserva_repo.criar_pedido(
                {
                    "chale_id": chale["id"],
                    "hospede_id": conta["hospede_id"],
                    "data_entrada": _escolha(chale)["entrada"],
                    "data_saida": _escolha(chale)["saida"],
                    "num_pessoas": 2,
                    "valor_total": "600.00",
                },
                modalidade="sinal",
                minutos_para_o_pix=90,
                prazo_restante=None,
                limite_em_aberto=1,
                usuario_id=conta["id"],
                acompanhantes=[
                    {"nome": NOME_1, "documento": DOC_1, "menor_de_idade": False},
                    {"nome": NOME_2, "documento": DOC_2, "menor_de_idade": False},
                ],
            )

    assert _reservas_de(aplicacao, conta) == []
    assert _linhas(aplicacao) == []


def test_o_bloco_cifrado_nao_muda_de_linha(aplicacao, conexao_banco, conta,
                                           chale):
    """Quem tem escrita no banco troca o nome do 1 pelo do 2: a
    verificação de integridade falha, em vez de mostrar a pessoa errada."""
    with aplicacao.app_context():
        identificador = pedido.pedir(conta, _familia(chale))

    _cru(
        conexao_banco,
        "UPDATE acompanhante a1 JOIN acompanhante a2 "
        "ON a1.reserva_id = a2.reserva_id AND a1.ordem = 1 AND a2.ordem = 2 "
        "SET a1.nome_cif = a2.nome_cif, a1.nome_nonce = a2.nome_nonce, "
        "    a1.nome_tag = a2.nome_tag",
    )
    with aplicacao.app_context():
        with pytest.raises(cripto.FalhaDeAutenticacao):
            acompanhante_repo.listar_da_reserva(identificador)


def test_o_bloco_cifrado_nao_troca_de_campo(aplicacao, conexao_banco, conta,
                                            chale):
    """O documento posto no lugar do nome, na mesma linha, também falha."""
    with aplicacao.app_context():
        identificador = pedido.pedir(conta, _familia(chale))

    _cru(
        conexao_banco,
        "UPDATE acompanhante SET nome_cif = documento_cif, "
        "nome_nonce = documento_nonce, nome_tag = documento_tag "
        "WHERE ordem = 1",
    )
    with aplicacao.app_context():
        with pytest.raises(cripto.FalhaDeAutenticacao):
            acompanhante_repo.listar_da_reserva(identificador)


def test_a_etiqueta_tem_reserva_ordem_e_campo():
    assert cripto.aad_acompanhante(7, 2, "nome") == b"acompanhante:7:2:nome"
    assert cripto.aad_acompanhante(7, 2, "nome") != cripto.aad_acompanhante(
        7, 2, "documento"
    )


# ===========================================================================
# 4. O log
# ===========================================================================

def test_o_log_diz_quantos_e_nao_quem(aplicacao, conta, chale, conexao_banco):
    with aplicacao.app_context():
        identificador = pedido.pedir(conta, _familia(chale))

    detalhes = [
        linha[0] or "" for linha in _cru(
            conexao_banco, "SELECT detalhe FROM log_auditoria"
        )
    ]
    do_pedido = [d for d in detalhes if d.startswith("Pedido pelo site")]
    assert do_pedido == [
        "Pedido pelo site, pagamento sinal, com 2 acompanhantes; "
        "regulamento aceito"
    ], identificador

    for detalhe in detalhes:
        for texto in (NOME_1, NOME_2, "Helena", "Joaquim", DOC_1, "4455667"):
            assert texto not in detalhe, f"{texto!r} no log de auditoria"


def test_o_log_sem_acompanhantes(aplicacao, conta, chale, conexao_banco):
    with aplicacao.app_context():
        pedido.pedir(conta, _escolha(chale, pessoas=1))
    detalhes = [linha[0] for linha in _cru(
        conexao_banco,
        "SELECT detalhe FROM log_auditoria WHERE detalhe LIKE 'Pedido pelo site%'",
    )]
    assert detalhes == [
        "Pedido pelo site, pagamento sinal, sem acompanhantes; "
        "regulamento aceito"
    ]


@pytest.mark.parametrize("quantidade,texto", [
    (0, "sem acompanhantes"), (1, "com 1 acompanhante"),
    (2, "com 2 acompanhantes"), (5, "com 5 acompanhantes"),
])
def test_a_descricao_da_quantidade(quantidade, texto):
    assert pedido.descrever_acompanhantes(quantidade) == texto


def test_nenhum_nome_nem_documento_no_log_da_aplicacao(aplicacao, hospede,
                                                       chale, caplog):
    """O percurso inteiro pela tela, com o log da aplicação em DEBUG."""
    dados = _familia(chale)
    dados["csrf_token"] = _token(hospede, "/reservas/pedir")

    with caplog.at_level(logging.DEBUG):
        hospede.post("/reservas/pedir/revisar", data=dados)
        hospede.post("/reservas/pedir", data=dados)
        hospede.get("/reservas/minhas")
        # E uma recusa, que também passa pelo log se algo for registrar.
        recusa = dict(dados, acompanhante_1_documento="")
        hospede.post("/reservas/pedir/revisar", data=recusa)

    for registro in caplog.records:
        linha = registro.getMessage()
        for texto in (NOME_1, NOME_2, "Helena", "Joaquim", DOC_1, "4455667"):
            assert texto not in linha, f"{texto!r} no log: {linha}"


# ===========================================================================
# 5. Quem vê
# ===========================================================================

def test_o_card_do_titular_lista_quem_vai_com_documento_mascarado(
        aplicacao, hospede, conta, chale):
    with aplicacao.app_context():
        identificador = pedido.pedir(conta, _familia(chale))

    html = hospede.get("/reservas/minhas").data.decode()
    card = html[html.index(f'id="reserva-{identificador}"'):]
    card = card[:card.index("</article>")]

    assert NOME_1 in card and NOME_2 in card
    assert "•••667" in card
    assert DOC_1 not in html, "o documento inteiro chegou à página"
    assert "menor de idade" in card


def test_a_reserva_alheia_nao_mostra_acompanhantes(aplicacao, conta, outra,
                                                   chale):
    with aplicacao.app_context():
        pedido.pedir(conta, _familia(chale))
        assert acompanhante_repo.listar_por_hospede(outra["hospede_id"]) == {}

    alheio = _entrar(aplicacao, OUTRA["email"])
    html = alheio.get("/reservas/minhas").data.decode()
    for texto in (NOME_1, NOME_2, "•••667"):
        assert texto not in html


def test_a_lista_do_titular_e_por_reserva(aplicacao, conta, chale):
    with aplicacao.app_context():
        identificador = pedido.pedir(conta, _familia(chale))
        por_reserva = acompanhante_repo.listar_por_hospede(conta["hospede_id"])
    assert list(por_reserva) == [identificador]
    assert [p["nome"] for p in por_reserva[identificador]] == [NOME_1, NOME_2]


def test_a_equipe_le_o_documento_inteiro(aplicacao, conta, chale):
    """O card da equipe (Bloco 8) mostra a lista completa."""
    with aplicacao.app_context():
        identificador = pedido.pedir(conta, _familia(chale))
        lista = acompanhante_repo.listar_da_reserva(identificador)
    assert lista[0]["documento"] == DOC_1


# ===========================================================================
# 6. A tela: os quatro passos
# ===========================================================================

def _passo(html):
    achado = re.search(r'<li aria-current="step"\s+class="[^"]*">([^<]+)</li>', html)
    return achado.group(1).strip() if achado else None


def test_passo_1_pede_chale_e_datas_sem_pessoas(aplicacao, hospede):
    html = hospede.get("/reservas/pedir").data.decode()
    assert _passo(html) == "Chalé e datas"
    assert 'name="pessoas"' not in html


def test_passo_2_oferece_botoes_ate_a_capacidade(aplicacao, hospede, chale):
    escolha = _escolha(chale)
    html = hospede.get("/reservas/pedir", query_string={
        "chale": escolha["chale"], "entrada": escolha["entrada"],
        "saida": escolha["saida"],
    }).data.decode()

    assert _passo(html) == "Pessoas"
    valores = re.findall(r'type="radio" id="pessoas-\d+" name="pessoas" value="(\d+)"', html)
    assert valores == [str(n) for n in range(1, chale["capacidade"] + 1)]
    assert 'type="number"' not in html


def test_passo_2_muda_com_o_chale(aplicacao, hospede, conexao_banco):
    """Cada chalé, os seus botões — o que o campo único não fazia.

    Na massa fictícia os chalés recebem todos 4; um deles passa a 2 e
    outro a 6, para que o mesmo número não sirva a todos.
    """
    _cru(conexao_banco, "UPDATE chale SET capacidade = 2 ORDER BY id LIMIT 1")
    _cru(conexao_banco,
         "UPDATE chale SET capacidade = 6 ORDER BY id DESC LIMIT 1")
    with aplicacao.app_context():
        with conexao.obter_cursor(dicionario=True) as cursor:
            cursor.execute("SELECT id, capacidade FROM chale WHERE ativo = TRUE")
            chales = cursor.fetchall()
    assert len({c["capacidade"] for c in chales}) > 1, "massa sem variação"

    for chale in chales:
        escolha = _escolha(chale)
        html = hospede.get("/reservas/pedir", query_string={
            "chale": escolha["chale"], "entrada": escolha["entrada"],
            "saida": escolha["saida"],
        }).data.decode()
        assert len(re.findall(r'name="pessoas" value=', html)) == chale["capacidade"]


def test_pessoas_forjadas_na_url_voltam_aos_botoes(aplicacao, hospede, chale):
    escolha = _escolha(chale)
    html = hospede.get("/reservas/pedir", query_string={
        "chale": escolha["chale"], "entrada": escolha["entrada"],
        "saida": escolha["saida"], "pessoas": chale["capacidade"] + 6,
    }).data.decode()
    assert _passo(html) == "Pessoas"
    assert f"recebe até {chale['capacidade']} pessoas" in html


def test_pessoas_forjadas_no_envio_final_sao_recusadas(aplicacao, hospede,
                                                       conta, chale):
    """Quem monta o envio à mão manda qualquer número."""
    pessoas = chale["capacidade"] + 1
    dados = _escolha(chale, pessoas=pessoas)
    dados["csrf_token"] = _token(hospede, "/reservas/pedir")

    resposta = hospede.post("/reservas/pedir", data=dados)
    assert resposta.status_code == 200
    assert f"recebe até {chale['capacidade']} pessoas" in resposta.data.decode()
    assert _reservas_de(aplicacao, conta) == []


def test_passo_3_tem_um_campo_por_acompanhante(aplicacao, hospede, chale):
    escolha = _escolha(chale, pessoas=3)
    html = hospede.get("/reservas/pedir", query_string={
        campo: escolha[campo] for campo in ("chale", "entrada", "saida", "pessoas")
    }).data.decode()

    assert _passo(html) == "Quem vai"
    assert re.findall(r'name="acompanhante_(\d+)_nome"', html) == ["1", "2"]
    assert re.findall(r'name="acompanhante_(\d+)_menor"', html) == ["1", "2"]


def test_passo_3_vai_por_post(aplicacao, hospede, chale):
    """Nome e documento na URL iriam para o histórico e o log do servidor."""
    escolha = _escolha(chale, pessoas=2)
    html = hospede.get("/reservas/pedir", query_string={
        campo: escolha[campo] for campo in ("chale", "entrada", "saida", "pessoas")
    }).data.decode()
    formulario = html[html.index('action="/reservas/pedir/revisar"') - 40:]
    assert 'method="post"' in formulario[:60]


def test_passo_3_de_uma_pessoa_nao_pede_ninguem(aplicacao, hospede, chale):
    escolha = _escolha(chale, pessoas=1)
    html = hospede.get("/reservas/pedir", query_string={
        campo: escolha[campo] for campo in ("chale", "entrada", "saida", "pessoas")
    }).data.decode()
    assert "acompanhante_1_nome" not in html
    assert "só para você" in html


def test_a_revisao_recusada_volta_preenchida_sem_redirecionar(aplicacao,
                                                              hospede, chale):
    dados = _com(_escolha(chale, pessoas=3),
                 (NOME_1, DOC_1, False), (NOME_2, "", False))
    dados["csrf_token"] = _token(hospede, "/reservas/pedir")

    resposta = hospede.post("/reservas/pedir/revisar", data=dados)
    html = resposta.data.decode()

    assert resposta.status_code == 200
    assert _passo(html) == "Quem vai"
    assert "Acompanhante 2: informe o documento" in html
    assert f'value="{NOME_1}"' in html and f'value="{NOME_2}"' in html


def test_a_revisao_mostra_quem_vai(aplicacao, hospede, chale):
    dados = _familia(chale)
    dados["csrf_token"] = _token(hospede, "/reservas/pedir")
    html = hospede.post("/reservas/pedir/revisar", data=dados).data.decode()

    assert _passo(html) == "Confirmar"
    assert "Você, titular da reserva" in html
    assert NOME_1 in html and NOME_2 in html
    assert "Confirmar pedido" in html


def test_a_revisao_ja_recusa_o_documento_do_titular(aplicacao, hospede, chale):
    """No passo 4, e não só no envio final: o resumo não pode prometer
    uma lista que a gravação vai recusar."""
    dados = _com(_escolha(chale, pessoas=2), (NOME_1, DADOS["documento"], False))
    dados["csrf_token"] = _token(hospede, "/reservas/pedir")
    html = hospede.post("/reservas/pedir/revisar", data=dados).data.decode()
    assert "este é o seu documento" in html
    assert _passo(html) == "Quem vai"


def test_a_revisao_nao_grava_nada(aplicacao, hospede, conta, chale):
    dados = _familia(chale)
    dados["csrf_token"] = _token(hospede, "/reservas/pedir")
    hospede.post("/reservas/pedir/revisar", data=dados)
    assert _reservas_de(aplicacao, conta) == []
    assert _linhas(aplicacao) == []


def test_o_percurso_inteiro_pela_tela(aplicacao, hospede, conta, chale):
    """Revisa, e o formulário do passo 4 é o que grava — sem nada a mais."""
    dados = _familia(chale)
    dados["csrf_token"] = _token(hospede, "/reservas/pedir")
    html = hospede.post("/reservas/pedir/revisar", data=dados).data.decode()

    formulario = html[html.index('action="/reservas/pedir" class="formulario"'):]
    formulario = formulario[:formulario.index("</form>")]
    enviados = dict(re.findall(
        r'type="hidden" name="([^"]+)"\s+value="([^"]*)"', formulario
    ))
    enviados.update(re.findall(
        r'type="hidden" name="([^"]+)" value="([^"]*)"', formulario
    ))
    enviados["aceite"] = "sim"

    resposta = hospede.post("/reservas/pedir", data=enviados)
    assert resposta.status_code == 302

    with aplicacao.app_context():
        reservas = reserva_repo.listar_por_hospede(conta["hospede_id"])
        lista = acompanhante_repo.listar_da_reserva(reservas[0]["id"])
    assert [(p["nome"], p["documento"], p["menor_de_idade"]) for p in lista] == [
        (NOME_1, DOC_1, False), (NOME_2, None, True),
    ]


def test_o_envio_final_revalida_os_acompanhantes(aplicacao, hospede, conta,
                                                 chale):
    """O passo 4 revisado pode ser alterado no caminho."""
    dados = _com(_escolha(chale, pessoas=2), (NOME_1, "", False))
    dados["csrf_token"] = _token(hospede, "/reservas/pedir")

    resposta = hospede.post("/reservas/pedir", data=dados)
    assert resposta.status_code == 200
    assert "Acompanhante 1: informe o documento" in resposta.data.decode()
    assert _reservas_de(aplicacao, conta) == []


def test_a_revisao_exige_csrf(aplicacao, hospede, chale):
    aplicacao.config["WTF_CSRF_ENABLED"] = True
    try:
        resposta = hospede.post("/reservas/pedir/revisar", data=_familia(chale))
    finally:
        aplicacao.config["WTF_CSRF_ENABLED"] = False
    assert resposta.status_code == 400


def test_a_revisao_e_so_do_hospede(aplicacao, chale):
    recepcao = _entrar(aplicacao, "recepcao", "Villa@Recep2026")
    dados = _familia(chale)
    assert recepcao.post("/reservas/pedir/revisar", data=dados).status_code == 403

    visitante = aplicacao.test_client()
    resposta = visitante.post("/reservas/pedir/revisar", data=dados)
    assert resposta.status_code == 302
    assert "/login" in resposta.headers["Location"]
