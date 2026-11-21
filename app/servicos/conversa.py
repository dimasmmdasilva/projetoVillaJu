"""A conversa da reserva — Etapa 5, Bloco 17.

O hóspede logado fala com a pousada sobre uma reserva dele; a equipe
responde no detalhe da reserva. A gravação e a cifra estão em
`app/dados/mensagem_repo.py`; aqui ficam quem pode, o log e a tela.

**O log diz que houve mensagem, nunca o que ela diz.** "Mensagem enviada
na reserva N", com o autor da linha — o texto é dado pessoal e só existe
cifrado.

**Para o hóspede, quem responde é a pousada.** O login de quem atendeu
não vai à tela dele; a equipe vê quem foi.
"""

import logging

from app.dados import auditoria_repo, mensagem_repo, reserva_repo
from app.dados.mensagem_repo import LIMITE_DO_TEXTO, ErroDeMensagem  # noqa: F401

_log = logging.getLogger(__name__)

PELA_POUSADA = "Pousada VillaJu"


def _registrar(usuario, reserva_id, quem):
    auditoria_repo.registrar(
        auditoria_repo.MENSAGEM_ENVIADA,
        usuario_id=usuario["id"],
        tabela="mensagem",
        registro_id=reserva_id,
        detalhe=f"mensagem enviada na reserva {reserva_id} {quem}",
    )


def reserva_do_hospede(usuario, reserva_id):
    """A reserva, se for do hóspede conectado; `None` em qualquer outro
    caso — de outra pessoa ou inexistente, sem distinguir."""
    reserva = reserva_repo.buscar_por_id(reserva_id, usuario_id=usuario["id"])
    if reserva is None or reserva["hospede_id"] != usuario.get("hospede_id"):
        return None
    return reserva


def tela_do_hospede(usuario, reserva_id):
    reserva = reserva_do_hospede(usuario, reserva_id)
    if reserva is None:
        return None
    mensagens = mensagem_repo.conversa_do_hospede(
        reserva_id, usuario["hospede_id"], usuario_id=usuario["id"]
    )
    for mensagem in mensagens:
        mensagem["quem"] = PELA_POUSADA if mensagem["da_equipe"] else "Você"
    return {"reserva": reserva, "mensagens": mensagens,
            "limite": LIMITE_DO_TEXTO}


def enviar_do_hospede(usuario, reserva_id, texto) -> bool:
    valeu = mensagem_repo.enviar_do_hospede(
        reserva_id, usuario.get("hospede_id"), usuario["id"], texto
    )
    if valeu:
        _registrar(usuario, reserva_id, "pelo hóspede")
    return valeu


def conversa_da_equipe(usuario, reserva_id) -> list:
    mensagens = mensagem_repo.conversa(reserva_id, usuario_id=usuario["id"])
    for mensagem in mensagens:
        mensagem["quem"] = (
            mensagem["autor_login"] if mensagem["da_equipe"] else "Hóspede"
        )
        mensagem["nao_lida"] = (
            not mensagem["da_equipe"] and not mensagem["lida_pela_equipe"]
        )
    if any(not m["legivel"] for m in mensagens):
        # O número da reserva, e nada do conteúdo.
        _log.warning("Mensagem ilegível na conversa da reserva %s", reserva_id)
    return mensagens


def enviar_da_equipe(usuario, reserva_id, texto) -> bool:
    valeu = mensagem_repo.enviar_da_equipe(reserva_id, usuario["id"], texto)
    if valeu:
        _registrar(usuario, reserva_id, "pela equipe")
    return valeu


def marcar_como_lidas(usuario, reserva_id) -> int:
    return mensagem_repo.marcar_como_lidas(reserva_id, usuario_id=usuario["id"])
