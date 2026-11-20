"""O livro dos Pix conferidos pela equipe — Etapa 5, Bloco 8.

O Bloco 7 criou a tabela `pagamento` e as declarações do hóspede ("Fiz o
Pix"). Este módulo é a outra metade: a equipe confere o extrato e
registra. **Nenhuma ação move dinheiro.** Cada uma grava um fato que uma
pessoa conferiu, com o autor e a hora — a hora vem do gatilho, não daqui.

## O valor nunca é digitado

Sai do `valor_total` lido **na mesma leitura travada** da reserva, dividido
pela mesma função que o hóspede viu no pedido (`pedido.dividir`), que quem
chama passa como `dividir`. Nenhum formulário leva valor.

## Trava, decide, grava — na mesma transação

Cada ação trava a linha da reserva com `FOR UPDATE`, lê os lançamentos que
já existem, decide, e grava. Dois cliques — ou dois recepcionistas no
mesmo instante — se enfileiram na trava: o segundo encontra a reserva já
mudada e é recusado. A unicidade `(reserva_id, tipo)` da tabela é a
segunda camada, para o caso de a primeira falhar.

Recusa devolve `False`, sem distinguir o motivo: a tela diz que a reserva
não aceita mais a operação e mostra a situação atual.
"""

from app.dados.conexao import obter_conexao, obter_cursor
from app.dados.erros import ErroDeDados, RegistroDuplicado, executar

SINAL = "sinal"
RESTANTE = "restante"
INTEGRAL = "integral"
DEVOLUCAO = "devolucao"

# Pagamento completo: nenhum lançamento de entrada falta.
_QUITAM = (RESTANTE, INTEGRAL)

_SQL_TRAVAR_RESERVA = (
    "SELECT id, situacao, modalidade, valor_total "
    "FROM reserva WHERE id = %s FOR UPDATE"
)

_SQL_TIPOS_DA_RESERVA = "SELECT tipo FROM pagamento WHERE reserva_id = %s"

_SQL_LANCAR = (
    "INSERT INTO pagamento (reserva_id, tipo, valor, registrado_por) "
    "VALUES (%s, %s, %s, %s)"
)

_SQL_CONFIRMAR_RESERVA = (
    "UPDATE reserva SET situacao = 'confirmada' "
    "WHERE id = %s AND situacao = 'pendente' AND modalidade IS NOT NULL"
)

_SQL_CANCELAR_POR_FALTA = (
    "UPDATE reserva SET situacao = 'cancelada', "
    "       motivo_cancelamento = 'falta_de_pagamento', cancelado_em = NOW() "
    "WHERE id = %s AND situacao IN ('pendente', 'confirmada') "
    "  AND modalidade IS NOT NULL"
)

# Os pedidos do site com algo a resolver: vivos, ainda não quitados, e com
# a saída de hoje em diante. A urgência é decidida em Python, com o relógio
# de quem chama, para que a regra seja testável sem banco.
_SQL_PENDENCIAS = (
    "SELECT r.id, r.chale_id, r.hospede_id, r.data_entrada, r.data_saida, "
    "       r.num_pessoas, r.situacao, r.valor_total, r.modalidade, "
    "       r.prazo_pagamento, r.prazo_restante, r.pix_informado_em, "
    "       r.restante_informado_em, r.criado_em, "
    "       c.nome AS chale_nome "
    "FROM reserva r "
    "JOIN chale c ON c.id = r.chale_id "
    "WHERE r.modalidade IS NOT NULL "
    "  AND r.situacao IN ('pendente', 'confirmada') "
    "  AND r.data_saida >= CURDATE() "
    "  AND NOT EXISTS ("
    "      SELECT 1 FROM pagamento p "
    "      WHERE p.reserva_id = r.id AND p.tipo IN ('restante', 'integral')"
    "  ) "
    "ORDER BY r.data_entrada, r.id"
)

