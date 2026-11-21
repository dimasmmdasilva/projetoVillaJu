"""As consultas dos relatórios — Etapa 5, Bloco 18a (RF06 e RF12).

Só leitura, e só números: nenhuma consulta daqui traz nome, documento ou
texto de mensagem. O relatório diz quanto, nunca quem.

O período chega já validado, como `[inicio, fim)` — semiaberto, como as
reservas: a noite de `fim` não entra.
"""

from app.dados.conexao import obter_cursor

# Noites ocupadas por chalé no período. Ocupa a reserva confirmada ou
# concluída — a pendente ainda pode cair no prazo do Pix. A estadia que
# atravessa a borda do período conta só as noites que caem dentro dele:
# `LEAST(saída, fim) - GREATEST(entrada, início)`.
#
# Os chalés ativos entram todos, mesmo sem nenhuma noite: um chalé vazio é
# justamente o que o relatório precisa mostrar.
_SQL_OCUPACAO = (
    "SELECT c.id, c.nome, "
    "       COALESCE(SUM(DATEDIFF(LEAST(r.data_saida, %s), "
    "                             GREATEST(r.data_entrada, %s))), 0) "
    "         AS noites "
    "FROM chale c "
    "LEFT JOIN reserva r "
    "       ON r.chale_id = c.id "
    "      AND r.situacao IN ('confirmada', 'concluida') "
    "      AND r.data_entrada < %s AND r.data_saida > %s "
    "WHERE c.ativo = TRUE "
    "GROUP BY c.id, c.nome "
    "ORDER BY c.nome"
)

# As reservas cuja estadia toca o período, por situação e, nas canceladas,
# por motivo.
_SQL_RESERVAS = (
    "SELECT situacao, motivo_cancelamento, COUNT(*) "
    "FROM reserva "
    "WHERE data_entrada < %s AND data_saida > %s "
    "GROUP BY situacao, motivo_cancelamento"
)

# O RF12 reescrito no Bloco 17: o volume de mensagens da conversa,
# escritas no período. A contagem, nunca o texto.
_SQL_MENSAGENS = (
    "SELECT da_equipe, COUNT(*) FROM mensagem "
    "WHERE criado_em >= %s AND criado_em < %s "
    "GROUP BY da_equipe"
)


def ocupacao(inicio, fim, usuario_id=None) -> list:
    with obter_cursor(usuario_id=usuario_id, dicionario=True) as cursor:
        cursor.execute(_SQL_OCUPACAO, (fim, inicio, fim, inicio))
        return cursor.fetchall()


def reservas(inicio, fim, usuario_id=None) -> list:
    with obter_cursor(usuario_id=usuario_id) as cursor:
        cursor.execute(_SQL_RESERVAS, (fim, inicio))
        return cursor.fetchall()


def mensagens(inicio, fim, usuario_id=None) -> dict:
    """`{True: da equipe, False: dos hóspedes}`."""
    with obter_cursor(usuario_id=usuario_id) as cursor:
        cursor.execute(_SQL_MENSAGENS, (inicio, fim))
        return {bool(da_equipe): total for da_equipe, total in cursor.fetchall()}
