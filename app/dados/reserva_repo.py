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

from app.dados import acompanhante_repo, periodo
from app.dados.conexao import obter_conexao, obter_cursor
from app.dados.erros import ErroDeDados, executar

SITUACOES = ("pendente", "confirmada", "cancelada", "concluida")

# Somente estas duas ocupam o chalé. Uma reserva cancelada ou concluída
# não impede nova reserva no mesmo período.
SITUACOES_ATIVAS = ("pendente", "confirmada")

# Teto de dias de uma estadia. Não vem do banco: vem de evitar que um erro
# de digitação na data crie uma reserva de dez anos que bloqueie o chalé.
#
# Reexportado de `periodo` desde o Bloco 5 da Etapa 5, para que o valor
# exista num lugar só — a face pública aplica o mesmo teto.
MAXIMO_DE_DIARIAS = periodo.MAXIMO_DE_DIARIAS


class ErroDeReserva(ErroDeDados):
    """Dado de reserva inválido, recusado antes de chegar ao banco."""


class ConflitoDePeriodo(ErroDeReserva):
    """O chalé já está reservado no período pedido.

    Separada das demais porque é a única recusa que não vem de dado
    errado: a reserva está perfeitamente bem formada, e o que falta é
    disponibilidade. A tela devolve 409 para esta e 400 para as outras —
    a mesma distinção que o HTTP faz entre "você escreveu errado" e "não
    dá, do jeito que as coisas estão agora".
    """


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


# ---------------------------------------------------------------------------
# Período
#
# A regra mudou de casa no Bloco 5 da Etapa 5, e estas duas funções
# passaram a repassar a chamada para `app/dados/periodo.py`.
#
# **Por que a mudança.** A consulta pública de disponibilidade precisa da
# mesma validação: mesmo formato de data, mesma exigência de saída
# posterior à entrada, mesmo teto de diárias. Mas a face pública não pode
# importar este módulo — importá-lo lhe daria também `listar`,
# `buscar_por_id`, `listar_por_hospede` e `chales_ocupados`, que devolve
# identificador de reserva, datas e situação.
#
# Duplicar a validação em `vitrine.py` seria a alternativa fácil e a
# errada: duas cópias da mesma regra, livres para divergir. Já aconteceu
# neste projeto, e a lição foi que uma regra em dois lugares é uma regra
# que vai discordar de si mesma.
#
# **Nada mudou para quem chama.** A exceção continua sendo
# `ErroDeReserva`, com a mesma mensagem: `periodo` levanta a sua própria
# e estas funções a traduzem. Quem já escrevia `except ErroDeReserva`
# continua certo.
# ---------------------------------------------------------------------------

def converter_data(valor, rotulo: str) -> dt.date:
    try:
        return periodo.converter_data(valor, rotulo)
    except periodo.ErroDePeriodo as erro:
        raise ErroDeReserva(str(erro)) from erro


def validar_periodo(entrada, saida) -> tuple:
    """Confere o período e devolve as duas datas convertidas.

    Espelha a primeira regra da trigger: a saída precisa ser estritamente
    posterior à entrada. Uma reserva de zero noites não existe.
    """
    try:
        return periodo.validar_periodo(entrada, saida)
    except periodo.ErroDePeriodo as erro:
        raise ErroDeReserva(str(erro)) from erro


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


# ---------------------------------------------------------------------------
# Transições de situação
# ---------------------------------------------------------------------------
# O ciclo de vida de uma reserva não é livre: de `cancelada` não se volta,
# e `concluida` é terminal. Sem este mapa, a interface permitiria
# reabilitar uma reserva cancelada só trocando o valor num campo oculto —
# e a trigger não impediria, porque ela valida sobreposição, não história.
TRANSICOES = {
    "pendente": ("confirmada", "cancelada"),
    "confirmada": ("cancelada", "concluida"),
    "cancelada": (),
    "concluida": (),
}

LIMITE_DE_OBSERVACOES = 2000

# A lista de colunas aparece por extenso em cada consulta, sem f-string.
# A regra vale para todo o projeto: nenhuma f-string chega a um `execute`,
# de modo que qualquer uma que apareça seja anomalia visível.

