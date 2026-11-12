"""Acesso à tabela de usuários da equipe.

Toda consulta é parametrizada, sem exceção. Nenhuma montagem de SQL por
concatenação de texto, f-string ou operador de formatação — é a regra 3 do
projeto, e ela vale inclusive para consultas administrativas.

A diferença não é estilística. Com parâmetro, o valor viaja separado do
comando e o servidor nunca o interpreta como SQL: um login digitado como
`' OR '1'='1` é procurado literalmente, como se fosse um nome esquisito.
Com concatenação, ele viraria parte da consulta.
"""

import base64
import datetime as dt
import logging

import mysql.connector

from app.dados.conexao import obter_conexao, obter_cursor
from app.dados.erros import ERRO_DE_DUPLICIDADE, traduzir
from app.seguranca import cripto

_registrador = logging.getLogger(__name__)

# Perfis aceitos, iguais ao ENUM da coluna. Repetidos aqui para que um
# perfil inventado seja recusado em Python, com mensagem legível, antes de
# virar erro de truncamento do MySQL.
PERFIS = ("administrador", "recepcionista", "hospede")

# Limite da coluna `login`, VARCHAR(50).
TAMANHO_MAXIMO_DO_LOGIN = 50

# Limite da coluna `nome`, VARCHAR(100).
TAMANHO_MAXIMO_DO_NOME = 100


class ErroDeUsuario(Exception):
    """Não foi possível criar ou alterar a conta."""


class LoginEmUso(ErroDeUsuario):
    """Já existe conta com este login.

    **Nunca repassar esta exceção ao visitante.** No autocadastro ela
    significa que o endereço de e-mail já tem conta, e dizer isso
    transformaria o formulário num oráculo de enumeração: bastaria
    submeter endereços e ler as respostas para descobrir quem é cliente
    da hospedaria. Quem chama no fluxo público trata internamente e
    devolve a mesma resposta de sempre.
    """

# As consultas são constantes de módulo, escritas por extenso. Nenhuma
# f-string chega ao `execute` — nem para montar lista de colunas, que seria
# inofensivo em si.
#
# O motivo é tornar o perigo visível. Uma f-string num `execute` é o
# padrão exato da injeção de SQL, e misturar usos inofensivos com o
# perigoso treina o leitor a ignorá-lo. Com esta regra, qualquer f-string
# que apareça aqui é anomalia, e tanto o Bandit quanto a revisão por pares
# a apontam de imediato.

_SQL_POR_LOGIN = (
    "SELECT id, login, nome, senha_hash, perfil, hospede_id, ativo, "
    "       ultimo_acesso, criado_em, alterado_em "
    "FROM usuario WHERE login = %s LIMIT 1"
)

_SQL_POR_ID = (
    "SELECT id, login, nome, senha_hash, perfil, hospede_id, ativo, "
    "       ultimo_acesso, criado_em, alterado_em "
    "FROM usuario WHERE id = %s LIMIT 1"
)

_SQL_LISTAR = (
    "SELECT id, login, nome, senha_hash, perfil, hospede_id, ativo, "
    "       ultimo_acesso, criado_em, alterado_em "
    "FROM usuario ORDER BY nome"
)


