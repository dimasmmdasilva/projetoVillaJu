"""A equipe confere o Pix — Etapa 5, Bloco 8.

Duas telas:

- **Os cards** (8a), para administrador e recepção: um por pedido do site
  com algo a resolver, ordenados por urgência, com a lista completa de
  quem vai e os botões que a situação permite.
- **O controle** (8c), só do administrador: o ano em 12 meses, e os
  lançamentos de um mês com quem conferiu.

**Não é um módulo financeiro.** O sistema cuida do aluguel dos chalés; o
dinheiro aparece como o estado de uma reserva. Nenhuma ação move dinheiro:
cada uma registra um fato que alguém conferiu no extrato.

## Urgência

1. **Pix informado** pelo hóspede, aguardando a conferência da equipe —
   alguém está esperando uma resposta.
2. **Restante com prazo vencido, ou vencendo em até 2 dias.**
3. O resto: aguardando o hóspede.

## Os botões aparecem só quando o banco aceitaria o clique

E quem decide é o repositório, com a reserva travada. O botão "Cancelar
por falta de pagamento" aparece só quando o hóspede declarou o Pix e ele
não foi encontrado, ou quando o prazo do restante venceu. Um pedido sem
declaração, dentro dos 90 minutos, não tem o que cancelar: a varredura
cuida dele no vencimento.
"""

import calendar
import datetime as dt
import logging
from decimal import Decimal

from app.dados import (
    acompanhante_repo,
    auditoria_repo,
    hospede_repo,
    pagamento_repo,
    reserva_repo,
)
from app.seguranca import cripto
from app.servicos import pedido, regulamento

_log = logging.getLogger(__name__)

URGENCIA_PIX_INFORMADO = 1
URGENCIA_RESTANTE = 2
URGENCIA_AGUARDANDO = 3

DIAS_DE_ANTECEDENCIA_DO_AVISO = 2

PRIMEIRO = "primeiro"
RESTANTE = "restante"
CANCELAR = "cancelar"
DEVOLVER = "devolver"
ACOES = (PRIMEIRO, RESTANTE, CANCELAR, DEVOLVER)

# Os percentuais saem do regulamento, como todo número que a tela mostra:
# o teste do "Como usar" (18b) achou o 50 escrito à mão aqui e nos botões.
ROTULOS_DO_TIPO = {
    "sinal": f"sinal ({regulamento.PERCENTUAL_DO_SINAL}%)",
    "restante": f"restante ({100 - regulamento.PERCENTUAL_DO_SINAL}%)",
    "integral": "integral (100%)",
    "devolucao": "devolução",
}

MESES = (
    "janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho",
    "agosto", "setembro", "outubro", "novembro", "dezembro",
)


# ---------------------------------------------------------------------------
# Quem vai — a lista completa, para a equipe
# ---------------------------------------------------------------------------

def titular_completo(hospede_id, usuario_id):
    """Nome e documento inteiros: é para a conferência na chegada."""
    try:
        cadastro = hospede_repo.buscar_por_id(hospede_id, usuario_id=usuario_id)
    except cripto.FalhaDeAutenticacao:
        _log.warning("Cadastro ilegível do hóspede %s no card da equipe",
                     hospede_id)
        cadastro = None
    if cadastro is None:
        return {"nome": "", "documento": "", "legivel": False}
    return {
        "nome": cadastro["nome"],
        "documento": cadastro.get("documento") or "",
        "telefone": cadastro.get("telefone") or "",
        "legivel": True,
    }


def _acompanhantes(reserva_id, usuario_id):
    try:
        return acompanhante_repo.listar_da_reserva(
            reserva_id, usuario_id=usuario_id
        ), True
    except cripto.FalhaDeAutenticacao:
        _log.warning("Acompanhante ilegível na reserva %s", reserva_id)
        return [], False


# ---------------------------------------------------------------------------
# 8a — os cards
# ---------------------------------------------------------------------------