_SQL_CRIAR = (
    "INSERT INTO reserva "
    "(chale_id, hospede_id, data_entrada, data_saida, num_pessoas, "
    " situacao, valor_total, observacoes, criado_por) "
    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)"
)

_SQL_ATUALIZAR = (
    "UPDATE reserva SET chale_id = %s, hospede_id = %s, data_entrada = %s, "
    "data_saida = %s, num_pessoas = %s, valor_total = %s, observacoes = %s "
    "WHERE id = %s"
)

_SQL_MUDAR_SITUACAO = "UPDATE reserva SET situacao = %s WHERE id = %s"

_SQL_POR_ID = (
    "SELECT r.id, r.chale_id, r.hospede_id, r.data_entrada, r.data_saida, "
    "       r.num_pessoas, r.situacao, r.valor_total, r.observacoes, "
    "       r.criado_por, r.criado_em, r.alterado_em, "
    "       r.modalidade, r.prazo_pagamento, r.prazo_restante, "
    "       r.pix_informado_em, r.restante_informado_em, "
    "       r.regulamento_aceito_em, r.cancelado_em, "
    "       r.motivo_cancelamento, "
    "       c.nome AS chale_nome, c.capacidade AS chale_capacidade, "
    "       u.login AS criado_por_login "
    "FROM reserva r "
    "JOIN chale c ON c.id = r.chale_id "
    "LEFT JOIN usuario u ON u.id = r.criado_por "
    "WHERE r.id = %s LIMIT 1"
)

_SQL_LISTAR = (
    "SELECT r.id, r.chale_id, r.hospede_id, r.data_entrada, r.data_saida, "
    "       r.num_pessoas, r.situacao, r.valor_total, r.observacoes, "
    "       r.criado_por, r.criado_em, r.alterado_em, "
    "       r.modalidade, r.prazo_pagamento, r.prazo_restante, "
    "       r.pix_informado_em, r.restante_informado_em, "
    "       r.regulamento_aceito_em, r.cancelado_em, "
    "       r.motivo_cancelamento, "
    "       c.nome AS chale_nome, c.capacidade AS chale_capacidade, "
    "       u.login AS criado_por_login "
    "FROM reserva r "
    "JOIN chale c ON c.id = r.chale_id "
    "LEFT JOIN usuario u ON u.id = r.criado_por "
    "ORDER BY r.data_entrada DESC, r.id DESC LIMIT %s"
)

_SQL_LISTAR_POR_SITUACAO = (
    "SELECT r.id, r.chale_id, r.hospede_id, r.data_entrada, r.data_saida, "
    "       r.num_pessoas, r.situacao, r.valor_total, r.observacoes, "
    "       r.criado_por, r.criado_em, r.alterado_em, "
    "       r.modalidade, r.prazo_pagamento, r.prazo_restante, "
    "       r.pix_informado_em, r.restante_informado_em, "
    "       r.regulamento_aceito_em, r.cancelado_em, "
    "       r.motivo_cancelamento, "
    "       c.nome AS chale_nome, c.capacidade AS chale_capacidade, "
    "       u.login AS criado_por_login "
    "FROM reserva r "
    "JOIN chale c ON c.id = r.chale_id "
    "LEFT JOIN usuario u ON u.id = r.criado_por "
    "WHERE r.situacao = %s "
    "ORDER BY r.data_entrada DESC, r.id DESC LIMIT %s"
)

_SQL_POR_HOSPEDE = (
    "SELECT r.id, r.chale_id, r.hospede_id, r.data_entrada, r.data_saida, "
    "       r.num_pessoas, r.situacao, r.valor_total, r.observacoes, "
    "       r.criado_por, r.criado_em, r.alterado_em, "
    "       r.modalidade, r.prazo_pagamento, r.prazo_restante, "
    "       r.pix_informado_em, r.restante_informado_em, "
    "       r.regulamento_aceito_em, r.cancelado_em, "
    "       r.motivo_cancelamento, "
    "       c.nome AS chale_nome, c.capacidade AS chale_capacidade, "
    "       u.login AS criado_por_login "
    "FROM reserva r "
    "JOIN chale c ON c.id = r.chale_id "
    "LEFT JOIN usuario u ON u.id = r.criado_por "
    "WHERE r.hospede_id = %s "
    "ORDER BY r.data_entrada DESC, r.id DESC"
)