# As contas candidatas ao descarte.
#
# **O prazo é lido do token, e não da data de criação da conta.** A
# primeira versão deste comando comparava `usuario.criado_em` com um
# limite calculado em Python, e tinha dois problemas.
#
# O primeiro é que não funcionava: `trg_usuario_antes_alterar` faz
# `SET NEW.criado_em = OLD.criado_em`, protegendo a data de criação de
# qualquer alteração. A trigger está certa — data de criação não se
# reescreve — e o efeito foi que nenhum teste conseguia envelhecer uma
# conta, e o descarte passava sem nunca descartar nada.
#
# O segundo é mais sério, e é o mesmo que motivou passar a validade do
# token de horas para minutos: **dois relógios para o mesmo prazo.** Um
# link válido apontando para uma conta já descartada, ou uma conta
# descartada enquanto o link ainda valia, são dois estados impossíveis de
# explicar a quem reclama — e nenhum teste os pega, porque cada metade,
# isolada, está correta.
#
# Com o prazo no token há um relógio só: o mesmo valor decide se o link
# abre e se os dados ficam. Não existe janela em que os dois discordem.
#
# Cada condição fecha uma porta, e nenhuma é dispensável:
#
#   ativo = FALSE          nunca toca conta que funciona
#   perfil = 'hospede'     redundante por invariante do banco — ver abaixo
#   EXISTS (token)         nunca toca conta que nasceu sem confirmação a
#                          fazer — é o caso da conta inativa criada pelo
#                          administrador, que espera ativação manual e não
#                          tem prazo nenhum correndo
#   NOT EXISTS (válido)    só descarta depois de o prazo vencer
#   NOT EXISTS (reserva)   nunca toca hóspede com histórico
#   COUNT(conta) = 1       nunca toca hóspede que tenha outra conta
#
# A penúltima e a última são as menos óbvias. Um hóspede pode ter mais de
# uma conta — foi o caminho do ataque que `existe_para_hospede` fechou —, e
# sem a contagem o descarte de uma conta inativa levaria consigo o registro
# de hóspede de que a conta ATIVA depende.
#
# ---
#
# **`perfil = 'hospede'` é redundante, e fica.** A sabotagem que a removeu
# não quebrou teste nenhum, e a investigação mostrou por quê: a trigger
# `trg_usuario_antes_inserir` recusa `perfil <> 'hospede'` com
# `hospede_id` preenchido. Como esta consulta já exige
# `hospede_id IS NOT NULL`, o banco garante que toda candidata é de perfil
# hóspede — a condição em Python nunca descarta nada.
#
# Fica porque a redundância é contra o futuro, não contra o presente: se
# algum dia a trigger for afrouxada, esta linha passa a ser a única
# proteção. O que não pode ficar é a redundância **silenciosa**, e por
# isso `test_caminho_unico` verifica que a invariante existe na trigger.
# Se ela sair, o teste avisa que esta condição deixou de ser enfeite.
#
# **`ativo = FALSE` aparece aqui E no DELETE, e não é a mesma coisa.**
# Aqui é critério de seleção: conta que funciona não é nem considerada.
# No DELETE é guarda de corrida: entre a seleção e a remoção, alguém pode
# ter clicado no link de confirmação, e sem a condição lá a conta de quem
# acabou de confirmar seria apagada.
#
# As duas sabotagens correspondentes escaparam uma da outra — cada metade
# cobria a ausência da outra —, e a resposta não foi remover a
# duplicação, foi dar a cada uma o seu teste: um confere que a seleção é
# precisa, outro executa o DELETE contra conta ativa e exige zero linhas.
_SQL_CANDIDATAS_AO_DESCARTE = (
    "SELECT u.id AS usuario_id, u.hospede_id "
    "FROM usuario u "
    "WHERE u.ativo = FALSE "
    "  AND u.perfil = 'hospede' "
    "  AND u.hospede_id IS NOT NULL "
    "  AND EXISTS ("
    "        SELECT 1 FROM token_acesso t "
    "        WHERE t.usuario_id = u.id AND t.tipo = 'confirmacao'"
    "      ) "
    "  AND NOT EXISTS ("
    "        SELECT 1 FROM token_acesso t "
    "        WHERE t.usuario_id = u.id AND t.tipo = 'confirmacao' "
    "          AND t.consumido_em IS NULL AND t.expira_em > NOW()"
    "      ) "
    "  AND NOT EXISTS ("
    "        SELECT 1 FROM reserva r WHERE r.hospede_id = u.hospede_id"
    "      ) "
    "  AND ("
    "        SELECT COUNT(*) FROM usuario o WHERE o.hospede_id = u.hospede_id"
    "      ) = 1"
)

_SQL_APAGAR_CONTA = "DELETE FROM usuario WHERE id = %s AND ativo = FALSE"

_SQL_CONTA_DO_HOSPEDE = (
    "SELECT COUNT(*) FROM usuario WHERE hospede_id = %s"
)

_SQL_INSERIR = (
    "INSERT INTO usuario "
    "  (login, nome, senha_hash, perfil, hospede_id, ativo, "
    "   criado_em, alterado_em) "
    "VALUES (%s, %s, %s, %s, %s, %s, NOW(), NOW())"
)

_SQL_ATIVAR = (
    "UPDATE usuario SET ativo = TRUE, alterado_em = NOW() "
    "WHERE id = %s AND ativo = FALSE"
)


# ---------------------------------------------------------------------------
# Login a partir do e-mail
# ---------------------------------------------------------------------------