_SQL_LANCAMENTOS_POR_RESERVAS_DO_HOSPEDE = (
    "SELECT p.reserva_id, p.tipo, p.valor "
    "FROM pagamento p JOIN reserva r ON r.id = p.reserva_id "
    "WHERE r.hospede_id = %s"
)

_SQL_LANCAMENTOS_DA_RESERVA = (
    "SELECT tipo, valor FROM pagamento WHERE reserva_id = %s"
)

# --- Bloco 9: as devoluções ------------------------------------------------

# Canceladas pelo hóspede, com entrada conferida e sem devolução lançada. O
# direito à devolução (7 dias antes da entrada) é decidido em Python, pela
# mesma função do regulamento que o hóspede leu.
_SQL_DEVOLUCOES_EM_ABERTO = (
    "SELECT r.id, r.hospede_id, r.data_entrada, r.data_saida, "
    "       r.num_pessoas, r.valor_total, r.modalidade, r.cancelado_em, "
    "       c.nome AS chale_nome, "
    "       (SELECT SUM(p.valor) FROM pagamento p "
    "        WHERE p.reserva_id = r.id AND p.valor > 0) AS pago "
    "FROM reserva r "
    "JOIN chale c ON c.id = r.chale_id "
    "WHERE r.situacao = 'cancelada' "
    "  AND r.motivo_cancelamento = 'pedido_do_hospede' "
    "  AND EXISTS (SELECT 1 FROM pagamento p "
    "              WHERE p.reserva_id = r.id AND p.valor > 0) "
    "  AND NOT EXISTS (SELECT 1 FROM pagamento p "
    "                  WHERE p.reserva_id = r.id AND p.tipo = 'devolucao') "
    "ORDER BY r.cancelado_em, r.id"
)

_SQL_TRAVAR_CANCELADA = (
    "SELECT situacao, motivo_cancelamento, data_entrada, cancelado_em "
    "FROM reserva WHERE id = %s FOR UPDATE"
)

_SQL_SOMA_DAS_ENTRADAS = (
    "SELECT COALESCE(SUM(valor), 0), "
    "       SUM(tipo = 'devolucao') "
    "FROM pagamento WHERE reserva_id = %s"
)

# --- 8c: o controle do administrador ---------------------------------------

_SQL_ANOS = (
    "SELECT DISTINCT YEAR(registrado_em) FROM pagamento ORDER BY 1 DESC"
)

_SQL_TOTAIS_DO_ANO = (
    "SELECT MONTH(registrado_em) AS mes, SUM(valor) AS total, "
    "       COUNT(*) AS lancamentos "
    "FROM pagamento "
    "WHERE registrado_em >= %s AND registrado_em < %s "
    "GROUP BY MONTH(registrado_em)"
)

_SQL_LANCAMENTOS_DO_MES = (
    "SELECT p.id, p.reserva_id, p.tipo, p.valor, p.registrado_em, "
    "       r.hospede_id, c.nome AS chale_nome, "
    "       u.nome AS conferido_por "
    "FROM pagamento p "
    "JOIN reserva r ON r.id = p.reserva_id "
    "JOIN chale c ON c.id = r.chale_id "
    "JOIN usuario u ON u.id = p.registrado_por "
    "WHERE p.registrado_em >= %s AND p.registrado_em < %s "
    "ORDER BY p.registrado_em, p.id"
)


# ---------------------------------------------------------------------------
# As três ações da equipe
# ---------------------------------------------------------------------------

def _travar(cursor, reserva_id):
    executar(cursor, _SQL_TRAVAR_RESERVA, (reserva_id,))
    linha = cursor.fetchone()
    if linha is None:
        return None, set()
    executar(cursor, _SQL_TIPOS_DA_RESERVA, (reserva_id,))
    tipos = {tipo for (tipo,) in cursor.fetchall()}
    reserva = {
        "id": linha[0],
        "situacao": linha[1],
        "modalidade": linha[2],
        "valor_total": linha[3],
    }
    return reserva, tipos