_SQL_CHALE = "SELECT capacidade, ativo, nome FROM chale WHERE id = %s"
_SQL_HOSPEDE_EXISTE = "SELECT 1 FROM hospede WHERE id = %s"
_SQL_CONTAR_ATIVAS = (
    "SELECT COUNT(*) FROM reserva WHERE situacao IN ('pendente', 'confirmada')"
)


def _validar_regras(cursor, dados: dict, ignorar_id=0) -> None:
    """Espelha na aplicação as três regras da trigger.

    Não substitui a trigger: antecipa. Se esta função tiver defeito, a
    reserva conflitante continua não entrando — o banco a recusa. O que se
    ganha aqui é a mensagem: "o Embaré já está reservado de 15 a 20/12"
    diz mais do que "erro 1644".

    A ordem das verificações segue a da trigger, de propósito: quando as
    duas recusarem a mesma operação, recusarão pelo mesmo motivo.
    """
    # Regra 1 — coerência de datas: já aplicada por `validar_periodo`.

    # Regra 2 — capacidade do chalé.
    cursor.execute(_SQL_CHALE, (dados["chale_id"],))
    chale = cursor.fetchone()

    if chale is None:
        raise ErroDeReserva("O chalé informado não existe.")

    capacidade, ativo, nome = chale[0], chale[1], chale[2]

    if not ativo:
        raise ErroDeReserva(
            f"O chalé {nome} está desativado e não aceita novas reservas."
        )

    if dados["num_pessoas"] > capacidade:
        plural = "s" if capacidade != 1 else ""
        raise ErroDeReserva(
            f"O chalé {nome} comporta {capacidade} pessoa{plural}; "
            f"foram informadas {dados['num_pessoas']}."
        )

    # O hóspede precisa existir. A chave estrangeira garantiria, mas o
    # erro dela não diz qual campo está errado.
    cursor.execute(_SQL_HOSPEDE_EXISTE, (dados["hospede_id"],))
    if cursor.fetchone() is None:
        raise ErroDeReserva("O hóspede informado não existe.")

    # Regra 3 — não-sobreposição. Só vale quando a reserva ocupa o chalé.
    if dados["situacao"] not in SITUACOES_ATIVAS:
        return

    cursor.execute(
        _SQL_CONFLITOS,
        (
            dados["chale_id"],
            ignorar_id or 0,
            dados["data_entrada"],
            dados["data_saida"],
        ),
    )

    # fetchall, não fetchone: a consulta pode devolver mais de uma reserva
    # em conflito, e deixar linhas pendentes faz o próximo `execute` na
    # mesma conexão falhar com "Unread result found".
    achados = cursor.fetchall()
    conflito = achados[0] if achados else None

    if conflito:
        raise ConflitoDePeriodo(
            f"O chalé {nome} já está reservado de "
            f"{conflito[1]:%d/%m/%Y} a {conflito[2]:%d/%m/%Y}. "
            f"Escolha outro período ou outro chalé."
        )


def _limpar(dados: dict) -> dict:
    entrada, saida = validar_periodo(
        dados.get("data_entrada"), dados.get("data_saida")
    )

    try:
        chale_id = int(dados.get("chale_id"))
        hospede_id = int(dados.get("hospede_id"))
    except (TypeError, ValueError):
        raise ErroDeReserva("Selecione o chalé e o hóspede.") from None

    try:
        pessoas = int(dados.get("num_pessoas"))
    except (TypeError, ValueError):
        raise ErroDeReserva(
            "O número de pessoas precisa ser um número inteiro."
        ) from None

    if pessoas < 1:
        raise ErroDeReserva("A reserva precisa ter ao menos uma pessoa.")

    situacao = (dados.get("situacao") or "pendente").strip()
    if situacao not in SITUACOES:
        raise ErroDeReserva("Situação de reserva inválida.")

    observacoes = dados.get("observacoes")
    observacoes = (
        observacoes.strip() if isinstance(observacoes, str) else observacoes
    )
    if observacoes and len(observacoes) > LIMITE_DE_OBSERVACOES:
        raise ErroDeReserva(
            f"As observações têm {len(observacoes)} caracteres; "
            f"o limite é {LIMITE_DE_OBSERVACOES}."
        )

    return {
        "chale_id": chale_id,
        "hospede_id": hospede_id,
        "data_entrada": entrada,
        "data_saida": saida,
        "num_pessoas": pessoas,
        "situacao": situacao,
        "valor_total": dados.get("valor_total"),
        "observacoes": observacoes or None,
    }


