"""O Painel da equipe em cards — Etapa 5, Bloco 16.

Pedido do Dimas em 20/11: juntar o Painel e a lista de Reservas numa
tela só, com um card por reserva — a foto do chalé, as datas, a situação
e um botão para o que falta. Administrador e recepção veem o mesmo
Painel; a lista cresce com o tempo e guarda todas as reservas.

## Os dois selos

Uma reserva passa por duas coisas independentes, cada uma com o seu
"concluído", e o card mostra as duas:

- **Pagamento** — aguardando Pix, aguardando conferência, 50% pago,
  restante em conferência, concluído; ou "combinado com a recepção",
  para a reserva registrada pela equipe.
- **Estadia** — próxima, em andamento, concluída; ou cancelada.

Um status só esconderia uma das duas: "concluída" no dia em que o
pagamento fecha faria parecer que o hóspede já veio. A estadia é
calculada pela data, na tela, sem mudar o banco.

## Os três grupos

1. **Precisa de ação** — Pix a conferir, restante vencido ou vencendo,
   devolução pendente. São os cards com urgência 1 ou 2 em `pagamentos`.
2. **Próximas estadias** — vivas, da entrada mais próxima à mais
   distante.
3. **Histórico** — o resto, do mais recente ao mais antigo, em páginas.
"""

import datetime as dt
import logging
import math

from app.dados import hospede_repo, pagamento_repo, reserva_repo
from app.rotas.publico import cor_da_foto
from app.seguranca import cripto
from app.servicos import pagamentos, regulamento

_log = logging.getLogger(__name__)

POR_PAGINA = 20

ACAO = "acao"
PROXIMAS = "proximas"
HISTORICO = "historico"

_MOTIVOS = {
    "prazo_do_pix": "Pix não informado no prazo",
    "falta_de_pagamento": "falta de pagamento",
    "pedido_do_hospede": "a pedido do hóspede",
    "equipe": "pela equipe",
}


# ---------------------------------------------------------------------------
# Os selos — puros, testáveis sem banco
# ---------------------------------------------------------------------------

def selo_pagamento(reserva: dict, lancamentos: dict) -> str:
    """O estado do pagamento, numa frase curta. `None` se cancelada."""
    if reserva["situacao"] == "cancelada":
        return None
    modalidade = reserva.get("modalidade")
    if modalidade is None:
        return "combinado com a recepção"
    if reserva["situacao"] == "pendente":
        if reserva.get("pix_informado_em"):
            return "aguardando conferência"
        return "aguardando Pix"
    # Confirmada: o primeiro Pix foi conferido.
    if modalidade == regulamento.INTEGRAL or "restante" in lancamentos:
        return "concluído"
    if reserva.get("restante_informado_em"):
        return "restante em conferência"
    return f"{regulamento.PERCENTUAL_DO_SINAL}% pago"


def selo_estadia(reserva: dict, hoje: dt.date) -> str:
    """O estado da estadia, pela data. Cancelada leva o motivo."""
    if reserva["situacao"] == "cancelada":
        motivo = _MOTIVOS.get(reserva.get("motivo_cancelamento"))
        return f"cancelada — {motivo}" if motivo else "cancelada"
    if reserva["situacao"] == "concluida" or reserva["data_saida"] <= hoje:
        return "concluída"
    if reserva["data_entrada"] <= hoje:
        return "em andamento"
    return f"próxima — {reserva['data_entrada']:%d/%m/%Y}"


def autor(reserva: dict) -> str:
    """Quem registrou. O login do hóspede é o HMAC do e-mail (Bloco 3) e
    não diz nada a ninguém; pedido do site se reconhece pela modalidade."""
    if reserva.get("modalidade"):
        return "pelo site"
    return reserva.get("criado_por_login") or "—"


def grupo(reserva: dict, hoje: dt.date, urgentes: set, devolucoes: set) -> str:
    if reserva["id"] in urgentes or reserva["id"] in devolucoes:
        return ACAO
    if (
        reserva["situacao"] in ("pendente", "confirmada")
        and reserva["data_saida"] > hoje
    ):
        return PROXIMAS
    return HISTORICO


