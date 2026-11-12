"""Tokens de acesso: prova de posse de um endereço de e-mail.

O sistema cria a conta **inativa** e envia um link. Só a abertura do link
ativa a conta — e abrir o link exige ter recebido a mensagem, o que só
acontece para quem é dono do endereço.

Avisar não bastaria. Se o link aparecesse na tela do cadastro, bastaria
digitar o endereço de outra pessoa para criar conta no nome dela. **O
e-mail não informa: ele prova.**

---

**O banco guarda o hash, nunca o token.** Mesma lógica da senha: quem
obtiver leitura do banco não consegue abrir link nenhum, porque teria o
hash e o hash não volta ao token.

**Por que SHA-256 aqui, e Argon2id na senha.** O token vem de
`secrets.token_urlsafe(32)` e carrega 256 bits de aleatoriedade — não há
dicionário nem força bruta contra isso. O Argon2id existe para proteger
segredos de **baixa** entropia, que pessoas escolhem e repetem; gastar
0,658 segundo por verificação num valor já impossível de adivinhar seria
desperdício, e transformaria a abertura de um link numa espera.

**Prazo por tipo.** O link de confirmação de cadastro vale minutos, e
acompanha a janela em que os dados não confirmados são descartados — ver
`VALIDADE_POR_TIPO`. O de recuperação de senha vale dois dias.

**Uso único.** Um link já aberto não abre de novo, mesmo dentro do prazo.
Sem isso, um link que vazasse depois de usado — no histórico do
navegador, num encaminhamento de e-mail — continuaria valendo.

**Resposta uniforme.** `consumir` devolve `None` para token inexistente,
vencido e já consumido. Quem está sondando não descobre em qual dos três
casos caiu, e portanto não aprende se um token existe.

**Emitir um novo invalida os pendentes.** Quem pede o link de novo porque
o primeiro não chegou não fica com dois links válidos circulando.
"""

import datetime as dt
import hashlib
import logging
import secrets

from app.dados.conexao import obter_conexao, obter_cursor

_registrador = logging.getLogger(__name__)

CONFIRMACAO = "confirmacao"
RECUPERACAO = "recuperacao"
TIPOS = (CONFIRMACAO, RECUPERACAO)

# Quantos MINUTOS o link vale, por tipo.
#
# A unidade mudou de horas para minutos no Bloco 4 da Etapa 5, quando a
# janela do cadastro não confirmado passou a ser de 5 minutos. Com horas,
# o prazo mais curto que se podia expressar era uma hora — doze vezes
# maior que a janela, e o link continuaria "válido" apontando para uma
# conta que já havia sido descartada.
#
# **Dois relógios diferentes para o mesmo prazo é fábrica de defeito**, e
# este é o tipo de defeito que não aparece em teste nenhum: o token passa
# na verificação, a conta não existe, e a mensagem de erro fala de link
# inválido quando o problema foi o descarte.
#
# Os dois tipos têm prazos propositalmente distintos:
#
# **Confirmação** acompanha a janela de descarte, e quem a define é a
# configuração da aplicação — por isso o valor aqui é apenas o padrão de
# quem chamar sem dizer nada. `app/servicos/cadastro.py` passa o valor
# configurado.
#
# **Recuperação de senha** vale dois dias, e não cinco minutos. A
# diferença não é descuido: recuperar senha não cria nem descarta dado
# nenhum. Quem pede o link já é cliente, já teve o endereço comprovado, e
# um prazo curto aqui só produziria gente trancada fora da própria conta.
VALIDADE_POR_TIPO = {
    CONFIRMACAO: 15,
    RECUPERACAO: 48 * 60,
}

# Mantido para quem chamar sem indicar o tipo.
VALIDADE_PADRAO = VALIDADE_POR_TIPO[CONFIRMACAO]