def _calcular_valor(limpo: dict):
    """Valor total da estadia, somando noite a noite.

    Uma lacuna no cadastro de tarifas NÃO impede a reserva: ela grava com
    valor nulo e a tela mostra um traço. A recepção precisa poder reservar
    mesmo quando o administrador ainda não cadastrou a tarifa do período —
    bloquear a operação do negócio por falta de preço seria pior que
    registrar a estadia e acertar a conta depois.

    Qualquer outra falha de cálculo sobe, porque aí é defeito.
    """
    from app.dados.calculo import TarifaAusente, valor_total

    try:
        return valor_total(
            limpo["chale_id"], limpo["data_entrada"], limpo["data_saida"]
        )
    except TarifaAusente:
        return None


# ---------------------------------------------------------------------------
# Escrita
# ---------------------------------------------------------------------------

def criar(dados: dict, usuario_id=None) -> int:
    """Grava uma reserva nova, validando antes de tentar.

    A validação e a gravação acontecem na mesma transação e na mesma
    conexão. Isso importa: verificar o conflito numa conexão e gravar em
    outra abriria uma janela entre as duas, e duas requisições simultâneas
    poderiam passar as duas pela verificação. A trigger fecharia essa
    janela de qualquer forma — é exatamente para isso que ela existe —
    mas a mensagem ao usuário seria pior.
    """
    limpo = _limpar(dados)

    with obter_conexao(usuario_id=usuario_id) as conexao:
        cursor = conexao.cursor()
        try:
            _cancelar_vencidos(cursor)
            _validar_regras(cursor, limpo)
            limpo["valor_total"] = _calcular_valor(limpo)
            executar(
                cursor,
                _SQL_CRIAR,
                (
                    limpo["chale_id"],
                    limpo["hospede_id"],
                    limpo["data_entrada"],
                    limpo["data_saida"],
                    limpo["num_pessoas"],
                    limpo["situacao"],
                    limpo["valor_total"],
                    limpo["observacoes"],
                    usuario_id,
                ),
            )
            return cursor.lastrowid
        finally:
            cursor.close()


def atualizar(identificador, dados: dict, usuario_id=None) -> None:
    """Altera uma reserva existente.

    A situação NÃO é alterada aqui: ela tem regra própria de transição e
    rota própria. Misturar as duas coisas permitiria passar de `cancelada`
    para `confirmada` por um campo de formulário.
    """
    limpo = _limpar(dados)

    with obter_conexao(usuario_id=usuario_id) as conexao:
        cursor = conexao.cursor()
        try:
            _cancelar_vencidos(cursor)
            cursor.execute(
                "SELECT situacao FROM reserva WHERE id = %s", (identificador,)
            )
            atual = cursor.fetchone()
            if atual is None:
                raise ErroDeReserva("A reserva informada não existe.")

            if atual[0] in ("cancelada", "concluida"):
                raise ErroDeReserva(
                    f"Uma reserva {atual[0]} não pode ser alterada."
                )

            # A situação atual entra na validação para que a verificação
            # de conflito considere se esta reserva ocupa o chalé.
            limpo["situacao"] = atual[0]
            _validar_regras(cursor, limpo, ignorar_id=identificador)
            limpo["valor_total"] = _calcular_valor(limpo)

            executar(
                cursor,
                _SQL_ATUALIZAR,
                (
                    limpo["chale_id"],
                    limpo["hospede_id"],
                    limpo["data_entrada"],
                    limpo["data_saida"],
                    limpo["num_pessoas"],
                    limpo["valor_total"],
                    limpo["observacoes"],
                    identificador,
                ),
            )
        finally:
            cursor.close()


