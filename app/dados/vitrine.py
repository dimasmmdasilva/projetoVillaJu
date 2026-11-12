"""Disponibilidade para quem não tem conta: a segunda porta da face pública.

Este módulo existe para ser **o único caminho** pelo qual a face pública
alcança a tabela `reserva`, e é escrito com essa restrição como primeira
preocupação.

---

## O que ele responde, e o que não responde

Responde: **este chalé está livre neste período?** Sim ou não.

Não responde, e não tem como responder:

- quem está hospedado — nenhuma consulta aqui toca `hospede`;
- em que datas a reserva alheia começa ou termina — as datas entram como
  parâmetro e não saem como resultado;
- quantas reservas existem — as consultas são `EXISTS` e `NOT EXISTS`,
  que devolvem presença e não contagem;
- quanto custa — `tarifa` e `calculo` ficam fora do alcance, pela decisão
  de não publicar preço.

**Nenhuma função devolve registro de reserva.** `esta_livre` devolve um
booleano; `nomes_livres` devolve nomes de chalé, que já estão impressos na
vitrine para qualquer visitante ver. É o teto do que esta face pode saber.

## Por que um módulo, e não `reserva_repo`

A Etapa 4 estabeleceu, e verificou por teste de travessia de importações,
que a face pública não alcança `reserva_repo`. Importá-lo aqui traria
junto `listar`, `buscar_por_id`, `listar_por_hospede` e
`chales_ocupados` — e esse último devolve identificador de reserva,
datas e situação, que é exatamente o que não pode sair.

A regra foi **estreitada, não abandonada**. A face pública alcança dois
módulos de porta, e cada um tem uma direção só:

    app/servicos/cadastro.py    escreve, não lê
    app/dados/vitrine.py        lê, não escreve

E a propriedade essencial continua verificável: a face pública não tem
como exibir dado de hóspede nem de reserva.

## Por que não varre nem escreve

O Bloco 7 vai dar à reserva um prazo de sinal de 24 horas, e uma reserva
com prazo vencido deixa de ocupar o chalé. A varredura que efetiva esse
vencimento — que muda a situação para `cancelada` e deixa linha de
auditoria — pertence ao lado autenticado.

Aqui o vencimento é **ignorado na leitura**, por condição no próprio
`SELECT`. A diferença importa: varrer daqui daria escrita em `reserva` a
uma face que não deve ter nenhuma, e a linha de auditoria nasceria sem
autor possível.

A consequência aceita é uma janela curta de discordância entre as duas
telas: a pública já mostra o chalé como livre enquanto a interna ainda
mostra a reserva vencida, até alguém da equipe acessar o sistema. Como a
criação de reserva varre antes de inserir, a reserva que o visitante for
fazer vai de fato caber.
"""

import logging

from app.dados.conexao import obter_cursor
from app.dados.periodo import ErroDePeriodo, validar_periodo  # noqa: F401

_registrador = logging.getLogger(__name__)

# As situações que ocupam o chalé.
#
# As mesmas duas da trigger `trg_reserva_antes_inserir`, e isso não é
# coincidência: se esta lista divergisse da trigger, a tela diria livre
# onde a reserva seria recusada, ou diria ocupado onde cabia. Há teste
# conferindo que as duas listas são iguais, lendo a trigger do arquivo
# SQL — porque a cópia é inevitável (uma está em SQL no banco, a outra em
# Python) e o que se pode fazer é impedi-las de divergir em silêncio.
SITUACOES_QUE_OCUPAM = ("pendente", "confirmada")

# Intervalo semiaberto, igual ao da trigger: a reserva ocupa de
# `data_entrada` inclusive até `data_saida` exclusive, de modo que a saída
# de um hóspede libere o chalé para a entrada de outro no mesmo dia.
#
# `%s < r.data_saida AND %s > r.data_entrada` — a entrada consultada antes
# da saída existente, e a saída consultada depois da entrada existente.
_SQL_ESTA_LIVRE = (
    "SELECT NOT EXISTS ( "
    "    SELECT 1 FROM reserva r "
    "    WHERE r.chale_id = %s "
    "      AND r.situacao IN ('pendente', 'confirmada') "
    "      AND %s < r.data_saida "
    "      AND %s > r.data_entrada "
    ") AS livre "
    "FROM chale c WHERE c.id = %s AND c.ativo = TRUE"
)

# Apenas id e nome. Nem capacidade, nem descrição: quem precisa delas lê a
# vitrine, que já as publica. Aqui a resposta é só a lista de quem está
# livre.
#
# **São duas proteções e não uma, e elas protegem coisas diferentes.** A
# consulta estreita evita trazer pela rede o que a tela não mostra; a
# montagem explícita do dicionário em `nomes_livres` garante que, mesmo
# que a consulta engorde um dia, o que sai da função continue sendo id e
# nome.
#
# A sabotagem que acrescentou colunas aqui não quebrou teste nenhum, e a
# razão é a segunda proteção. Cada uma passou a ter o seu teste, porque
# um teste que só olha o resultado final não distingue as duas.
_SQL_NOMES_LIVRES = (
    "SELECT c.id, c.nome FROM chale c "
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


class ChaleIndisponivel(Exception):
    """O chalé não existe, ou está desativado.

    Os dois casos juntos, e de propósito. Um chalé desativado é um chalé
    que a hospedaria tirou de operação, e informar que ele existe mas está
    desligado é informação de operação interna — além de permitir contar
    quantos chalés a hospedaria tem, somando os que respondem.
    """


def esta_livre(chale_id, entrada, saida) -> bool:
    """Se o chalé está livre no período. Levanta se o chalé não serve.

    **A única função que a face pública precisa**, e o teto do que ela
    pode saber: um booleano.

    Levanta `ErroDePeriodo` para data mal formada ou período inválido, com
    mensagem que pode ir à tela, e `ChaleIndisponivel` para chalé que não
    existe ou está desativado.
    """
    entrada, saida = validar_periodo(entrada, saida)

    try:
        chale_id = int(chale_id)
    except (TypeError, ValueError):
        raise ChaleIndisponivel("Chalé não encontrado.") from None

    with obter_cursor(dicionario=True) as cursor:
        cursor.execute(_SQL_ESTA_LIVRE, (chale_id, entrada, saida, chale_id))
        linha = cursor.fetchone()

    if linha is None:
        raise ChaleIndisponivel("Chalé não encontrado.")

    return bool(linha["livre"])


def nomes_livres(entrada, saida) -> list:
    """Os chalés livres no período, com id e nome apenas.

    Serve à saída que a tela oferece quando o chalé consultado está
    ocupado. Sem ela, o visitante testaria os cinco um por um — ou
    desistiria, que é o resultado provável.

    Devolve lista de dicionários com `id` e `nome`, e nada mais. Nome de
    chalé é dado público: está impresso na vitrine.
    """
    entrada, saida = validar_periodo(entrada, saida)

    with obter_cursor(dicionario=True) as cursor:
        cursor.execute(_SQL_NOMES_LIVRES, (entrada, saida))
        return [
            {"id": linha["id"], "nome": linha["nome"]}
            for linha in cursor.fetchall()
        ]


__all__ = [
    "ChaleIndisponivel",
    "ErroDePeriodo",
    "SITUACOES_QUE_OCUPAM",
    "esta_livre",
    "nomes_livres",
    "validar_periodo",
]
