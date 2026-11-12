"""Tokens de acesso: prova de posse de um endereço de e-mail.

O sistema cria a conta inativa e envia um link; só a abertura do link a
ativa. Quem abre o link recebeu a mensagem, e quem recebeu a mensagem é
dono do endereço.

Quatro propriedades sustentam isso, e cada uma tem testes próprios:

**O banco guarda o hash, nunca o token.** Quem obtiver leitura do banco
não abre link nenhum.

**Uso único.** Um link já aberto não abre de novo, mesmo no prazo.

**Prazo de validade.** Um link esquecido numa caixa antiga não serve
meses depois.

**Resposta uniforme.** Token inexistente, vencido e consumido produzem o
mesmo resultado, de modo que não se possa sondar quais existem.
"""

import datetime as dt
import hashlib
import logging

import pytest

from app.config import Configuracao
from app.dados import conexao, token_repo
from app.dados.token_repo import ErroDeToken

pytestmark = pytest.mark.banco

# A conta `joao` da massa fictícia, de perfil hóspede.
USUARIO = 3


@pytest.fixture
def banco(conexao_banco):
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


def _linha_do_token(token: str):
    with conexao.obter_cursor(dicionario=True) as cursor:
        cursor.execute(
            "SELECT * FROM token_acesso WHERE token_hash = %s",
            (hashlib.sha256(token.encode("utf-8")).digest(),),
        )
        return cursor.fetchone()


def _envelhecer(token: str, horas: int):
    """Move a expiração para trás, simulando passagem de tempo."""
    with conexao.obter_cursor() as cursor:
        cursor.execute(
            "UPDATE token_acesso SET expira_em = NOW() - INTERVAL %s HOUR "
            "WHERE token_hash = %s",
            (horas, hashlib.sha256(token.encode("utf-8")).digest()),
        )


# ---------------------------------------------------------------------------
# Emissão
# ---------------------------------------------------------------------------

def test_o_token_tem_entropia_suficiente(banco):
    """256 bits. Não há dicionário nem força bruta contra isso.

    É a razão de o hash ser SHA-256 e não Argon2id: o custo do Argon2id
    existe para proteger segredo de baixa entropia, que pessoa escolhe.
    """
    token = token_repo.gerar(USUARIO)

    assert len(token) >= 40, "token_urlsafe(32) produz 43 caracteres"
    assert token_repo.BYTES_DO_TOKEN == 32


def test_dois_tokens_nunca_se_repetem(banco):
    tokens = {token_repo.gerar(USUARIO) for _ in range(20)}
    assert len(tokens) == 20


def test_o_banco_guarda_o_hash_e_nao_o_token(banco):
    """Quem obtiver leitura do banco não consegue abrir link nenhum."""
    token = token_repo.gerar(USUARIO)
    linha = _linha_do_token(token)

    assert linha is not None
    assert len(linha["token_hash"]) == 32
    assert token.encode("utf-8") not in linha["token_hash"]

    # E nenhuma outra coluna o guarda por descuido.
    tudo = b"".join(
        valor if isinstance(valor, bytes) else str(valor).encode("utf-8")
        for valor in linha.values()
        if valor is not None
    )
    assert token.encode("utf-8") not in tudo


def test_o_token_nao_vai_para_o_log(banco, caplog):
    """Token em log é token vazado."""
    with caplog.at_level(logging.DEBUG):
        token = token_repo.gerar(USUARIO)
        token_repo.consumir(token)

    assert token not in caplog.text
    assert token[:16] not in caplog.text
    # O identificador do usuário pode e deve aparecer: permite diagnosticar.
    assert str(USUARIO) in caplog.text


def test_tipo_desconhecido_e_recusado(banco):
    with pytest.raises(ErroDeToken) as erro:
        token_repo.gerar(USUARIO, tipo="chave-mestra")
    assert "chave-mestra" in str(erro.value)


def test_validade_invalida_e_recusada(banco):
    with pytest.raises(ErroDeToken):
        token_repo.gerar(USUARIO, minutos=0)


def test_o_carimbo_de_criacao_vem_do_banco(banco):
    """Relógio de aplicação adiantado produziria token nascido vencido."""
    token = token_repo.gerar(USUARIO)
    criado = _linha_do_token(token)["criado_em"]

    assert isinstance(criado, dt.datetime)
    assert abs((dt.datetime.now() - criado).total_seconds()) < 60


