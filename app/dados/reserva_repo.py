"""Acesso à tabela de reservas e à consulta de disponibilidade.

A regra de não-sobreposição mora no banco, em trigger — é o argumento
central do projeto. O que existe aqui é outra coisa: a MESMA comparação,
repetida na aplicação, para dois fins diferentes.

**Consultar disponibilidade.** Listar quais chalés estão livres num
período é uma pergunta de leitura, que a trigger não responde: ela só
reage a uma gravação.

**Antecipar a recusa.** Verificar antes de tentar gravar permite dizer
"este chalé já está reservado de 10 a 15/12" em vez de deixar a trigger
interromper e traduzir o erro depois.

A duplicação é deliberada e o plano a prevê. A garantia continua sendo da
trigger: se esta validação tiver defeito, a reserva conflitante ainda
assim não entra. O contrário não vale — sem a trigger, um defeito aqui
gravaria dado inconsistente.

Por isso a comparação precisa ser a mesma, letra por letra:

    NEW.data_entrada < data_saida  AND  NEW.data_saida > data_entrada

Intervalo semiaberto: a reserva ocupa de `data_entrada` inclusive até
`data_saida` exclusive. A saída de um hóspede libera o chalé para a
entrada de outro no mesmo dia.
"""

import datetime as dt

from app.dados.conexao import obter_cursor
from app.dados.erros import ErroDeDados

SITUACOES = ("pendente", "confirmada", "cancelada", "concluida")

# Somente estas duas ocupam o chalé. Uma reserva cancelada ou concluída
# não impede nova reserva no mesmo período.
SITUACOES_ATIVAS = ("pendente", "confirmada")

# Teto de dias de uma estadia. Não vem do banco: vem de evitar que um erro
# de digitação na data crie uma reserva de dez anos que bloqueie o chalé.
MAXIMO_DE_DIARIAS = 90


class ErroDeReserva(ErroDeDados):
    """Dado de reserva inválido, recusado antes de chegar ao banco."""


# A consulta usa NOT EXISTS em vez de LEFT JOIN com filtro: o otimizador
# interrompe a varredura na primeira reserva conflitante encontrada, em
# vez de reunir todas para descartá-las depois. O índice
# idx_reserva_disponibilidade (chale_id, data_entrada, data_saida,
# situacao) sustenta exatamente esse acesso.
_SQL_DISPONIVEIS = (
    "SELECT c.id, c.nome, c.capacidade, c.configuracao_camas, "
    "       c.comodidades, c.descricao "
    "FROM chale c "
    "WHERE c.ativo = TRUE "
    "  AND NOT EXISTS ( "
    "        SELECT 1 FROM reserva r "
    "        WHERE r.chale_id = c.id "
    "          AND r.situacao IN ('pendente', 'confirmada') "
    "          AND %s < r.data_saida "
    "          AND %s > r.data_entrada "
    "      ) "
    "ORDER BY c.nome"
)

_SQL_OCUPADOS = (
    "SELECT c.id, c.nome, c.capacidade, r.id AS reserva_id, "
    "       r.data_entrada, r.data_saida, r.situacao "
    "FROM chale c "
    "JOIN reserva r ON r.chale_id = c.id "
    "WHERE c.ativo = TRUE "
    "  AND r.situacao IN ('pendente', 'confirmada') "
    "  AND %s < r.data_saida "
    "  AND %s > r.data_entrada "
    "ORDER BY c.nome, r.data_entrada"
)

# A mesma comparação da trigger, para um chalé específico. O parâmetro de
# exclusão permite reusá-la na alteração, ignorando a própria reserva.
_SQL_CONFLITOS = (
    "SELECT id, data_entrada, data_saida, situacao FROM reserva "
    "WHERE chale_id = %s "
    "  AND situacao IN ('pendente', 'confirmada') "
    "  AND id <> %s "
    "  AND %s < data_saida "
    "  AND %s > data_entrada "
    "ORDER BY data_entrada"
)


def converter_data(valor, rotulo: str) -> dt.date:
    if isinstance(valor, dt.datetime):
        return valor.date()
    if isinstance(valor, dt.date):
        return valor
    if not valor:
        raise ErroDeReserva(f"A {rotulo} é obrigatória.")
    try:
        return dt.date.fromisoformat(str(valor).strip())
    except ValueError:
        raise ErroDeReserva(
            f"A {rotulo} precisa estar no formato AAAA-MM-DD."
        ) from None


def validar_periodo(entrada, saida) -> tuple:
    """Confere o período e devolve as duas datas convertidas.

    Espelha a primeira regra da trigger: a saída precisa ser estritamente
    posterior à entrada. Uma reserva de zero noites não existe.
    """
    entrada = converter_data(entrada, "data de entrada")
    saida = converter_data(saida, "data de saída")

    if saida <= entrada:
        raise ErroDeReserva(
            "A data de saída precisa ser posterior à data de entrada."
        )

    diarias = (saida - entrada).days
    if diarias > MAXIMO_DE_DIARIAS:
        raise ErroDeReserva(
            f"A estadia tem {diarias} diárias; o máximo é "
            f"{MAXIMO_DE_DIARIAS}. Verifique as datas."
        )

    return entrada, saida


def chales_disponiveis(entrada, saida, usuario_id=None) -> list:
    """Chalés ativos sem reserva ativa que se sobreponha ao período.

    Um chalé desativado não aparece, mesmo estando livre: ele saiu de
    operação, e oferecê-lo produziria uma reserva que a recepção teria de
    desfazer.
    """
    entrada, saida = validar_periodo(entrada, saida)

    with obter_cursor(usuario_id=usuario_id, dicionario=True) as cursor:
        cursor.execute(_SQL_DISPONIVEIS, (entrada, saida))
        return cursor.fetchall()


def chales_ocupados(entrada, saida, usuario_id=None) -> list:
    """O complemento: quais chalés estão tomados, e por qual reserva.

    Serve à tela, que mostra os dois lados — saber que um chalé está
    ocupado até dia 15 é tão útil quanto saber que outro está livre.
    """
    entrada, saida = validar_periodo(entrada, saida)

    with obter_cursor(usuario_id=usuario_id, dicionario=True) as cursor:
        cursor.execute(_SQL_OCUPADOS, (entrada, saida))
        return cursor.fetchall()


def conflitos(chale_id, entrada, saida, ignorar_id=0, usuario_id=None) -> list:
    """Reservas ativas que se sobrepõem ao período, no mesmo chalé.

    `ignorar_id` exclui a própria reserva na alteração. Sem isso, editar
    apenas a observação de uma reserva a faria conflitar consigo mesma —
    o risco que o plano registra para a trigger de UPDATE, e que vale
    igualmente para esta verificação.
    """
    entrada, saida = validar_periodo(entrada, saida)

    with obter_cursor(usuario_id=usuario_id, dicionario=True) as cursor:
        cursor.execute(
            _SQL_CONFLITOS, (chale_id, ignorar_id or 0, entrada, saida)
        )
        return cursor.fetchall()


def chale_disponivel(chale_id, entrada, saida, ignorar_id=0, usuario_id=None) -> bool:
    return not conflitos(
        chale_id, entrada, saida, ignorar_id=ignorar_id, usuario_id=usuario_id
    )
