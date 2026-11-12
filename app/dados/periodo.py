"""Período de datas: conversão e validação, sem tocar em banco.

Módulo extraído de `reserva_repo` no Bloco 5 da Etapa 5, e a razão é a
regra de isolamento da face pública.

A consulta pública de disponibilidade precisa validar o período informado
pelo visitante — mesmo formato, mesma regra de saída posterior à entrada,
mesmo teto de diárias que a face interna aplica. Mas a face pública não
pode alcançar `reserva_repo`: importá-lo lhe daria também `listar`,
`buscar_por_id` e `listar_por_hospede`, e o isolamento da Etapa 4
deixaria de valer.

As duas alternativas eram piores:

**Duplicar a validação** em `vitrine.py` criaria duas cópias da mesma
regra, livres para divergir. Já aconteceu neste projeto com a regra de
não-sobreposição, e a lição foi que uma regra em dois lugares é uma regra
que vai discordar de si mesma.

**Afrouxar o isolamento** para deixar a face pública importar
`reserva_repo` trocaria uma propriedade verificada por teste — a face
pública não tem como ler reserva — por conveniência de implementação.

Nenhuma função aqui acessa banco, nenhuma devolve dado de pessoa. É
aritmética de calendário, e pode ser alcançada por qualquer face.

`reserva_repo` continua expondo `converter_data` e `validar_periodo` com
a sua própria exceção, de modo que nada do que já estava escrito mudou de
comportamento.
"""

import datetime as dt

# Teto de diárias de uma estadia.
#
# Noventa dias não é regra de negócio da hospedaria: é defesa contra
# engano de digitação e contra abuso. Uma reserva de 2026 a 2031, por um
# ano errado no campo de saída, bloquearia o chalé por cinco anos e o
# cálculo noite a noite percorreria mil e oitocentas diárias.
MAXIMO_DE_DIARIAS = 90


class ErroDePeriodo(Exception):
    """O período informado não serve.

    A mensagem é escrita para ser exibida: fala de campo e de formato,
    nunca de banco nem de biblioteca.
    """


def converter_data(valor, rotulo: str) -> dt.date:
    """Aceita date, datetime ou texto em AAAA-MM-DD.

    O formato é o que o `<input type="date">` do HTML envia, e também o
    da norma ISO 8601 — o que evita a ambiguidade entre 03/04 e 04/03.
    """
    if isinstance(valor, dt.datetime):
        return valor.date()
    if isinstance(valor, dt.date):
        return valor
    if not valor:
        raise ErroDePeriodo(f"A {rotulo} é obrigatória.")
    try:
        return dt.date.fromisoformat(str(valor).strip())
    except ValueError:
        raise ErroDePeriodo(
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
        raise ErroDePeriodo(
            "A data de saída precisa ser posterior à data de entrada."
        )

    diarias = (saida - entrada).days
    if diarias > MAXIMO_DE_DIARIAS:
        raise ErroDePeriodo(
            f"A estadia tem {diarias} diárias; o máximo é "
            f"{MAXIMO_DE_DIARIAS}. Verifique as datas."
        )

    return entrada, saida