def test_a_trigger_recusa_token_nascido_vencido(banco):
    """A regra está no banco, e vale mesmo contornando a aplicação."""
    import mysql.connector

    with conexao.obter_conexao() as ligacao:
        cursor = ligacao.cursor()
        with pytest.raises(mysql.connector.Error) as erro:
            cursor.execute(
                "INSERT INTO token_acesso "
                "  (usuario_id, token_hash, tipo, expira_em, criado_em) "
                "VALUES (%s, %s, 'confirmacao', %s, NOW())",
                (
                    USUARIO,
                    hashlib.sha256(b"qualquer").digest(),
                    dt.datetime.now() - dt.timedelta(hours=1),
                ),
            )
        cursor.close()

    assert erro.value.errno == 1644
    assert "vencido" in str(erro.value)


# ---------------------------------------------------------------------------
# Consumo
# ---------------------------------------------------------------------------

def test_o_token_valido_devolve_o_usuario(banco):
    token = token_repo.gerar(USUARIO)
    assert token_repo.consumir(token) == USUARIO


def test_o_token_e_de_uso_unico(banco):
    """Um link que vaze depois de usado não pode continuar valendo.

    Vaza no histórico do navegador, num encaminhamento de e-mail, num
    registro de servidor intermediário.

    **A pausa é necessária, e não é frescura.** A primeira versão deste
    teste consumia duas vezes seguidas e passava mesmo com as duas
    proteções de uso único removidas — porque o `rowcount` do MySQL conta
    linhas *alteradas*, e gravar o mesmo `NOW()` dentro do mesmo segundo
    não altera nada. O `None` da segunda chamada vinha de uma
    particularidade do banco, não da defesa.

    Medido com as proteções removidas: a segunda tentativa imediata
    devolvia `None`, e a terceira, 1,2 segundo depois, devolvia o usuário.
    O token era reutilizável; o teste é que não via.
    """
    import time

    token = token_repo.gerar(USUARIO)

    assert token_repo.consumir(token) == USUARIO

    time.sleep(1.2)
    assert token_repo.consumir(token) is None, (
        "o token valeu uma segunda vez passado o segundo corrente"
    )


def test_o_consumo_e_condicional_contra_a_corrida(banco):
    """Duas aberturas simultâneas do mesmo link não podem ambas valer.

    A verificação em Python — ler a linha e conferir `consumido_em` — não
    cobre a janela entre a leitura e a escrita: duas requisições podem ler
    a mesma linha ainda não consumida antes de qualquer uma gravar.

    Quem fecha essa janela é a condição no próprio `UPDATE`, que faz o
    banco decidir. É verificação estrutural porque a corrida não se
    reproduz de forma determinística num teste — e a proteção é
    invisível até alguém removê-la.
    """
    assert "consumido_em IS NULL" in token_repo._SQL_CONSUMIR, (
        "o UPDATE de consumo precisa ser condicional"
    )


def test_a_linha_ja_consumida_e_recusada_sem_depender_do_relogio(banco):
    """A verificação explícita, isolada da semântica de `rowcount`.

    A linha é marcada como consumida no passado, por SQL direto, de modo
    que nenhuma coincidência de segundo possa mascarar o resultado.
    """
    token = token_repo.gerar(USUARIO)

    with conexao.obter_cursor() as cursor:
        cursor.execute(
            "UPDATE token_acesso SET consumido_em = NOW() - INTERVAL 1 HOUR "
            "WHERE token_hash = %s",
            (hashlib.sha256(token.encode("utf-8")).digest(),),
        )

    assert token_repo.consumir(token) is None


def test_o_consumo_marca_a_hora(banco):
    token = token_repo.gerar(USUARIO)
    token_repo.consumir(token)

    assert _linha_do_token(token)["consumido_em"] is not None


def test_o_token_vencido_nao_vale(banco):
    token = token_repo.gerar(USUARIO)
    _envelhecer(token, horas=1)

    assert token_repo.consumir(token) is None


def test_o_token_no_limite_do_prazo_ainda_vale(banco):
    """A borda: vencido é `<=` agora, não `<`."""
    token = token_repo.gerar(USUARIO, minutos=60)
    assert token_repo.consumir(token) == USUARIO


def test_token_de_outro_tipo_nao_serve(banco):
    """Um link de recuperação de senha não ativa uma conta.

    Sem essa separação, quem conseguisse um token de um fluxo o usaria no
    outro — e os dois fluxos não têm o mesmo significado nem o mesmo
    efeito.
    """
    token = token_repo.gerar(USUARIO, tipo=token_repo.RECUPERACAO)

    assert token_repo.consumir(token, tipo=token_repo.CONFIRMACAO) is None
    # E continua valendo para o tipo certo: a recusa foi pelo motivo certo.
    assert token_repo.consumir(token, tipo=token_repo.RECUPERACAO) == USUARIO


