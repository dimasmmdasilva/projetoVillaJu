"""O pedido de reserva feito pelo próprio hóspede — Etapa 5, Bloco 7.

O hóspede escolhe chalé, datas e pessoas; vê o valor, a modalidade e os
prazos; aceita o regulamento; e o pedido nasce `pendente`, segurando o
chalé por 90 minutos. Nesse tempo ele faz o Pix e clica "Fiz o Pix". Sem
o clique, a varredura cancela o pedido e o chalé volta a ficar livre.

**O sistema não transaciona.** O Pix acontece entre o aplicativo do banco
do hóspede e o da pousada. O clique do hóspede é uma **declaração**: ela
para a contagem e põe o pedido na fila da equipe, que confere o extrato.
Quem confirma é a equipe — e isso é o Bloco 8.

## Onde cada coisa mora

- **As regras** — 7 dias, 90 minutos, 48 horas, 50% — em
  `app/servicos/regulamento.py`, que a página inicial também lê. O
  regulamento que o hóspede lê e a regra que o sistema aplica saem das
  mesmas constantes.
- **O SQL** em `app/dados/reserva_repo.py`, como todo o resto.
- **Aqui**, a costura: validar, calcular, decidir a modalidade, chamar o
  repositório e preparar o que o card do hóspede mostra.

## Nenhum valor vem do formulário

O formulário traz chalé, datas e pessoas. O valor é recalculado no
servidor, noite a noite, a cada passo — na tela de confirmação e de novo
na gravação. Um campo oculto com o valor seria o primeiro alvo de quem
quisesse pagar menos, e está na lista de testes de abuso do Relatório 2.
"""

import datetime as dt
import math
from decimal import ROUND_HALF_UP, Decimal

from app.dados import auditoria_repo, chale_repo, reserva_repo
from app.dados.calculo import TarifaAusente
from app.dados.calculo import valor_total as calcular_valor_total
from app.dados.erros import ErroDeDados
from app.servicos import regulamento

PRIMEIRO = reserva_repo.PRIMEIRO
RESTANTE = reserva_repo.RESTANTE

_CENTAVO = Decimal("0.01")


class ErroDePedido(Exception):
    """O pedido não pode seguir. A mensagem vai para a tela."""


# ---------------------------------------------------------------------------
# Valores
# ---------------------------------------------------------------------------

def dividir(total: Decimal, modalidade: str) -> tuple:
    """Divide o total em (primeiro Pix, restante).

    Na modalidade `sinal`, o primeiro é 50% arredondado ao centavo, e o
    restante é **a diferença**, e não outros 50% arredondados: assim os
    dois somam exatamente o total, mesmo quando ele tem centavo ímpar.
    """
    total = Decimal(total).quantize(_CENTAVO)

    if modalidade == regulamento.INTEGRAL:
        return total, Decimal("0.00")

    primeiro = (
        total * Decimal(regulamento.PERCENTUAL_DO_SINAL) / Decimal(100)
    ).quantize(_CENTAVO, rounding=ROUND_HALF_UP)

    return primeiro, total - primeiro


def em_reais(valor) -> str:
    """`Decimal('1300.5')` vira `'R$ 1.300,50'`."""
    if valor is None:
        return "—"
    texto = f"{Decimal(valor).quantize(_CENTAVO):,.2f}"
    return "R$ " + texto.replace(",", "_").replace(".", ",").replace("_", ".")


# ---------------------------------------------------------------------------
# Tempo
# ---------------------------------------------------------------------------

def tempo_restante(prazo: dt.datetime, agora: dt.datetime) -> str:
    """Quanto falta, arredondado para cima: "1 h 12 min", "3 dias e 4 h".

    Para cima, e não para baixo: mostrar "0 min" com 40 segundos
    restantes faria a pessoa desistir de um prazo que ainda vale.
    """
    segundos = (prazo - agora).total_seconds()
    if segundos <= 0:
        return "prazo encerrado"

    minutos = math.ceil(segundos / 60)
    if minutos < 60:
        return f"{minutos} min"

    horas, minutos = divmod(minutos, 60)
    if horas < 48:
        return f"{horas} h {minutos:02d} min"

    dias, horas = divmod(horas, 24)
    return f"{dias} dias e {horas} h"