def mudar_situacao(identificador, nova: str, usuario_id=None) -> None:
    """Move a reserva no ciclo de vida, respeitando as transições.

    Confirmar uma reserva pendente exige reverificar o conflito: entre a
    criação e a confirmação, outra reserva pode ter ocupado o período. A
    trigger reverifica de qualquer forma; aqui se antecipa a mensagem.
    """
    nova = (nova or "").strip()
    if nova not in SITUACOES:
        raise ErroDeReserva("Situação de reserva inválida.")

    with obter_conexao(usuario_id=usuario_id) as conexao:
        cursor = conexao.cursor()
        try:
            _cancelar_vencidos(cursor)
            cursor.execute(
                "SELECT situacao, chale_id, data_entrada, data_saida, "
                "       num_pessoas, hospede_id "
                "FROM reserva WHERE id = %s",
                (identificador,),
            )
            linha = cursor.fetchone()
            if linha is None:
                raise ErroDeReserva("A reserva informada não existe.")

            atual = linha[0]

            if nova == atual:
                return

            if nova not in TRANSICOES[atual]:
                permitidas = TRANSICOES[atual]
                destino = (
                    " ou ".join(permitidas)
                    if permitidas
                    else "nenhuma outra situação"
                )
                raise ErroDeReserva(
                    f"Uma reserva {atual} só pode passar para {destino}."
                )

            if nova in SITUACOES_ATIVAS and atual not in SITUACOES_ATIVAS:
                _validar_regras(
                    cursor,
                    {
                        "chale_id": linha[1],
                        "hospede_id": linha[5],
                        "data_entrada": linha[2],
                        "data_saida": linha[3],
                        "num_pessoas": linha[4],
                        "situacao": nova,
                    },
                    ignorar_id=identificador,
                )

            executar(cursor, _SQL_MUDAR_SITUACAO, (nova, identificador))
        finally:
            cursor.close()


# ---------------------------------------------------------------------------
# O pedido do hóspede e o prazo do Pix — Etapa 5, Bloco 7
# ---------------------------------------------------------------------------
#
# **A varredura roda ANTES de toda escrita, na mesma transação.** O gatilho
# de não-sobreposição trata qualquer `pendente` como ocupação — inclusive
# um pedido cujo prazo venceu e que ninguém cancelou ainda. Se a varredura
# rodasse depois, a reserva nova seria recusada por causa de um pedido que
# já devia estar cancelado. `test_o_gatilho_trata_pedido_vencido_como_ocupacao`
# prende esse fato do banco.
#
# **O dono vai no WHERE.** Toda operação do hóspede sobre uma reserva leva
# `hospede_id` na própria condição da atualização. Uma reserva de outra
# pessoa não é encontrada, e a resposta é a mesma de uma reserva que não
# existe: quem troca o número no endereço não aprende nada.
#
# **Nenhuma consulta compara prazo com relógio da aplicação.** O prazo é
# gravado pelo banco (`NOW() + 90 minutos`) e conferido pelo banco
# (`prazo_pagamento > NOW()`). Um relógio só para cada regra.

_SQL_CANCELAR_VENCIDOS = (
    "UPDATE reserva "
    "SET situacao = 'cancelada', cancelado_em = NOW(), "
    "    motivo_cancelamento = 'prazo_do_pix' "
    "WHERE situacao = 'pendente' "
    "  AND prazo_pagamento IS NOT NULL "
    "  AND prazo_pagamento <= NOW() "
    "  AND pix_informado_em IS NULL"
)

# Serializa os pedidos de um mesmo hóspede. Sem a trava, duas requisições
# simultâneas contariam zero pedidos em aberto ao mesmo tempo, e as duas
# passariam pelo limite.
_SQL_TRAVAR_HOSPEDE = "SELECT id FROM hospede WHERE id = %s FOR UPDATE"

_SQL_CONTAR_EM_ABERTO = (
    "SELECT COUNT(*) FROM reserva "
    "WHERE hospede_id = %s AND situacao = 'pendente'"
)

