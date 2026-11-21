"""A conversa da reserva — Etapa 5, Bloco 17.

Um mural por reserva, entre o hóspede que tem conta e a equipe. Substitui o
formulário público de contato, que aceitava texto de qualquer pessoa sem
saber quem era: aqui quem escreve está logado, e a mensagem nasce presa a
uma reserva.

**O texto é dado pessoal** — quem escreve conta quando chega, com quem vem,
o que precisa — e fica cifrado com AES-256-GCM. A etiqueta da cifra amarra
o texto à reserva, à posição na conversa e ao autor: mover o bloco para
outra reserva, trocá-lo de lugar ou atribuí-lo a outra pessoa faz a
verificação falhar.

**Só acréscimo.** A aplicação tem SELECT e INSERT na tabela e UPDATE só na
coluna `lida_pela_equipe` (privilégio por coluna, em
`banco/03_usuario_app.sql`); o gatilho `trg_mensagem_antes_alterar` recusa
qualquer outra mudança e a volta de "lida" para "não lida". Ninguém edita
nem apaga o que foi dito.

**O dono vai no WHERE.** O hóspede só escreve e lê na reserva cujo
`hospede_id` é o da conta; a de outra pessoa responde como inexistente.

**A ordem é decidida com a reserva travada.** `SELECT ... FOR UPDATE` na
linha da reserva serializa duas mensagens simultâneas, e a unicidade
`(reserva_id, ordem)` é a segunda camada.
"""

from app.dados.conexao import obter_conexao
from app.dados.erros import ErroDeDados, executar
from app.seguranca import cripto

LIMITE_DO_TEXTO = 1000


class ErroDeMensagem(ErroDeDados):
    """A mensagem foi recusada; a frase diz o que fazer."""


# Consultas como constantes: nenhuma f-string chega a um `execute`.

_SQL_TRAVAR_DO_HOSPEDE = (
    "SELECT id FROM reserva WHERE id = %s AND hospede_id = %s FOR UPDATE"
)

_SQL_TRAVAR = "SELECT id FROM reserva WHERE id = %s FOR UPDATE"

_SQL_PROXIMA_ORDEM = (
    "SELECT COALESCE(MAX(ordem), 0) + 1 FROM mensagem WHERE reserva_id = %s"
)

_SQL_INSERIR = (
    "INSERT INTO mensagem "
    "(reserva_id, ordem, autor_id, da_equipe, texto_cif, texto_nonce, "
    " texto_tag, cripto_versao, lida_pela_equipe) "
    "VALUES (%s, %s, %s, %s, %s, %s, %s, 1, %s)"
)

_SQL_LISTAR = (
    "SELECT m.reserva_id, m.ordem, m.autor_id, m.da_equipe, m.texto_cif, "
    "       m.texto_nonce, m.texto_tag, m.lida_pela_equipe, m.criado_em, "
    "       u.login AS autor_login "
    "FROM mensagem m "
    "JOIN usuario u ON u.id = m.autor_id "
    "WHERE m.reserva_id = %s "
    "ORDER BY m.ordem"
)

_SQL_LISTAR_DO_HOSPEDE = (
    "SELECT m.reserva_id, m.ordem, m.autor_id, m.da_equipe, m.texto_cif, "
    "       m.texto_nonce, m.texto_tag, m.lida_pela_equipe, m.criado_em, "
    "       NULL AS autor_login "
    "FROM mensagem m "
    "JOIN reserva r ON r.id = m.reserva_id "
    "WHERE m.reserva_id = %s AND r.hospede_id = %s "
    "ORDER BY m.ordem"
)

_SQL_MARCAR_LIDAS = (
    "UPDATE mensagem SET lida_pela_equipe = TRUE "
    "WHERE reserva_id = %s AND da_equipe = FALSE "
    "  AND lida_pela_equipe = FALSE"
)

_SQL_NAO_LIDAS = (
    "SELECT DISTINCT reserva_id FROM mensagem "
    "WHERE da_equipe = FALSE AND lida_pela_equipe = FALSE"
)

_SQL_QUANTAS_DO_HOSPEDE = (
    "SELECT m.reserva_id, COUNT(*) FROM mensagem m "
    "JOIN reserva r ON r.id = m.reserva_id "
    "WHERE r.hospede_id = %s "
    "GROUP BY m.reserva_id"
)


def _limpar(texto) -> str:
    texto = texto.strip() if isinstance(texto, str) else ""
    if not texto:
        raise ErroDeMensagem("Escreva a mensagem antes de enviar.")
    if len(texto) > LIMITE_DO_TEXTO:
        raise ErroDeMensagem(
            f"A mensagem tem {len(texto)} caracteres; o limite é "
            f"{LIMITE_DO_TEXTO}."
        )
    return texto