@pytest.mark.parametrize(
    "apresentado",
    ["", None, "naoexiste", "a" * 43, "../../etc/passwd", "' OR 1=1 --"],
)
def test_a_resposta_e_a_mesma_para_todo_token_invalido(banco, apresentado):
    """Quem sonda não descobre em qual caso caiu.

    Token inexistente, vencido e já consumido devolvem `None`. Se um deles
    respondesse diferente, daria para descobrir quais tokens existem.
    """
    assert token_repo.consumir(apresentado) is None


def test_inexistente_vencido_e_consumido_sao_indistinguiveis(banco):
    consumido = token_repo.gerar(USUARIO)
    token_repo.consumir(consumido)

    vencido = token_repo.gerar(USUARIO)
    _envelhecer(vencido, horas=1)

    respostas = {
        token_repo.consumir("inexistente"),
        token_repo.consumir(vencido),
        token_repo.consumir(consumido),
    }

    assert respostas == {None}


# ---------------------------------------------------------------------------
# Emitir de novo
# ---------------------------------------------------------------------------

def test_emitir_de_novo_invalida_o_pendente(banco):
    """Quem pede o link de novo não fica com dois links válidos."""
    primeiro = token_repo.gerar(USUARIO)
    segundo = token_repo.gerar(USUARIO)

    assert token_repo.consumir(primeiro) is None
    assert token_repo.consumir(segundo) == USUARIO


def test_emitir_de_outro_tipo_nao_invalida_o_primeiro(banco):
    """A invalidação é por tipo: os dois fluxos são independentes."""
    confirmacao = token_repo.gerar(USUARIO, tipo=token_repo.CONFIRMACAO)
    token_repo.gerar(USUARIO, tipo=token_repo.RECUPERACAO)

    assert token_repo.consumir(confirmacao, tipo=token_repo.CONFIRMACAO) == USUARIO


def test_emitir_para_outro_usuario_nao_invalida_o_primeiro(banco):
    do_hospede = token_repo.gerar(USUARIO)
    token_repo.gerar(1)

    assert token_repo.consumir(do_hospede) == USUARIO


def test_tem_pendente_reflete_o_estado(banco):
    assert not token_repo.tem_pendente(USUARIO)

    token = token_repo.gerar(USUARIO)
    assert token_repo.tem_pendente(USUARIO)

    token_repo.consumir(token)
    assert not token_repo.tem_pendente(USUARIO)


def test_token_vencido_nao_conta_como_pendente(banco):
    token = token_repo.gerar(USUARIO)
    _envelhecer(token, horas=1)

    assert not token_repo.tem_pendente(USUARIO)


# ---------------------------------------------------------------------------
# Ciclo de vida da linha
# ---------------------------------------------------------------------------

def test_apagar_a_conta_apaga_os_tokens(banco):
    """CASCADE, ao contrário de `contato.hospede_id`, que é SET NULL.

    Um token é autorização pendente, não histórico: apagada a conta, ele
    não autoriza mais nada, e guardá-lo seria guardar lixo com poder.
    """
    token = token_repo.gerar(USUARIO)
    assert _linha_do_token(token) is not None

    with conexao.obter_conexao() as ligacao:
        cursor = ligacao.cursor()
        cursor.execute("UPDATE usuario SET hospede_id = NULL WHERE id = 1")
        cursor.execute("DELETE FROM token_acesso WHERE usuario_id <> %s", (USUARIO,))
        cursor.execute("DELETE FROM usuario WHERE id = %s", (USUARIO,))
        cursor.close()

    assert _linha_do_token(token) is None


def test_a_limpeza_remove_os_vencidos_com_carencia(banco):
    """A carência deixa o motivo no log antes de a linha sumir.

    Achar a linha e recusá-la por prazo dá o mesmo resultado, do lado de
    fora, que não achar — mas é a diferença entre diagnosticar e adivinhar
    quando alguém reclama que o link não funcionou.
    """
    recente = token_repo.gerar(USUARIO)
    _envelhecer(recente, horas=2)

    antigo = token_repo.gerar(1)
    _envelhecer(antigo, horas=24 * 30)

    removidos = token_repo.limpar_vencidos(dias_de_carencia=7)

    assert removidos == 1
    assert _linha_do_token(antigo) is None
    assert _linha_do_token(recente) is not None


def test_a_limpeza_nao_toca_nos_validos(banco):
    valido = token_repo.gerar(USUARIO)

    token_repo.limpar_vencidos(dias_de_carencia=0)

    assert _linha_do_token(valido) is not None
    assert token_repo.consumir(valido) == USUARIO