def confirmar_primeiro(reserva_id, dividir, usuario_id) -> bool:
    """"Pagou 50% Pix" (sinal) ou "Pagou 100% Pix" (integral).

    Confirma a reserva e lança o primeiro Pix, juntos. O tipo do
    lançamento vem da modalidade gravada na reserva, e não de quem chama.
    """
    with obter_conexao(usuario_id=usuario_id) as conexao:
        cursor = conexao.cursor()
        try:
            reserva, tipos = _travar(cursor, reserva_id)
            if reserva is None or tipos:
                return False

            # Situação e modalidade são conferidas UMA vez, no UPDATE. Uma
            # cópia aqui em Python não mudava nada observável — a sabotagem
            # do Bloco 8 mostrou —, e regra repetida é regra que diverge.
            executar(cursor, _SQL_CONFIRMAR_RESERVA, (reserva_id,))
            if cursor.rowcount != 1:
                return False

            tipo = SINAL if reserva["modalidade"] == SINAL else INTEGRAL
            valor, _ = dividir(reserva["valor_total"], reserva["modalidade"])
            executar(cursor, _SQL_LANCAR, (reserva_id, tipo, valor, usuario_id))
            return True
        except RegistroDuplicado:
            # A segunda camada: a unicidade (reserva, tipo). O `with`
            # desfaz a confirmação junto.
            conexao.rollback()
            return False
        finally:
            cursor.close()


def confirmar_restante(reserva_id, dividir, usuario_id) -> bool:
    """"Pagou 100% Pix" numa reserva com sinal: o restante completa o total."""
    with obter_conexao(usuario_id=usuario_id) as conexao:
        cursor = conexao.cursor()
        try:
            reserva, tipos = _travar(cursor, reserva_id)
            # Sem conferir a modalidade: só reserva com sinal tem lançamento
            # `sinal`. `RESTANTE in tipos` e a unicidade (reserva, tipo) são
            # duas camadas de propósito — a segunda está presa por teste em
            # test_livro_de_pagamentos.
            if (
                reserva is None
                or reserva["situacao"] != "confirmada"
                or SINAL not in tipos
                or RESTANTE in tipos
            ):
                return False

            _, valor = dividir(reserva["valor_total"], SINAL)
            executar(
                cursor, _SQL_LANCAR, (reserva_id, RESTANTE, valor, usuario_id)
            )
            return True
        except RegistroDuplicado:
            conexao.rollback()
            return False
        finally:
            cursor.close()


def cancelar_por_falta_de_pagamento(reserva_id, usuario_id) -> bool:
    """O Pix não apareceu no extrato. O chalé volta a ficar livre.

    Recusado para reserva já quitada: "falta de pagamento" de quem pagou
    tudo seria um registro falso.
    """
    with obter_conexao(usuario_id=usuario_id) as conexao:
        cursor = conexao.cursor()
        try:
            reserva, tipos = _travar(cursor, reserva_id)
            if (
                reserva is None
                or reserva["situacao"] not in ("pendente", "confirmada")
                or reserva["modalidade"] is None
                or tipos & set(_QUITAM)
            ):
                return False

            executar(cursor, _SQL_CANCELAR_POR_FALTA, (reserva_id,))
            return cursor.rowcount == 1
        finally:
            cursor.close()