def _gravar(cursor, reserva_id, autor_id, da_equipe, texto) -> int:
    executar(cursor, _SQL_PROXIMA_ORDEM, (reserva_id,))
    ordem = cursor.fetchone()[0]
    cif, nonce, tag = cripto.cifrar(
        texto, cripto.aad_mensagem(reserva_id, ordem, autor_id)
    )
    # A mensagem da equipe não espera leitura da equipe: nasce "lida".
    executar(
        cursor,
        _SQL_INSERIR,
        (reserva_id, ordem, autor_id, da_equipe, cif, nonce, tag, da_equipe),
    )
    return ordem


def enviar_do_hospede(reserva_id, hospede_id, autor_id, texto) -> bool:
    """O hóspede escreve na conversa da própria reserva.

    `False` quando a reserva não é dele ou não existe — sem distinguir,
    como nas demais operações do hóspede.
    """
    texto = _limpar(texto)
    with obter_conexao(usuario_id=autor_id) as conexao:
        cursor = conexao.cursor()
        try:
            executar(cursor, _SQL_TRAVAR_DO_HOSPEDE, (reserva_id, hospede_id))
            if cursor.fetchone() is None:
                return False
            _gravar(cursor, reserva_id, autor_id, False, texto)
            return True
        finally:
            cursor.close()


def enviar_da_equipe(reserva_id, autor_id, texto) -> bool:
    """A equipe responde. Responder também marca as do hóspede como lidas:
    quem responde leu."""
    texto = _limpar(texto)
    with obter_conexao(usuario_id=autor_id) as conexao:
        cursor = conexao.cursor()
        try:
            executar(cursor, _SQL_TRAVAR, (reserva_id,))
            if cursor.fetchone() is None:
                return False
            executar(cursor, _SQL_MARCAR_LIDAS, (reserva_id,))
            _gravar(cursor, reserva_id, autor_id, True, texto)
            return True
        finally:
            cursor.close()


def marcar_como_lidas(reserva_id, usuario_id=None) -> int:
    """A equipe leu sem responder. Devolve quantas marcou."""
    with obter_conexao(usuario_id=usuario_id) as conexao:
        cursor = conexao.cursor()
        try:
            executar(cursor, _SQL_MARCAR_LIDAS, (reserva_id,))
            return cursor.rowcount
        finally:
            cursor.close()


def _decifrar(linha) -> dict:
    """Uma mensagem que não passa na verificação aparece como ilegível, em
    vez de derrubar a conversa inteira."""
    try:
        texto = cripto.decifrar(
            linha["texto_cif"], linha["texto_nonce"], linha["texto_tag"],
            cripto.aad_mensagem(
                linha["reserva_id"], linha["ordem"], linha["autor_id"]
            ),
        )
        legivel = True
    except cripto.FalhaDeAutenticacao:
        texto, legivel = None, False
    return {
        "ordem": linha["ordem"],
        "da_equipe": bool(linha["da_equipe"]),
        "lida_pela_equipe": bool(linha["lida_pela_equipe"]),
        "criado_em": linha["criado_em"],
        "autor_login": linha["autor_login"],
        "texto": texto,
        "legivel": legivel,
    }


def _listar(comando, parametros, usuario_id) -> list:
    with obter_conexao(usuario_id=usuario_id) as conexao:
        cursor = conexao.cursor(dictionary=True)
        try:
            executar(cursor, comando, parametros)
            linhas = cursor.fetchall()
        finally:
            cursor.close()
    return [_decifrar(linha) for linha in linhas]


def conversa(reserva_id, usuario_id=None) -> list:
    """A conversa inteira, para a equipe, da mais antiga à mais nova."""
    return _listar(_SQL_LISTAR, (reserva_id,), usuario_id)


def conversa_do_hospede(reserva_id, hospede_id, usuario_id=None) -> list:
    """A conversa, para o dono da reserva. Sem o login de quem respondeu:
    para o hóspede, quem fala é a pousada."""
    return _listar(_SQL_LISTAR_DO_HOSPEDE, (reserva_id, hospede_id), usuario_id)


def reservas_com_nao_lidas(usuario_id=None) -> set:
    """As reservas com mensagem do hóspede que a equipe ainda não leu."""
    with obter_conexao(usuario_id=usuario_id) as conexao:
        cursor = conexao.cursor()
        try:
            executar(cursor, _SQL_NAO_LIDAS)
            return {linha[0] for linha in cursor.fetchall()}
        finally:
            cursor.close()


def quantas_por_reserva_do_hospede(hospede_id, usuario_id=None) -> dict:
    """Quantas mensagens cada reserva do hóspede tem, para o card."""
    with obter_conexao(usuario_id=usuario_id) as conexao:
        cursor = conexao.cursor()
        try:
            executar(cursor, _SQL_QUANTAS_DO_HOSPEDE, (hospede_id,))
            return {linha[0]: linha[1] for linha in cursor.fetchall()}
        finally:
            cursor.close()