def avaliar(reserva: dict, lancamentos: dict, agora: dt.datetime) -> dict:
    """Situação, urgência e botões de um card. Sem banco: testável puro."""
    modalidade = reserva["modalidade"]
    primeiro, restante = pedido.dividir(reserva["valor_total"], modalidade)
    situacao = reserva["situacao"]

    estado = {
        "primeiro": primeiro,
        "restante": restante,
        "valor_pago": sum(lancamentos.values(), Decimal("0.00")),
        "urgencia": URGENCIA_AGUARDANDO,
        "texto": "",
        "botoes": [],
        "pode_cancelar": False,
    }
    rotulo_primeiro = (
        f"Pagou {regulamento.PERCENTUAL_DO_SINAL}% Pix — {pedido.em_reais(primeiro)}"
        if modalidade == regulamento.SINAL
        else f"Pagou 100% Pix — {pedido.em_reais(primeiro)}"
    )

    if situacao == "pendente":
        estado["botoes"] = [{"acao": PRIMEIRO, "rotulo": rotulo_primeiro}]
        if reserva.get("pix_informado_em"):
            estado["urgencia"] = URGENCIA_PIX_INFORMADO
            estado["texto"] = (
                f"O hóspede informou o Pix em "
                f"{reserva['pix_informado_em']:%d/%m/%Y às %Hh%M}. "
                f"Confira o extrato."
            )
            estado["pode_cancelar"] = True
        else:
            estado["texto"] = (
                "Aguardando o Pix do hóspede "
                + pedido.prazo_por_extenso(reserva["prazo_pagamento"], agora)
                + "."
            )
        return estado

    # Confirmada, com sinal e sem o restante lançado (a consulta já tirou
    # as quitadas).
    estado["botoes"] = [{
        "acao": RESTANTE,
        "rotulo": f"Pagou 100% Pix — {pedido.em_reais(restante)} (restante)",
    }]
    prazo = reserva.get("prazo_restante")
    if reserva.get("restante_informado_em"):
        estado["urgencia"] = URGENCIA_PIX_INFORMADO
        estado["texto"] = (
            f"Sinal confirmado. O hóspede informou o Pix do restante em "
            f"{reserva['restante_informado_em']:%d/%m/%Y às %Hh%M}. "
            f"Confira o extrato."
        )
        estado["pode_cancelar"] = True
    elif prazo and prazo <= agora:
        estado["urgencia"] = URGENCIA_RESTANTE
        estado["texto"] = "Sinal confirmado. O prazo do restante venceu."
        estado["pode_cancelar"] = True
    else:
        if prazo and prazo <= agora + dt.timedelta(
            days=DIAS_DE_ANTECEDENCIA_DO_AVISO
        ):
            estado["urgencia"] = URGENCIA_RESTANTE
        estado["texto"] = (
            "Sinal confirmado. Restante "
            + (pedido.prazo_por_extenso(prazo, agora) if prazo else "")
            + "."
        )
    return estado


def cards(usuario_id, agora: dt.datetime = None) -> list:
    """Os cards da equipe, do mais urgente ao menos urgente.

    **A varredura vem antes da leitura** — a lição de ordem do Bloco 4:
    um pedido vencido e ainda não cancelado não pode aparecer como
    pendência.
    """
    agora = agora or dt.datetime.now()
    reserva_repo.cancelar_pedidos_vencidos(usuario_id=usuario_id)

    lista = []
    for reserva in pagamento_repo.pendencias(usuario_id=usuario_id):
        lancamentos = pagamento_repo.lancamentos_da_reserva(
            reserva["id"], usuario_id=usuario_id
        )
        acompanhantes, legiveis = _acompanhantes(reserva["id"], usuario_id)
        lista.append({
            "reserva": reserva,
            "estado": avaliar(reserva, lancamentos, agora),
            "titular": titular_completo(reserva["hospede_id"], usuario_id),
            "acompanhantes": acompanhantes,
            "acompanhantes_legiveis": legiveis,
        })

    lista.sort(key=lambda card: (
        card["estado"]["urgencia"],
        card["reserva"]["data_entrada"],
        card["reserva"]["id"],
    ))
    return lista