def login_de_email(email: str) -> str:
    """Devolve o login derivado de um endereço de e-mail.

    **Por que o login não é o próprio e-mail.** A coluna `login` é texto
    claro e tem restrição de unicidade. Guardar o endereço ali desfaria o
    trabalho da tabela `hospede`, que cifra o mesmo dado com AES-GCM: o
    e-mail estaria em claro na linha ao lado. E um UNIQUE sobre e-mail em
    claro é, por si, um oráculo de enumeração.

    O login é a **impressão digital** do endereço — o mesmo HMAC-SHA256
    que `hospede.email_busca` usa para permitir busca sem decifrar. Ele é
    irreversível, e a tabela `usuario` nunca vê um e-mail.

    Em base64url sem preenchimento: 32 bytes viram 43 caracteres, que
    cabem no VARCHAR(50) da coluna. O `=` do preenchimento é removido
    porque não carrega informação e só gastaria espaço.

    Quem digita o endereço na tela de entrada chega a este mesmo valor,
    porque a normalização (`NFKC`, sem espaços nas pontas, em minúsculas)
    é a mesma de `cripto.indice`.
    """
    if not isinstance(email, str) or not email.strip():
        raise ErroDeUsuario("O endereço de e-mail é obrigatório.")

    digitais = cripto.indice(email)
    return base64.urlsafe_b64encode(digitais).decode("ascii").rstrip("=")


def parece_email(valor) -> bool:
    """Se o valor digitado deve ser tratado como endereço de e-mail.

    Critério deliberadamente grosseiro: a presença de `@`. Não é
    validação de endereço — é apenas a decisão de qual dos dois caminhos
    de busca seguir. Os logins da equipe (`admin`, `recepcao`) não têm
    `@` e seguem literais.
    """
    return isinstance(valor, str) and "@" in valor


def buscar_por_login_ou_email(valor: str, usuario_id=None):
    """Busca a conta pelo que a pessoa digitou na tela de entrada.

    Um único caminho de busca, nos dois casos: o valor é convertido
    quando tem `@` e usado literalmente quando não tem. Nenhum dos dois
    faz consulta a mais, de modo que o tempo de resposta não distingue um
    login de equipe de um endereço de hóspede.
    """
    if not isinstance(valor, str) or not valor.strip():
        return None

    if parece_email(valor):
        try:
            return buscar_por_login(login_de_email(valor), usuario_id=usuario_id)
        except ErroDeUsuario:
            return None

    return buscar_por_login(valor, usuario_id=usuario_id)


def buscar_por_login(login: str, usuario_id=None):
    """Devolve o usuário com esse login, ou None.

    Inclui contas inativas de propósito: quem decide o que fazer com elas
    é a rota de login, que precisa recusar a entrada com a MESMA mensagem
    usada para senha errada. Filtrar aqui faria a conta desativada
    responder diferente da inexistente, e a diferença revelaria quais
    contas existem.
    """
    if not isinstance(login, str) or not login.strip():
        return None

    with obter_cursor(usuario_id=usuario_id, dicionario=True) as cursor:
        cursor.execute(_SQL_POR_LOGIN, (login.strip(),))
        return cursor.fetchone()


def existe_para_hospede(hospede_id, usuario_id=None) -> bool:
    """Se este hóspede já tem alguma conta de acesso, de qualquer login.

    **Esta pergunta não é a mesma que "existe conta com este login".** A
    diferença fecha um ataque real no autocadastro público.

    A massa fictícia tem a conta `joao` ligada ao hóspede 1, com login de
    apelido e não derivado do e-mail. Sem esta verificação, alguém que
    soubesse o endereço desse hóspede poderia cadastrá-lo de novo: o
    login do autocadastro é o HMAC do e-mail e não colide com `joao`, de
    modo que a verificação por login não veria nada, e uma **segunda**
    conta nasceria apontando para o mesmo `hospede_id`.

    A conta nasceria inativa, mas o link de ativação iria para o endereço
    do hóspede de verdade — que, achando ser o próprio cadastro, abriria
    o link e ativaria uma conta cuja senha quem atacou escolheu. Dali em
    diante, as reservas dele estariam visíveis.

    Por isso o autocadastro pergunta pelo hóspede, e não pelo login.
    """
    with obter_cursor(usuario_id=usuario_id) as cursor:
        cursor.execute(_SQL_CONTA_DO_HOSPEDE, (hospede_id,))
        return cursor.fetchone()[0] > 0


def buscar_por_id(identificador, usuario_id=None):
    with obter_cursor(usuario_id=usuario_id, dicionario=True) as cursor:
        cursor.execute(_SQL_POR_ID, (identificador,))
        return cursor.fetchone()


def listar(usuario_id=None) -> list:
    with obter_cursor(usuario_id=usuario_id, dicionario=True) as cursor:
        cursor.execute(_SQL_LISTAR)
        return cursor.fetchall()