# Bytes de aleatoriedade. 32 bytes são 256 bits — a mesma ordem de grandeza
# de uma chave AES-256, e muito além do que qualquer força bruta alcança.
BYTES_DO_TOKEN = 32


class ErroDeToken(Exception):
    """Não foi possível emitir ou consumir o token."""


_SQL_INVALIDAR_PENDENTES = (
    "UPDATE token_acesso SET consumido_em = NOW() "
    "WHERE usuario_id = %s AND tipo = %s AND consumido_em IS NULL"
)

_SQL_INSERIR = (
    "INSERT INTO token_acesso "
    "  (usuario_id, token_hash, tipo, expira_em, criado_em) "
    "VALUES (%s, %s, %s, %s, NOW())"
)

_SQL_POR_HASH = (
    "SELECT id, usuario_id, tipo, expira_em, consumido_em "
    "FROM token_acesso WHERE token_hash = %s"
)

_SQL_CONSUMIR = (
    "UPDATE token_acesso SET consumido_em = NOW() "
    "WHERE id = %s AND consumido_em IS NULL"
)

_SQL_LIMPAR_VENCIDOS = (
    "DELETE FROM token_acesso "
    "WHERE expira_em < NOW() - INTERVAL %s DAY"
)

_SQL_PENDENTES_DO_USUARIO = (
    "SELECT COUNT(*) FROM token_acesso "
    "WHERE usuario_id = %s AND tipo = %s AND consumido_em IS NULL "
    "  AND expira_em > NOW()"
)


def _resumir(token: str) -> bytes:
    """SHA-256 do token, em bytes.

    Devolve bytes e não texto hexadecimal porque a coluna é VARBINARY(32):
    guardar o hexadecimal ocuparia o dobro e não acrescentaria nada.
    """
    return hashlib.sha256(token.encode("utf-8")).digest()


def gerar(usuario_id, tipo: str = CONFIRMACAO, minutos: int = None,
          usuario_atuante=None) -> str:
    """Emite um token e devolve o valor em claro.

    **É a única vez que o token existe fora do e-mail.** Quem chama precisa
    usá-lo imediatamente para montar o link; ele não é recuperável depois,
    porque o banco só guarda o hash.
    """
    if tipo not in TIPOS:
        raise ErroDeToken(f"Tipo de token desconhecido: {tipo}")
    if minutos is None:
        minutos = VALIDADE_POR_TIPO[tipo]
    if minutos < 1:
        raise ErroDeToken("A validade do token precisa ser de ao menos um minuto.")

    token = secrets.token_urlsafe(BYTES_DO_TOKEN)
    expira_em = dt.datetime.now() + dt.timedelta(minutes=minutos)

    with obter_conexao(usuario_id=usuario_atuante) as conexao:
        cursor = conexao.cursor()
        try:
            # Os pendentes do mesmo tipo morrem aqui. Quem pede o link de
            # novo porque o primeiro não chegou não fica com dois links
            # válidos circulando.
            cursor.execute(_SQL_INVALIDAR_PENDENTES, (usuario_id, tipo))

            cursor.execute(
                _SQL_INSERIR, (usuario_id, _resumir(token), tipo, expira_em)
            )
        finally:
            cursor.close()

    # O identificador do usuário pode ir para o log; o token, nunca.
    _registrador.info("token de %s emitido para o usuário %s", tipo, usuario_id)
    return token