def devolucoes(usuario_id) -> list:
    """Bloco 9: as devoluções que a equipe ainda precisa fazer.

    Só as que têm direito pela regra dos 7 dias, contados do dia do
    cancelamento até a entrada. O botão diz quanto voltar: "Pix 50%
    retornado" quando entrou só o sinal, "Pix 100% retornado" quando
    entrou tudo.
    """
    lista = []
    for reserva in pagamento_repo.devolucoes_em_aberto(usuario_id=usuario_id):
        if not regulamento.tem_direito_a_devolucao(
            reserva["motivo_cancelamento"], reserva["data_entrada"],
            reserva["cancelado_em"].date(),
        ):
            continue
        quem = (
            "A pousada cancelou"
            if reserva["motivo_cancelamento"] == regulamento.PELA_POUSADA
            else "O hóspede cancelou"
        )
        pago = reserva["pago"]
        percentual = 100 if pago >= reserva["valor_total"] else (
            regulamento.PERCENTUAL_DO_SINAL)
        acompanhantes, legiveis = _acompanhantes(reserva["id"], usuario_id)
        lista.append({
            "reserva": reserva,
            "titular": titular_completo(reserva["hospede_id"], usuario_id),
            "acompanhantes": acompanhantes,
            "acompanhantes_legiveis": legiveis,
            "rotulo": f"Pix {percentual}% retornado — {pedido.em_reais(pago)}",
            "texto": (
                f"{quem} em "
                f"{reserva['cancelado_em']:%d/%m/%Y às %Hh%M}, com direito à "
                f"devolução. Faça o Pix de volta pelo banco e registre."
            ),
        })
    return lista


def executar(acao: str, reserva_id, usuario: dict) -> bool:
    """Um botão da equipe. Devolve se valeu; o log registra o que valeu."""
    if acao == PRIMEIRO:
        valeu = pagamento_repo.confirmar_primeiro(
            reserva_id, pedido.dividir, usuario_id=usuario["id"]
        )
        detalhe = "Equipe conferiu no extrato o primeiro Pix (sinal ou integral)"
        evento = auditoria_repo.PAGAMENTO_CONFIRMADO
    elif acao == RESTANTE:
        valeu = pagamento_repo.confirmar_restante(
            reserva_id, pedido.dividir, usuario_id=usuario["id"]
        )
        detalhe = "Equipe conferiu no extrato o Pix do restante"
        evento = auditoria_repo.PAGAMENTO_CONFIRMADO
    elif acao == DEVOLVER:
        valeu = pagamento_repo.registrar_devolucao(
            reserva_id, regulamento.tem_direito_a_devolucao,
            usuario_id=usuario["id"],
        )
        detalhe = "Equipe registrou a devolução do Pix ao hóspede"
        evento = auditoria_repo.DEVOLUCAO_REGISTRADA
    elif acao == CANCELAR:
        valeu = pagamento_repo.cancelar_por_falta_de_pagamento(
            reserva_id, usuario_id=usuario["id"]
        )
        detalhe = "Equipe cancelou a reserva: Pix não encontrado no extrato"
        evento = auditoria_repo.CANCELADA_SEM_PAGAMENTO
    else:
        return False

    if valeu:
        auditoria_repo.registrar(
            evento,
            usuario_id=usuario["id"],
            tabela="reserva",
            registro_id=reserva_id,
            detalhe=detalhe,
        )
    return valeu


# ---------------------------------------------------------------------------
# Bloco 17e — a equipe cancela, dizendo o motivo
# ---------------------------------------------------------------------------
#
# Até aqui o "Marcar como cancelada" só trocava a situação: a reserva paga
# ficava sem devolução, fora de "Devoluções" e sem dizer ao hóspede quem
# cancelou. Agora a equipe escolhe o motivo, e o motivo decide a devolução,
# pela mesma regra do site (`regulamento.tem_direito_a_devolucao`).

ROTULOS_DO_MOTIVO = {
    regulamento.A_PEDIDO_DO_HOSPEDE: "a pedido do hóspede",
    regulamento.PELA_POUSADA: "pela pousada",
}


def _pago(reserva_id, usuario_id) -> Decimal:
    return sum(
        (v for v in pagamento_repo.lancamentos_da_reserva(
            reserva_id, usuario_id=usuario_id).values() if v > 0),
        Decimal("0.00"),
    )