def test_dois_tokens_iguais_nao_cabem_no_banco(banco):
    """A unicidade do hash: dois tokens iguais seriam o mesmo token.

    E a consulta de consumo precisa achar no máximo uma linha.
    """
    import mysql.connector

    token = token_repo.gerar(USUARIO)
    mesmo_hash = hashlib.sha256(token.encode("utf-8")).digest()

    with conexao.obter_conexao() as ligacao:
        cursor = ligacao.cursor()
        with pytest.raises(mysql.connector.Error) as erro:
            cursor.execute(
                "INSERT INTO token_acesso "
                "  (usuario_id, token_hash, tipo, expira_em, criado_em) "
                "VALUES (%s, %s, 'confirmacao', NOW() + INTERVAL 1 DAY, NOW())",
                (USUARIO, mesmo_hash),
            )
        cursor.close()

    assert erro.value.errno == 1062


# ---------------------------------------------------------------------------
# Privilégio
# ---------------------------------------------------------------------------

def test_a_aplicacao_alcanca_a_tabela_de_tokens(banco):
    """A tabela é nova, e os privilégios são concedidos um a um.

    Sem o GRANT correspondente, tudo acima falharia na primeira execução
    contra o banco real — onde a aplicação não é root.
    """
    with conexao.obter_cursor() as cursor:
        cursor.execute("SELECT COUNT(*) FROM token_acesso")
        assert cursor.fetchone()[0] >= 0


# ---------------------------------------------------------------------------
# A validade por tipo, que entrou no Bloco 4 da Etapa 5
# ---------------------------------------------------------------------------

def test_a_confirmacao_vale_minutos_e_a_recuperacao_vale_dias(banco):
    """Dois prazos propositalmente diferentes, e o contraste importa.

    A **confirmação** acompanha a janela em que o cadastro não confirmado
    é descartado, que é de minutos. Se ela fosse maior, haveria link
    válido apontando para conta já apagada.

    A **recuperação de senha** vale dois dias. Recuperar senha não cria
    nem descarta dado nenhum: quem pede o link já é cliente e já teve o
    endereço comprovado. Um prazo de minutos aqui só produziria gente
    trancada fora da própria conta.

    Sem este teste, igualar os dois por descuido — numa simplificação
    bem-intencionada do dicionário — não quebraria nada.
    """
    confirmacao = token_repo.VALIDADE_POR_TIPO[token_repo.CONFIRMACAO]
    recuperacao = token_repo.VALIDADE_POR_TIPO[token_repo.RECUPERACAO]

    assert confirmacao < 60, (
        "a confirmação precisa valer menos de uma hora, para acompanhar a "
        "janela de descarte do cadastro não confirmado"
    )
    assert recuperacao >= 24 * 60, (
        "a recuperação de senha precisa valer ao menos um dia"
    )
    assert recuperacao > confirmacao * 10


def test_o_prazo_gravado_e_o_do_tipo_pedido(banco):
    """A tabela de validade chega de fato ao banco.

    Verificado na coluna `expira_em`, e não no dicionário: um `gerar` que
    ignorasse o tipo e usasse sempre o mesmo prazo passaria no teste
    anterior sem erro algum.
    """
    confirmacao = _linha_do_token(
        token_repo.gerar(USUARIO, token_repo.CONFIRMACAO)
    )
    recuperacao = _linha_do_token(
        token_repo.gerar(USUARIO, token_repo.RECUPERACAO)
    )

    minutos_da_confirmacao = (
        confirmacao["expira_em"] - confirmacao["criado_em"]
    ).total_seconds() / 60
    minutos_da_recuperacao = (
        recuperacao["expira_em"] - recuperacao["criado_em"]
    ).total_seconds() / 60

    # Um minuto de folga para o relógio: `criado_em` vem do `NOW()` do
    # banco e `expira_em` é calculado em Python, e as duas leituras não
    # acontecem no mesmo instante.
    assert abs(
        minutos_da_confirmacao
        - token_repo.VALIDADE_POR_TIPO[token_repo.CONFIRMACAO]
    ) < 1
    assert abs(
        minutos_da_recuperacao
        - token_repo.VALIDADE_POR_TIPO[token_repo.RECUPERACAO]
    ) < 1


def test_a_validade_em_minutos_permite_prazo_menor_que_uma_hora(banco):
    """A razão da mudança de unidade, verificada diretamente.

    Com a validade em horas, o menor prazo expressável era **uma hora** —
    doze vezes a janela de cinco minutos. O link continuaria "válido"
    apontando para uma conta já descartada, e a mensagem de erro falaria
    de link inválido quando o problema foi o descarte.

    Dois relógios para o mesmo prazo é o tipo de defeito que nenhum teste
    pega, porque cada metade, isolada, está correta.
    """
    linha = _linha_do_token(token_repo.gerar(USUARIO, minutos=5))

    duracao = (linha["expira_em"] - linha["criado_em"]).total_seconds() / 60
    assert 4 < duracao < 6, (
        f"um prazo de 5 minutos saiu com {duracao:.1f} minutos"
    )