def registrar_acesso(identificador, usuario_id=None) -> None:
    """Marca o momento do último acesso bem-sucedido."""
    with obter_conexao(usuario_id=usuario_id) as conexao:
        cursor = conexao.cursor()
        try:
            cursor.execute(
                "UPDATE usuario SET ultimo_acesso = NOW() WHERE id = %s",
                (identificador,),
            )
        finally:
            cursor.close()


def atualizar_hash(identificador, novo_hash: str, usuario_id=None) -> None:
    """Regrava a senha com os parâmetros de calibração atuais.

    Chamada no login bem-sucedido, quando o hash guardado foi gerado com
    parâmetros mais fracos. É o único momento em que a senha em claro está
    disponível para recalcular — fora dele, só restaria forçar todo mundo
    a trocar de senha.
    """
    with obter_conexao(usuario_id=usuario_id) as conexao:
        cursor = conexao.cursor()
        try:
            cursor.execute(
                "UPDATE usuario SET senha_hash = %s WHERE id = %s",
                (novo_hash, identificador),
            )
        finally:
            cursor.close()


# ---------------------------------------------------------------------------
# Escrita
# ---------------------------------------------------------------------------

def criar(login: str, nome: str, senha_hash: str, perfil: str,
          hospede_id=None, ativo: bool = False, usuario_id=None) -> int:
    """Cria a conta e devolve o identificador.

    **Recebe o hash, nunca a senha.** O parâmetro se chama `senha_hash` e
    o valor é gravado como veio. Não existe caminho neste módulo que
    aceite senha em claro e a transforme — quem chama precisa ter passado
    por `senhas.gerar_hash`, o que torna impossível gravar senha legível
    por descuido ou por uma chamada apressada.

    A verificação do formato do hash não é cosmética: um `senha_hash` que
    chegasse em claro seria gravado e **não autenticaria ninguém**,
    porque `senhas.verificar` não reconheceria o formato. A conta ficaria
    inutilizável e a senha, exposta. Recusar aqui transforma um erro
    silencioso em exceção.

    **`ativo` é False por omissão.** O padrão fica do lado seguro: uma
    chamada que esqueça o parâmetro cria conta que não entra, e não conta
    que entra sem o endereço ter sido comprovado. O inverso seria um
    padrão que falha para o lado errado.
    """
    if not isinstance(login, str) or not login.strip():
        raise ErroDeUsuario("O login é obrigatório.")
    login = login.strip()
    if len(login) > TAMANHO_MAXIMO_DO_LOGIN:
        raise ErroDeUsuario(
            f"O login tem {len(login)} caracteres; o limite é "
            f"{TAMANHO_MAXIMO_DO_LOGIN}."
        )

    if not isinstance(nome, str) or not nome.strip():
        raise ErroDeUsuario("O nome é obrigatório.")
    nome = nome.strip()
    if len(nome) > TAMANHO_MAXIMO_DO_NOME:
        raise ErroDeUsuario(
            f"O nome tem {len(nome)} caracteres; o limite é "
            f"{TAMANHO_MAXIMO_DO_NOME}."
        )

    if not isinstance(senha_hash, str) or not senha_hash.startswith("$argon2"):
        raise ErroDeUsuario(
            "criar() recebe o hash da senha, não a senha. "
            "Use senhas.gerar_hash antes de chamar."
        )

    if perfil not in PERFIS:
        raise ErroDeUsuario(f"Perfil desconhecido: {perfil!r}")

    # O vínculo com `hospede` não é conferido aqui de propósito. A regra
    # mora na trigger `trg_usuario_antes_inserir`, que recusa perfil
    # hospede sem hospede_id, e vale para qualquer caminho de escrita —
    # inclusive um INSERT manual no cliente do MySQL. Duplicar a regra em
    # Python criaria dois lugares para mantê-la em sincronia.
    with obter_conexao(usuario_id=usuario_id) as conexao:
        cursor = conexao.cursor()
        try:
            try:
                cursor.execute(
                    _SQL_INSERIR,
                    (login, nome, senha_hash, perfil, hospede_id, bool(ativo)),
                )
            except mysql.connector.Error as erro:
                if getattr(erro, "errno", None) == ERRO_DE_DUPLICIDADE:
                    raise LoginEmUso(
                        "Já existe uma conta com este login."
                    ) from erro
                raise traduzir(erro) from erro

            identificador = cursor.lastrowid
        finally:
            cursor.close()

    # O login vai para o log; no caso do hóspede ele é o HMAC do e-mail,
    # e portanto não revela o endereço. A senha e o hash nunca vão.
    _registrador.info(
        "conta criada: perfil %s, ativa %s, login %s",
        perfil, bool(ativo), login,
    )
    return identificador