_SQL_CRIAR_PEDIDO = (
    "INSERT INTO reserva "
    "(chale_id, hospede_id, data_entrada, data_saida, num_pessoas, "
    " situacao, valor_total, criado_por, modalidade, prazo_pagamento, "
    " prazo_restante, regulamento_aceito_em) "
    "VALUES (%s, %s, %s, %s, %s, 'pendente', %s, %s, %s, "
    "        DATE_ADD(NOW(), INTERVAL %s MINUTE), %s, NOW())"
)

_SQL_INFORMAR_PRIMEIRO_PIX = (
    "UPDATE reserva SET pix_informado_em = NOW() "
    "WHERE id = %s AND hospede_id = %s "
    "  AND situacao = 'pendente' "
    "  AND modalidade IS NOT NULL "
    "  AND pix_informado_em IS NULL "
    "  AND prazo_pagamento > NOW()"
)

_SQL_INFORMAR_RESTANTE = (
    "UPDATE reserva SET restante_informado_em = NOW() "
    "WHERE id = %s AND hospede_id = %s "
    "  AND situacao = 'confirmada' "
    "  AND modalidade = 'sinal' "
    "  AND restante_informado_em IS NULL "
    "  AND prazo_restante > NOW()"
)

# Desistir só vale para o pedido feito pelo site que ainda não teve Pix
# informado: é o único caso sem dinheiro envolvido. O cancelamento com
# devolução é o Bloco 9.
_SQL_DESISTIR = (
    "UPDATE reserva "
    "SET situacao = 'cancelada', cancelado_em = NOW(), "
    "    motivo_cancelamento = 'pedido_do_hospede' "
    "WHERE id = %s AND hospede_id = %s "
    "  AND situacao = 'pendente' "
    "  AND modalidade IS NOT NULL "
    "  AND pix_informado_em IS NULL"
)

PRIMEIRO = "primeiro"
RESTANTE = "restante"


class PedidoEmAberto(ErroDeReserva):
    """A conta já tem um pedido aguardando pagamento ou conferência."""


def _cancelar_vencidos(cursor) -> int:
    """Cancela os pedidos cujo prazo do Pix venceu sem declaração.

    Recebe o cursor de quem chama para rodar **na mesma transação** da
    escrita que vem depois. Ver a nota no topo desta seção.
    """
    executar(cursor, _SQL_CANCELAR_VENCIDOS)
    return cursor.rowcount


def cancelar_pedidos_vencidos(usuario_id=None) -> int:
    """A mesma varredura, por conta própria. Devolve quantos cancelou.

    Usada pelo gancho de requisição da aplicação, para que o chalé
    liberado apareça livre na consulta de disponibilidade sem esperar a
    próxima reserva.
    """
    with obter_conexao(usuario_id=usuario_id) as conexao:
        cursor = conexao.cursor()
        try:
            return _cancelar_vencidos(cursor)
        finally:
            cursor.close()


def criar_pedido(dados: dict, modalidade: str, minutos_para_o_pix: int,
                 prazo_restante, limite_em_aberto: int, usuario_id,
                 acompanhantes=()) -> int:
    """Grava o pedido do hóspede. Devolve o identificador da reserva.

    `dados` traz chalé, hóspede, datas, pessoas e o valor total já
    calculado. A modalidade e os prazos vêm do regulamento, decididos por
    quem chama; este módulo grava, e o banco calcula o prazo do Pix.

    Tudo numa transação, nesta ordem — e a ordem é a defesa:

    1. cancela os pedidos vencidos;
    2. trava a linha do hóspede;
    3. confere o limite de pedidos em aberto;
    4. confere as regras da reserva (chalé, capacidade, sobreposição);
    5. grava a reserva;
    6. grava os acompanhantes (Bloco 15), no mesmo cursor.

    O passo 6 na mesma transação é o que garante "tudo ou nada": se um
    acompanhante for recusado pelo banco, a reserva também não fica.
    """
    limpo = _limpar(dados)
    if limpo["valor_total"] is None:
        raise ErroDeReserva("O pedido precisa do valor calculado.")

    with obter_conexao(usuario_id=usuario_id) as conexao:
        cursor = conexao.cursor()
        try:
            _cancelar_vencidos(cursor)

            executar(cursor, _SQL_TRAVAR_HOSPEDE, (limpo["hospede_id"],))
            if cursor.fetchone() is None:
                raise ErroDeReserva("O cadastro do hóspede não foi encontrado.")

            executar(cursor, _SQL_CONTAR_EM_ABERTO, (limpo["hospede_id"],))
            if cursor.fetchone()[0] >= limite_em_aberto:
                raise PedidoEmAberto(
                    "Você já tem um pedido aguardando pagamento ou a "
                    "conferência da equipe. Conclua ou desista dele antes "
                    "de fazer outro."
                )

            _validar_regras(cursor, limpo)

            executar(
                cursor,
                _SQL_CRIAR_PEDIDO,
                (
                    limpo["chale_id"],
                    limpo["hospede_id"],
                    limpo["data_entrada"],
                    limpo["data_saida"],
                    limpo["num_pessoas"],
                    limpo["valor_total"],
                    usuario_id,
                    modalidade,
                    minutos_para_o_pix,
                    prazo_restante,
                ),
            )
            identificador = cursor.lastrowid
            acompanhante_repo.gravar(cursor, identificador, acompanhantes)
            return identificador
        finally:
            cursor.close()