def prazo_por_extenso(prazo: dt.datetime, agora: dt.datetime) -> str:
    """"até 14h32 de hoje", "até 23h59 de 18/11/2026".

    O horário escrito vale mesmo que a pessoa deixe a página parada: a
    contagem envelhece, o horário não.
    """
    hora = f"{prazo.hour}h{prazo.minute:02d}"
    if prazo.date() == agora.date():
        return f"até {hora} de hoje"
    if prazo.date() == agora.date() + dt.timedelta(days=1):
        return f"até {hora} de amanhã"
    return f"até {hora} de {prazo:%d/%m/%Y}"


# ---------------------------------------------------------------------------
# O pedido
# ---------------------------------------------------------------------------

def _inteiro(valor, mensagem: str) -> int:
    try:
        return int(valor)
    except (TypeError, ValueError):
        raise ErroDePedido(mensagem) from None


def simular(dados: dict, hoje: dt.date = None) -> dict:
    """Confere o pedido e calcula tudo, **sem gravar nada**.

    É o que a tela de confirmação mostra antes do aceite, e é também o
    primeiro passo de `pedir`: o que foi mostrado e o que será gravado
    saem da mesma conta.
    """
    hoje = hoje or dt.date.today()

    chale_id = _inteiro(dados.get("chale"), "Escolha um chalé.")
    pessoas = _inteiro(
        dados.get("pessoas"), "Informe quantas pessoas vão se hospedar."
    )

    try:
        entrada, saida = reserva_repo.validar_periodo(
            dados.get("entrada"), dados.get("saida")
        )
    except ErroDeDados as erro:
        raise ErroDePedido(str(erro)) from erro

    # A equipe pode registrar estadia retroativa; o hóspede, não.
    if entrada < hoje:
        raise ErroDePedido("A data de entrada já passou.")

    chale = chale_repo.buscar_por_id(chale_id)
    if chale is None or not chale.get("ativo"):
        raise ErroDePedido("Escolha um chalé.")

    if pessoas < 1:
        raise ErroDePedido("A reserva precisa ter ao menos uma pessoa.")
    if pessoas > chale["capacidade"]:
        raise ErroDePedido(
            f"O chalé {chale['nome']} recebe até {chale['capacidade']} "
            f"pessoas."
        )

    try:
        total = calcular_valor_total(chale_id, entrada, saida)
    except TarifaAusente:
        raise ErroDePedido(
            "Ainda não há valor cadastrado para este período. Fale com a "
            "recepção para fechar a reserva."
        ) from None

    modalidade = regulamento.modalidade_para(entrada, hoje)
    primeiro, restante = dividir(total, modalidade)

    return {
        "chale_id": chale_id,
        "chale_nome": chale["nome"],
        "entrada": entrada,
        "saida": saida,
        "noites": (saida - entrada).days,
        "pessoas": pessoas,
        "total": total,
        "modalidade": modalidade,
        "primeiro": primeiro,
        "restante": restante,
        "prazo_restante": (
            regulamento.prazo_do_restante(entrada)
            if modalidade == regulamento.SINAL else None
        ),
        "ultimo_dia_com_devolucao": regulamento.ultimo_dia_com_devolucao(
            entrada
        ),
        "tem_devolucao": regulamento.tem_devolucao(entrada, hoje),
        "disponivel": reserva_repo.chale_disponivel(chale_id, entrada, saida),
    }