def registrar_devolucao(reserva_id, tem_devolucao, usuario_id) -> bool:
    """"Pix 50% retornado" ou "Pix 100% retornado" (Bloco 9).

    O valor é o que entrou — a soma dos lançamentos, lida com a reserva
    travada — e vai com sinal negativo, no mês em que a equipe devolve.
    `tem_devolucao(entrada, dia_do_cancelamento)` é a regra do
    regulamento, passada por quem chama.
    """
    with obter_conexao(usuario_id=usuario_id) as conexao:
        cursor = conexao.cursor()
        try:
            executar(cursor, _SQL_TRAVAR_CANCELADA, (reserva_id,))
            linha = cursor.fetchone()
            if (
                linha is None
                or linha[0] != "cancelada"
                or linha[1] != "pedido_do_hospede"
                or linha[3] is None
                or not tem_devolucao(linha[2], linha[3].date())
            ):
                return False

            executar(cursor, _SQL_SOMA_DAS_ENTRADAS, (reserva_id,))
            entrou, devolucoes = cursor.fetchone()
            if devolucoes or entrou <= 0:
                return False

            executar(cursor, _SQL_LANCAR,
                     (reserva_id, DEVOLUCAO, -entrou, usuario_id))
            return True
        except RegistroDuplicado:
            conexao.rollback()
            return False
        finally:
            cursor.close()


# ---------------------------------------------------------------------------
# Leitura
# ---------------------------------------------------------------------------

def devolucoes_em_aberto(usuario_id=None) -> list:
    with obter_cursor(usuario_id=usuario_id, dicionario=True) as cursor:
        cursor.execute(_SQL_DEVOLUCOES_EM_ABERTO)
        return cursor.fetchall()


def pendencias(usuario_id=None) -> list:
    """Os pedidos do site com algo a resolver, para os cards da equipe."""
    with obter_cursor(usuario_id=usuario_id, dicionario=True) as cursor:
        cursor.execute(_SQL_PENDENCIAS)
        return cursor.fetchall()


def lancamentos_por_reserva_do_hospede(hospede_id, usuario_id=None) -> dict:
    """`{reserva_id: {tipo: valor}}` das reservas de um hóspede.

    O filtro de dono está no SQL, como em toda leitura do perfil hóspede.
    """
    resultado = {}
    with obter_cursor(usuario_id=usuario_id) as cursor:
        cursor.execute(_SQL_LANCAMENTOS_POR_RESERVAS_DO_HOSPEDE, (hospede_id,))
        for reserva_id, tipo, valor in cursor.fetchall():
            resultado.setdefault(reserva_id, {})[tipo] = valor
    return resultado


def lancamentos_da_reserva(reserva_id, usuario_id=None) -> dict:
    with obter_cursor(usuario_id=usuario_id) as cursor:
        cursor.execute(_SQL_LANCAMENTOS_DA_RESERVA, (reserva_id,))
        return {tipo: valor for tipo, valor in cursor.fetchall()}


def anos(usuario_id=None) -> list:
    with obter_cursor(usuario_id=usuario_id) as cursor:
        cursor.execute(_SQL_ANOS)
        return [int(ano) for (ano,) in cursor.fetchall()]


def totais_do_ano(inicio, fim, usuario_id=None) -> dict:
    """`{mes: (total, quantidade)}` entre duas datas, fim exclusivo."""
    with obter_cursor(usuario_id=usuario_id) as cursor:
        cursor.execute(_SQL_TOTAIS_DO_ANO, (inicio, fim))
        return {
            int(mes): (total, int(quantidade))
            for mes, total, quantidade in cursor.fetchall()
        }


def lancamentos_do_periodo(inicio, fim, usuario_id=None) -> list:
    with obter_cursor(usuario_id=usuario_id, dicionario=True) as cursor:
        cursor.execute(_SQL_LANCAMENTOS_DO_MES, (inicio, fim))
        return cursor.fetchall()


__all__ = [
    "DEVOLUCAO",
    "ErroDeDados",
    "INTEGRAL",
    "RESTANTE",
    "SINAL",
    "anos",
    "cancelar_por_falta_de_pagamento",
    "confirmar_primeiro",
    "confirmar_restante",
    "devolucoes_em_aberto",
    "registrar_devolucao",
    "lancamentos_da_reserva",
    "lancamentos_do_periodo",
    "lancamentos_por_reserva_do_hospede",
    "pendencias",
    "totais_do_ano",
]