def informar_pix(identificador, hospede_id, etapa: str, usuario_id=None) -> bool:
    """O hóspede declara que fez o Pix. Devolve se a declaração valeu.

    `False` para todos os casos de recusa, sem distinguir: reserva de
    outra pessoa, inexistente, fora do prazo, já declarada, na situação
    errada. Quem sonda números de reserva não aprende quais existem.

    A condição inteira está no UPDATE, e não numa leitura anterior: entre
    ler e gravar, a varredura poderia cancelar o pedido.
    """
    if etapa == PRIMEIRO:
        comando = _SQL_INFORMAR_PRIMEIRO_PIX
    elif etapa == RESTANTE:
        comando = _SQL_INFORMAR_RESTANTE
    else:
        return False

    with obter_conexao(usuario_id=usuario_id) as conexao:
        cursor = conexao.cursor()
        try:
            executar(cursor, comando, (identificador, hospede_id))
            return cursor.rowcount == 1
        finally:
            cursor.close()


def desistir(identificador, hospede_id, usuario_id=None) -> bool:
    """O hóspede desiste de um pedido ainda sem Pix. Devolve se valeu."""
    with obter_conexao(usuario_id=usuario_id) as conexao:
        cursor = conexao.cursor()
        try:
            executar(cursor, _SQL_DESISTIR, (identificador, hospede_id))
            return cursor.rowcount == 1
        finally:
            cursor.close()


# ---------------------------------------------------------------------------
# Leitura
# ---------------------------------------------------------------------------

def buscar_por_id(identificador, usuario_id=None):
    with obter_cursor(usuario_id=usuario_id, dicionario=True) as cursor:
        cursor.execute(_SQL_POR_ID, (identificador,))
        return cursor.fetchone()


def listar(situacao: str = None, limite: int = 200, usuario_id=None) -> list:
    limite = max(1, min(int(limite), 1000))

    with obter_cursor(usuario_id=usuario_id, dicionario=True) as cursor:
        if situacao:
            if situacao not in SITUACOES:
                raise ErroDeReserva("Situação de reserva inválida.")
            cursor.execute(_SQL_LISTAR_POR_SITUACAO, (situacao, limite))
        else:
            cursor.execute(_SQL_LISTAR, (limite,))
        return cursor.fetchall()


def listar_por_hospede(hospede_id, usuario_id=None) -> list:
    """Reservas de um hóspede. Base da tela do perfil `hospede`.

    A filtragem é feita no SQL, não em Python: trazer todas as reservas e
    descartar as dos outros faria o banco entregar dado que o solicitante
    não pode ver, e bastaria um esquecimento no filtro para exibi-lo.
    """
    with obter_cursor(usuario_id=usuario_id, dicionario=True) as cursor:
        cursor.execute(_SQL_POR_HOSPEDE, (hospede_id,))
        return cursor.fetchall()


def contar_ativas(usuario_id=None) -> int:
    with obter_cursor(usuario_id=usuario_id) as cursor:
        cursor.execute(_SQL_CONTAR_ATIVAS)
        return cursor.fetchone()[0]
