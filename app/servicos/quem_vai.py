"""A lista de quem vai, editada pelo administrador — Etapa 5, Bloco 15b.

Decidido pelo Dimas em 13/11:

- **só o administrador** edita: acrescentar, remover e corrigir nome,
  documento e "menor de idade";
- **os dados do titular não se editam aqui.** Eles são o cadastro da conta
  da pessoa, e a regra do caminho único (Bloco 4) os deixa na tela de
  hóspedes. Mudá-los por uma reserva mudaria a conta inteira;
- a recepção vê a lista completa nos cards, mas não edita.

As regras de cada acompanhante são as do pedido (`pedido.validar_acompanhante`):
nome, documento de 5 a 20 letras ou números, documento obrigatório para
adulto, sem repetição na lista e diferente do titular.

**O log diz quem mudou qual campo, nunca os valores.**
"""

import logging

from app.dados import acompanhante_repo, auditoria_repo, reserva_repo
from app.dados.erros import ErroDeDados
from app.seguranca import cripto
from app.servicos import pagamentos, pedido

_log = logging.getLogger(__name__)

SITUACOES_EDITAVEIS = ("pendente", "confirmada")
_CAMPOS = ("nome", "documento", "menor_de_idade")
_ROTULOS = {"nome": "nome", "documento": "documento",
            "menor_de_idade": "menor de idade"}


class ErroDeLista(Exception):
    """A alteração não pode ser feita. A mensagem vai para a tela."""


def tela(reserva_id, usuario_id) -> dict:
    """A reserva, o titular e a lista, para a tela de edição."""
    reserva = reserva_repo.buscar_por_id(reserva_id, usuario_id=usuario_id)
    if reserva is None:
        return None
    try:
        lista = acompanhante_repo.listar_da_reserva(
            reserva_id, usuario_id=usuario_id
        )
        legivel = True
    except cripto.FalhaDeAutenticacao:
        _log.warning("Acompanhante ilegível na reserva %s", reserva_id)
        lista, legivel = [], False
    return {
        "reserva": reserva,
        "titular": pagamentos.titular_completo(reserva["hospede_id"], usuario_id),
        "acompanhantes": lista,
        "legivel": legivel,
        "editavel": legivel and reserva["situacao"] in SITUACOES_EDITAVEIS,
    }


def _documentos_dos_outros(reserva_id, usuario_id, exceto_ordem=None) -> set:
    return {
        pessoa["documento"]
        for pessoa in acompanhante_repo.listar_da_reserva(
            reserva_id, usuario_id=usuario_id
        )
        if pessoa["documento"] and pessoa["ordem"] != exceto_ordem
    }


def _validar(dados, numero, reserva_id, hospede_id, usuario_id, exceto=None):
    try:
        return pedido.validar_acompanhante(
            dados.get("nome"),
            dados.get("documento"),
            bool(dados.get("menor")),
            numero,
            vistos=_documentos_dos_outros(reserva_id, usuario_id, exceto),
            hospede_id=hospede_id,
            mensagem_do_titular=(
                f"Acompanhante {numero}: este é o documento do titular da "
                f"reserva."
            ),
        )
    except pedido.ErroDePedido as erro:
        raise ErroDeLista(str(erro)) from None


def _registrar(usuario, reserva_id, detalhe):
    auditoria_repo.registrar(
        auditoria_repo.LISTA_DE_HOSPEDES_ALTERADA,
        usuario_id=usuario["id"],
        tabela="acompanhante",
        registro_id=reserva_id,
        detalhe=detalhe,
    )


def _reserva(reserva_id, usuario_id):
    reserva = reserva_repo.buscar_por_id(reserva_id, usuario_id=usuario_id)
    if reserva is None or reserva["situacao"] not in SITUACOES_EDITAVEIS:
        raise ErroDeLista("Esta reserva não aceita mais alterações.")
    return reserva


def _no_banco(operacao):
    """Traduz a recusa do banco — capacidade, CHECK, reserva encerrada."""
    try:
        return operacao()
    except acompanhante_repo.ListaNaoEditavel as erro:
        raise ErroDeLista(str(erro)) from None
    except ErroDeDados as erro:
        raise ErroDeLista(str(erro)) from None


def acrescentar(reserva_id, dados, usuario) -> int:
    reserva = _reserva(reserva_id, usuario["id"])
    pessoa = _validar(dados, "novo", reserva_id, reserva["hospede_id"],
                      usuario["id"])
    ordem = _no_banco(lambda: acompanhante_repo.acrescentar(
        reserva_id, pessoa, usuario_id=usuario["id"]
    ))
    _registrar(usuario, reserva_id,
               f"Administrador acrescentou o acompanhante {ordem}")
    return ordem


def remover(reserva_id, ordem, usuario) -> bool:
    _reserva(reserva_id, usuario["id"])
    valeu = _no_banco(lambda: acompanhante_repo.remover(
        reserva_id, ordem, usuario_id=usuario["id"]
    ))
    if valeu:
        _registrar(usuario, reserva_id,
                   f"Administrador removeu o acompanhante {ordem}")
    return valeu


def corrigir(reserva_id, ordem, dados, usuario) -> bool:
    reserva = _reserva(reserva_id, usuario["id"])
    atuais = {
        p["ordem"]: p for p in acompanhante_repo.listar_da_reserva(
            reserva_id, usuario_id=usuario["id"]
        )
    }
    if ordem not in atuais:
        return False

    pessoa = _validar(dados, ordem, reserva_id, reserva["hospede_id"],
                      usuario["id"], exceto=ordem)
    mudou = [c for c in _CAMPOS if pessoa[c] != atuais[ordem][c]]
    if not mudou:
        return True

    valeu = _no_banco(lambda: acompanhante_repo.corrigir(
        reserva_id, ordem, pessoa, usuario_id=usuario["id"]
    ))
    if valeu:
        # Quais campos, nunca os valores.
        _registrar(usuario, reserva_id,
                   f"Administrador corrigiu o acompanhante {ordem}: "
                   + ", ".join(_ROTULOS[c] for c in mudou))
    return valeu


__all__ = ["ErroDeLista", "acrescentar", "corrigir", "remover", "tela"]