def consumir(token: str, tipo: str = CONFIRMACAO, usuario_atuante=None):
    """Valida e queima o token. Devolve o identificador do usuário, ou None.

    **Devolve None para os três casos de falha** — token inexistente,
    vencido e já consumido. Quem está sondando não descobre em qual deles
    caiu, e portanto não aprende se um token existe.

    O consumo é uma atualização condicional (`WHERE consumido_em IS NULL`)
    e não uma leitura seguida de escrita: duas aberturas simultâneas do
    mesmo link não podem ambas ter sucesso.
    """
    if not token or tipo not in TIPOS:
        return None

    with obter_conexao(usuario_id=usuario_atuante) as conexao:
        cursor = conexao.cursor(dictionary=True)
        try:
            cursor.execute(_SQL_POR_HASH, (_resumir(token),))
            linha = cursor.fetchone()

            if linha is None:
                _registrador.info("token apresentado não existe")
                return None

            if linha["tipo"] != tipo:
                _registrador.info("token apresentado é de outro tipo")
                return None

            if linha["consumido_em"] is not None:
                _registrador.info("token apresentado já havia sido consumido")
                return None

            if linha["expira_em"] <= dt.datetime.now():
                _registrador.info("token apresentado está vencido")
                return None

            cursor.close()
            cursor = conexao.cursor()

            # Condicional: se outra requisição consumiu entre a leitura e
            # esta linha, nenhuma fila é afetada e o token não vale duas
            # vezes.
            cursor.execute(_SQL_CONSUMIR, (linha["id"],))
            if cursor.rowcount != 1:
                _registrador.info("token consumido por outra requisição")
                return None

        finally:
            cursor.close()

    _registrador.info("token consumido pelo usuário %s", linha["usuario_id"])
    return linha["usuario_id"]


def conferir(token: str, tipo: str = CONFIRMACAO, usuario_atuante=None):
    """Diz a quem o token pertence **sem queimá-lo**. Devolve id ou None.

    Existe por causa de um passo do fluxo de recuperação de senha. A
    pessoa abre o link e precisa ver o formulário; só ao enviar a senha
    nova é que o token deve ser consumido.

    Se a abertura da página consumisse, o link morreria antes de servir
    para alguma coisa — e quem fechasse a aba sem querer teria de pedir
    outro.

    **A mesma resposta uniforme de `consumir`**: `None` para token
    inexistente, vencido, já consumido e de outro tipo. Quem está
    sondando não aprende se um token existe — e esta função, por não
    consumir, seria a ferramenta óbvia para sondar, se respondesse
    diferente.

    **Não substitui `consumir`.** Conferir e consumir são operações
    distintas, e é o `consumir` que decide: ele é uma atualização
    condicional, e duas aberturas simultâneas do mesmo link não podem
    ambas ter sucesso. Quem usar só esta função para autorizar algo abre
    uma janela de corrida entre a conferência e o efeito.
    """
    if not token or tipo not in TIPOS:
        return None

    with obter_cursor(usuario_id=usuario_atuante, dicionario=True) as cursor:
        cursor.execute(_SQL_POR_HASH, (_resumir(token),))
        linha = cursor.fetchone()

    if linha is None:
        return None
    if linha["tipo"] != tipo:
        return None
    if linha["consumido_em"] is not None:
        return None
    if linha["expira_em"] <= dt.datetime.now():
        return None

    return linha["usuario_id"]


def tem_pendente(usuario_id, tipo: str = CONFIRMACAO, usuario_atuante=None) -> bool:
    """Se há token válido e não consumido para esta conta e tipo."""
    with obter_cursor(usuario_id=usuario_atuante) as cursor:
        cursor.execute(_SQL_PENDENTES_DO_USUARIO, (usuario_id, tipo))
        return cursor.fetchone()[0] > 0


def limpar_vencidos(dias_de_carencia: int = 7, usuario_atuante=None) -> int:
    """Remove tokens vencidos há mais de alguns dias. Devolve quantos saíram.

    A carência existe para que um token recém-vencido ainda seja
    **encontrado** no consumo: achar a linha e recusá-la por prazo é o
    mesmo resultado, do lado de fora, que não achar — mas deixa o motivo
    no log, e é a diferença entre diagnosticar e adivinhar quando alguém
    reclama que o link não funcionou.
    """
    with obter_cursor(usuario_id=usuario_atuante) as cursor:
        cursor.execute(_SQL_LIMPAR_VENCIDOS, (dias_de_carencia,))
        return cursor.rowcount