# ---------------------------------------------------------------------------
# O Painel
# ---------------------------------------------------------------------------

def _titular(hospede_id, usuario_id) -> str:
    try:
        cadastro = hospede_repo.buscar_por_id(hospede_id, usuario_id=usuario_id)
    except cripto.FalhaDeAutenticacao:
        _log.warning("Cadastro ilegível do hóspede %s no painel", hospede_id)
        return "cadastro ilegível"
    return cadastro["nome"] if cadastro else "—"


def _botao(reserva_id, urgentes, pendentes, devolucoes):
    if reserva_id in devolucoes:
        return {"rotulo": "Registrar devolução", "ancora": f"devolucao-{reserva_id}"}
    if reserva_id in urgentes or reserva_id in pendentes:
        return {"rotulo": "Conferir pagamento", "ancora": f"pagamento-{reserva_id}"}
    return None


def montar(usuario_id, situacao=None, pagina=1, hoje: dt.date = None) -> dict:
    """Os três grupos de cards, com o histórico paginado.

    O nome do titular só é decifrado para os cards que vão à tela — o
    histórico de outras páginas não é lido.
    """
    hoje = hoje or dt.date.today()

    # Os cards de pagamento já varrem os pedidos vencidos antes de ler
    # (a lição de ordem do Bloco 4) e dizem quais reservas precisam de ação.
    cards_pag = pagamentos.cards(usuario_id=usuario_id)
    urgentes = {
        c["reserva"]["id"] for c in cards_pag
        if c["estado"]["urgencia"] < pagamentos.URGENCIA_AGUARDANDO
    }
    pendentes = {c["reserva"]["id"] for c in cards_pag}
    devolucoes = {
        d["reserva"]["id"] for d in pagamentos.devolucoes(usuario_id=usuario_id)
    }

    reservas = reserva_repo.listar(
        situacao=situacao, limite=1000, usuario_id=usuario_id
    )
    lancamentos = pagamento_repo.lancamentos_de_todas(usuario_id=usuario_id)

    grupos = {ACAO: [], PROXIMAS: [], HISTORICO: []}
    for reserva in reservas:
        grupos[grupo(reserva, hoje, urgentes, devolucoes)].append(reserva)

    grupos[ACAO].sort(key=lambda r: (r["data_entrada"], r["id"]))
    grupos[PROXIMAS].sort(key=lambda r: (r["data_entrada"], r["id"]))
    grupos[HISTORICO].sort(key=lambda r: (r["data_entrada"], r["id"]),
                           reverse=True)

    paginas = max(1, math.ceil(len(grupos[HISTORICO]) / POR_PAGINA))
    try:
        pagina = int(pagina)
    except (TypeError, ValueError):
        pagina = 1
    pagina = min(max(1, pagina), paginas)
    inicio = (pagina - 1) * POR_PAGINA
    grupos[HISTORICO] = grupos[HISTORICO][inicio:inicio + POR_PAGINA]

    def card(reserva):
        lanc = lancamentos.get(reserva["id"], {})
        return {
            "reserva": reserva,
            "titular": _titular(reserva["hospede_id"], usuario_id),
            "pagamento": selo_pagamento(reserva, lanc),
            "estadia": selo_estadia(reserva, hoje),
            "valor_pago": sum(lanc.values()) if lanc else None,
            "autor": autor(reserva),
            "cor": cor_da_foto(reserva["chale_id"], 1),
            "botao": _botao(reserva["id"], urgentes, pendentes, devolucoes),
        }

    return {
        "acao": [card(r) for r in grupos[ACAO]],
        "proximas": [card(r) for r in grupos[PROXIMAS]],
        "historico": [card(r) for r in grupos[HISTORICO]],
        "pagina": pagina,
        "paginas": paginas,
        "situacao": situacao,
    }


__all__ = [
    "ACAO", "HISTORICO", "POR_PAGINA", "PROXIMAS",
    "autor", "grupo", "montar", "selo_estadia", "selo_pagamento",
]