def pedir(usuario: dict, dados: dict, hoje: dt.date = None) -> int:
    """Grava o pedido. Devolve o número da reserva.

    `usuario` é a linha lida do banco nesta requisição — não a sessão do
    navegador. O `hospede_id` vem dela, e de nenhum outro lugar.
    """
    hospede_id = usuario.get("hospede_id")
    if not hospede_id:
        raise ErroDePedido(
            "Sua conta não está vinculada a um cadastro de hóspede."
        )

    # O aceite vem antes de qualquer cálculo: sem ele não há pedido, e
    # não há por que gastar uma consulta.
    if not dados.get("aceite"):
        raise ErroDePedido(
            "Para reservar, é preciso ler e aceitar o regulamento da pousada."
        )

    resumo = simular(dados, hoje=hoje)

    # **A disponibilidade NÃO é decidida aqui, pelo `resumo`.** A primeira
    # versão recusava o pedido quando `resumo["disponivel"]` era falso — e
    # essa leitura acontece ANTES da transação que varre os pedidos
    # vencidos. Um pedido de outra pessoa, vencido e ainda não cancelado,
    # "ocupava" o chalé na leitura, e o pedido novo era recusado por causa
    # dele. Foi o teste da regra de ordem que acusou — o mesmo defeito do
    # Bloco 4, desta vez no código novo.
    #
    # Quem decide é `criar_pedido`: varre, trava, valida e grava, nessa
    # ordem e na mesma transação.

    try:
        identificador = reserva_repo.criar_pedido(
            {
                "chale_id": resumo["chale_id"],
                "hospede_id": hospede_id,
                "data_entrada": resumo["entrada"],
                "data_saida": resumo["saida"],
                "num_pessoas": resumo["pessoas"],
                "valor_total": resumo["total"],
            },
            modalidade=resumo["modalidade"],
            minutos_para_o_pix=regulamento.MINUTOS_PARA_O_PIX,
            prazo_restante=resumo["prazo_restante"],
            limite_em_aberto=regulamento.LIMITE_DE_PEDIDOS_EM_ABERTO,
            usuario_id=usuario["id"],
        )
    except reserva_repo.ConflitoDePeriodo as erro:
        # **A mensagem do repositório NÃO vai para a tela do hóspede.**
        # Ela foi escrita para a equipe e diz as datas da reserva que
        # conflita — "já está reservado de 15/12 a 20/12". Para a equipe é
        # informação de trabalho; para um hóspede, é saber quando outra
        # pessoa vai estar na pousada. A face pública foi desenhada para
        # nunca mostrar isso, e este caminho não pode ser a exceção.
        raise ErroDePedido(
            f"O chalé {resumo['chale_nome']} já está reservado em parte "
            f"desse período. Escolha outras datas."
        ) from erro
    except ErroDeDados as erro:
        # As demais mensagens do repositório falam só do que a pessoa
        # pediu: o limite de pedidos, a capacidade, o chalé inexistente.
        raise ErroDePedido(str(erro)) from erro

    auditoria_repo.registrar(
        auditoria_repo.PEDIDO_DE_RESERVA,
        usuario_id=usuario["id"],
        tabela="reserva",
        registro_id=identificador,
        detalhe=(
            f"Pedido pelo site, pagamento {resumo['modalidade']}; "
            f"regulamento aceito"
        ),
    )

    return identificador


def informar_pix(usuario: dict, identificador, etapa: str) -> bool:
    """O hóspede declara que fez o Pix. Devolve se a declaração valeu.

    `False` sem explicação para todo caso de recusa. A tela diz que a
    reserva não aceita mais essa confirmação, e não diz por quê: dizer
    "esta reserva não é sua" confirmaria que o número existe.
    """
    hospede_id = usuario.get("hospede_id")
    if not hospede_id:
        return False

    valeu = reserva_repo.informar_pix(
        identificador, hospede_id, etapa, usuario_id=usuario["id"]
    )

    if valeu:
        auditoria_repo.registrar(
            auditoria_repo.PIX_INFORMADO,
            usuario_id=usuario["id"],
            tabela="reserva",
            registro_id=identificador,
            detalhe=(
                "Hóspede informou o Pix do sinal ou do valor integral"
                if etapa == PRIMEIRO
                else "Hóspede informou o Pix do restante"
            ),
        )

    return valeu


def desistir(usuario: dict, identificador) -> bool:
    """Desiste de um pedido ainda sem Pix. Devolve se valeu."""
    hospede_id = usuario.get("hospede_id")
    if not hospede_id:
        return False

    return reserva_repo.desistir(
        identificador, hospede_id, usuario_id=usuario["id"]
    )


# ---------------------------------------------------------------------------
# O card do hóspede
# ---------------------------------------------------------------------------

_MOTIVOS = {
    "prazo_do_pix": "o Pix não foi informado dentro do prazo",
    "falta_de_pagamento": "o pagamento não foi identificado",
    "pedido_do_hospede": "você desistiu do pedido",
    "equipe": "cancelada pela equipe da pousada",
}