def previa_do_cancelamento(reserva: dict, usuario_id,
                           hoje: dt.date = None) -> dict:
    """Quanto voltaria ao hóspede se a equipe cancelasse hoje, por motivo.

    É o que o detalhe da reserva mostra ao lado de cada botão: a equipe
    decide sabendo o efeito.
    """
    hoje = hoje or dt.date.today()
    pago = _pago(reserva["id"], usuario_id)
    return {
        motivo: (
            pago if regulamento.tem_direito_a_devolucao(
                motivo, reserva["data_entrada"], hoje) else Decimal("0.00")
        )
        for motivo in ROTULOS_DO_MOTIVO
    }


def cancelar_pela_equipe(usuario: dict, reserva: dict, motivo: str,
                         hoje: dt.date = None):
    """Cancela com o motivo e devolve o valor a devolver. Motivo fora dos
    dois da equipe, ou reserva já encerrada, levanta `ErroDeReserva`, com a
    frase para a tela."""
    hoje = hoje or dt.date.today()
    reserva_repo.mudar_situacao(
        reserva["id"], "cancelada", usuario_id=usuario["id"], motivo=motivo
    )
    devolver = previa_do_cancelamento(reserva, usuario["id"], hoje)[motivo]
    auditoria_repo.registrar(
        auditoria_repo.CANCELADA_PELA_EQUIPE,
        usuario_id=usuario["id"],
        tabela="reserva",
        registro_id=reserva["id"],
        detalhe=(
            f"Equipe cancelou a reserva {ROTULOS_DO_MOTIVO[motivo]}; "
            + ("com devolução" if devolver > 0 else "sem devolução")
        ),
    )
    return devolver


# ---------------------------------------------------------------------------
# 8c — o controle do administrador
# ---------------------------------------------------------------------------

def _inteiro(valor, padrao):
    try:
        return int(valor)
    except (TypeError, ValueError):
        return padrao


def controle(ano=None, mes=None, usuario_id=None, hoje: dt.date = None) -> dict:
    """O ano em 12 meses, e os lançamentos de um mês escolhido.

    Ano e mês vêm da URL; fora da faixa, valem o ano corrente e nenhum mês.
    Sem comparação com o ano anterior — decisão de 13/11.
    """
    hoje = hoje or dt.date.today()
    ano = _inteiro(ano, hoje.year)
    if not 2000 <= ano <= 2100:
        ano = hoje.year
    mes = _inteiro(mes, 0)
    if not 1 <= mes <= 12:
        mes = None

    anos = sorted(set(pagamento_repo.anos(usuario_id=usuario_id)) | {hoje.year, ano},
                  reverse=True)
    totais = pagamento_repo.totais_do_ano(
        dt.date(ano, 1, 1), dt.date(ano + 1, 1, 1), usuario_id=usuario_id
    )
    meses = [
        {
            "numero": numero,
            "nome": MESES[numero - 1],
            "total": totais.get(numero, (Decimal("0.00"), 0))[0],
            "lancamentos": totais.get(numero, (Decimal("0.00"), 0))[1],
        }
        for numero in range(1, 13)
    ]

    lancamentos = []
    if mes:
        ultimo = calendar.monthrange(ano, mes)[1]
        fim = dt.date(ano, mes, ultimo) + dt.timedelta(days=1)
        for linha in pagamento_repo.lancamentos_do_periodo(
            dt.date(ano, mes, 1), fim, usuario_id=usuario_id
        ):
            titular = titular_completo(linha["hospede_id"], usuario_id)
            lancamentos.append({
                **linha,
                "hospede_nome": titular["nome"],
                # O valor do ENUM é sem acento; a tela mostra o rótulo.
                "tipo_rotulo": ROTULOS_DO_TIPO.get(linha["tipo"], linha["tipo"]),
            })

    return {
        "ano": ano,
        "anos": anos,
        "mes": mes,
        "mes_nome": MESES[mes - 1] if mes else None,
        "meses": meses,
        "total_do_ano": sum((m["total"] for m in meses), Decimal("0.00")),
        "lancamentos": lancamentos,
    }


__all__ = [
    "ACOES",
    "CANCELAR",
    "DEVOLVER",
    "PRIMEIRO",
    "RESTANTE",
    "URGENCIA_AGUARDANDO",
    "URGENCIA_PIX_INFORMADO",
    "URGENCIA_RESTANTE",
    "avaliar",
    "titular_completo",
    "cards",
    "controle",
    "devolucoes",
    "executar",
]