def ativar(identificador, usuario_id=None) -> bool:
    """Ativa a conta. Devolve se alguma linha mudou.

    A condição `AND ativo = FALSE` está no comando e não numa leitura
    anterior: duas aberturas simultâneas do mesmo link de confirmação não
    podem ambas ativar, e a segunda descobre isso pelo número de linhas
    afetadas em vez de por uma leitura que já envelheceu.
    """
    with obter_conexao(usuario_id=usuario_id) as conexao:
        cursor = conexao.cursor()
        try:
            cursor.execute(_SQL_ATIVAR, (identificador,))
            mudou = cursor.rowcount == 1
        finally:
            cursor.close()

    if mudou:
        _registrador.info("conta %s ativada", identificador)
    return mudou


def descartar_nao_confirmados(usuario_id=None) -> int:
    """Remove cadastros cujo prazo de confirmação venceu. Devolve quantos.

    **Não recebe janela.** O prazo está no token, que é o mesmo valor que
    decide se o link abre. Receber um número aqui recriaria o segundo
    relógio que esta versão existe para eliminar.

    **Por que apagar, e não apenas deixar vencido.** Um cadastro não
    confirmado prende o endereço de e-mail e o documento de alguém. Se a
    pessoa digitou o endereço errado, a segunda tentativa com o documento
    certo seria recusada — e recusada **em silêncio**, pela regra de
    resposta uniforme, sem que nem ela nem a recepção conseguissem ver o
    motivo. Uma letra errada trancaria um CPF para sempre.

    Há também a razão de princípio: guardar por tempo indeterminado dado
    pessoal que nunca foi confirmado é guardar dado sem ter prova de que a
    pessoa quis dá-lo.

    **O que se perde, e como se compensa.** A linha do token sai junto,
    por `ON DELETE CASCADE`. Quem clicar no link um minuto depois do
    descarte recebe a resposta de token inexistente, e não de token
    vencido — para quem clicou é a mesma tela, mas o log perderia o
    motivo. Por isso o descarte **registra a si mesmo** no log, com o
    identificador da conta: a reclamação "meu link não funcionou" continua
    respondível, pela linha do descarte em vez da do consumo.

    **A ordem das remoções é imposta pela chave estrangeira.**
    `fk_usuario_hospede` é `ON DELETE RESTRICT`: não se apaga hóspede que
    tenha conta. Então a conta sai primeiro, e o hóspede depois.

    O `RESTRICT` deixou de ser obstáculo e passou a ser rede: se a seleção
    das candidatas tivesse um defeito e escolhesse um hóspede com conta
    ativa, a remoção do hóspede **falharia** em vez de apagar a conta de
    alguém. A regra do banco protege contra o erro do código que a chama.

    Uma transação por cadastro, e não uma só para todos: um cadastro que
    não possa ser removido não deve impedir a remoção dos outros.
    """
    with obter_cursor(usuario_id=usuario_id, dicionario=True) as cursor:
        cursor.execute(_SQL_CANDIDATAS_AO_DESCARTE)
        candidatas = cursor.fetchall()

    if not candidatas:
        return 0

    from app.dados import hospede_repo

    descartados = 0
    for linha in candidatas:
        try:
            with obter_conexao(usuario_id=usuario_id) as conexao:
                cursor = conexao.cursor()
                try:
                    cursor.execute(_SQL_APAGAR_CONTA, (linha["usuario_id"],))
                    if cursor.rowcount != 1:
                        # A conta foi ativada entre a seleção e aqui. É o
                        # caso de quem clicou no link no último segundo, e
                        # a condição `ativo = FALSE` no DELETE é o que
                        # impede que a corrida apague a conta de alguém que
                        # acabou de confirmar.
                        _registrador.info(
                            "conta %s não foi descartada: deixou de estar inativa",
                            linha["usuario_id"],
                        )
                        continue
                finally:
                    cursor.close()

            hospede_repo.excluir(linha["hospede_id"], usuario_id=usuario_id)
            descartados += 1

            # Registrado uma a uma, e não só o total: é esta linha que
            # responde a "meu link não funcionou" depois de o token ter
            # sido apagado junto com a conta.
            _registrador.info(
                "cadastro não confirmado descartado: conta %s, hóspede %s",
                linha["usuario_id"], linha["hospede_id"],
            )
        except Exception:  # noqa: BLE001
            _registrador.exception(
                "falha ao descartar o cadastro não confirmado %s",
                linha["usuario_id"],
            )

    return descartados