def cartao(reserva: dict, agora: dt.datetime = None) -> dict:
    """O que o card de uma reserva mostra, e quais botões oferece.

    Calculado aqui, e não no template, para que cada estado seja testável
    sem desenhar HTML.

    **O botão aparece só quando o banco aceitaria o clique.** Mas a
    decisão que vale é a do banco: o UPDATE carrega a condição inteira.
    Se a página ficar aberta além do prazo, o botão ainda está lá — e o
    clique é recusado.
    """
    agora = agora or dt.datetime.now()
    modalidade = reserva.get("modalidade")
    situacao = reserva["situacao"]
    total = reserva.get("valor_total")

    estado = {
        "etapa": None,
        "texto": "",
        "a_pagar": None,
        "prazo": None,
        "restam": None,
        "botao": None,
        "pode_desistir": False,
        "atualizar": False,
        "modalidade": modalidade,
        "total": total,
    }

    if situacao == "cancelada":
        motivo = _MOTIVOS.get(reserva.get("motivo_cancelamento"))
        estado["etapa"] = "cancelada"
        estado["texto"] = (
            f"Reserva cancelada: {motivo}." if motivo else "Reserva cancelada."
        )
        return estado

    if situacao == "concluida":
        estado["etapa"] = "concluida"
        estado["texto"] = "Estadia concluída. Obrigado pela visita!"
        return estado

    if modalidade is None:
        # Reserva registrada pela recepção: segue o fluxo manual.
        estado["etapa"] = "pela_recepcao"
        estado["texto"] = (
            "Reserva registrada pela recepção. O pagamento é combinado "
            "diretamente com a equipe."
        )
        return estado

    primeiro, restante = dividir(total, modalidade)

    if situacao == "pendente":
        if reserva.get("pix_informado_em"):
            estado["etapa"] = "pix_em_conferencia"
            estado["texto"] = (
                f"Você informou o Pix em "
                f"{reserva['pix_informado_em']:%d/%m/%Y às %Hh%M}. A equipe "
                f"vai conferir o extrato e confirmar a reserva."
            )
            return estado

        prazo = reserva["prazo_pagamento"]
        if prazo <= agora:
            estado["etapa"] = "prazo_vencido"
            estado["texto"] = (
                "O prazo do Pix terminou. O pedido será cancelado e o "
                "chalé volta a ficar disponível."
            )
            return estado

        rotulo = (
            f"Fiz o Pix 100% — {em_reais(primeiro)}"
            if modalidade == regulamento.INTEGRAL
            else f"Fiz o Pix 50% — {em_reais(primeiro)}"
        )
        estado.update(
            etapa="aguardando_pix",
            texto=(
                f"Faça o Pix de {em_reais(primeiro)} e informe pelo botão "
                f"abaixo. Sem a informação dentro do prazo, o pedido é "
                f"cancelado e o chalé volta a ficar disponível."
            ),
            a_pagar=primeiro,
            prazo=prazo_por_extenso(prazo, agora),
            restam=tempo_restante(prazo, agora),
            botao={"etapa": PRIMEIRO, "rotulo": rotulo},
            pode_desistir=True,
            # A página se atualiza sozinha enquanto a contagem é de
            # minutos. Nos outros estados, recarregar não muda nada.
            atualizar=True,
        )
        return estado

    # Confirmada.
    if modalidade == regulamento.SINAL and not reserva.get(
        "restante_informado_em"
    ):
        prazo = reserva["prazo_restante"]
        if prazo <= agora:
            estado["etapa"] = "restante_vencido"
            estado["texto"] = (
                "O prazo do restante terminou. Fale com a recepção."
            )
            return estado

        estado.update(
            etapa="aguardando_restante",
            texto=(
                f"Sinal confirmado. Falta {em_reais(restante)}, até 48 "
                f"horas antes da entrada."
            ),
            a_pagar=restante,
            prazo=prazo_por_extenso(prazo, agora),
            restam=tempo_restante(prazo, agora),
            botao={
                "etapa": RESTANTE,
                "rotulo": (
                    f"Fiz o Pix dos 50% restantes — {em_reais(restante)}"
                ),
            },
        )
        return estado

    if modalidade == regulamento.SINAL:
        estado["etapa"] = "restante_em_conferencia"
        estado["texto"] = (
            f"Você informou o Pix do restante em "
            f"{reserva['restante_informado_em']:%d/%m/%Y às %Hh%M}. A "
            f"equipe vai conferir o extrato."
        )
        return estado

    estado["etapa"] = "confirmada"
    estado["texto"] = (
        "Reserva confirmada. Esperamos você na VillaJu para viver ótimos "
        "momentos."
    )
    return estado


__all__ = [
    "ErroDePedido",
    "PRIMEIRO",
    "RESTANTE",
    "cartao",
    "desistir",
    "dividir",
    "em_reais",
    "informar_pix",
    "pedir",
    "prazo_por_extenso",
    "simular",
    "tempo_restante",
]
