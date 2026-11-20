"""A reserva registrada pela recepção — Etapa 5, Bloco 16b.

Quem liga ou manda mensagem para a pousada entra no mesmo controle de quem
pede pelo site: a reserva nasce `pendente`, com a modalidade decidida pela
antecedência (sinal de 50% com 7 dias ou mais, integral com menos) e um
prazo para o Pix. O prazo é maior — `HORAS_PARA_O_PIX_PELA_RECEPCAO` —,
porque quem pediu não está diante da tela e o código do Pix ainda precisa
chegar a ele.

Antes do Bloco 16b a recepção criava a reserva pendente e a passava a
confirmada por um botão, sem nenhum Pix conferido. O botão saiu, e a
transição saiu do repositório: quem confirma é a conferência do extrato.

**O aceite é da recepção.** O hóspede não clicou em nada; quem marca a
caixa declara que leu as regras a ele. O log grava isso e o autor — nunca
o nome de quem vai se hospedar.

**O limite de um pedido em aberto vale igual.** Um hóspede com um pedido
aguardando Pix não ganha um segundo pela recepção.

A gravação é a mesma de `pedido.pedir`: `reserva_repo.criar_pedido`, que
varre os vencidos, trava o hóspede, confere o limite e as regras e grava,
nessa ordem e na mesma transação.
"""

import datetime as dt

from app.dados import auditoria_repo, reserva_repo
from app.dados.calculo import TarifaAusente
from app.dados.calculo import valor_total as calcular_valor_total
from app.servicos import regulamento

MINUTOS_PARA_O_PIX = regulamento.HORAS_PARA_O_PIX_PELA_RECEPCAO * 60

ACEITE_DA_RECEPCAO = (
    "Li ao hóspede as regras do regulamento (pagamento, prazos, "
    "cancelamento e devolução)."
)


class ErroDeRegistro(reserva_repo.ErroDeReserva):
    """O registro foi recusado; a mensagem é para a equipe."""


def registrar(usuario: dict, dados: dict, hoje: dt.date = None) -> int:
    """Grava a reserva da recepção. Devolve o número dela.

    `dados` vem do formulário: chalé, hóspede, datas, pessoas, observações
    e a caixa do aceite. `usuario` é quem está registrando, e é o autor.
    """
    hoje = hoje or dt.date.today()

    if not dados.get("aceite"):
        raise ErroDeRegistro(
            "Marque que leu as regras do regulamento ao hóspede. Sem isso "
            "a reserva não é registrada."
        )

    entrada, saida = reserva_repo.validar_periodo(
        dados.get("data_entrada"), dados.get("data_saida")
    )
    # O controle do Pix é para reservas que ainda vão acontecer. Estadia
    # passada não tem prazo de pagamento a correr.
    if entrada < hoje:
        raise ErroDeRegistro("A data de entrada já passou.")

    try:
        chale_id = int(dados.get("chale_id"))
    except (TypeError, ValueError):
        raise ErroDeRegistro("Selecione o chalé e o hóspede.") from None

    try:
        total = calcular_valor_total(chale_id, entrada, saida)
    except TarifaAusente:
        # Sem valor não há Pix a pedir. Antes a reserva gravava com valor
        # nulo; no controle do Pix, ela não teria o que conferir.
        raise ErroDeRegistro(
            "Não há tarifa cadastrada para todo esse período. Peça ao "
            "administrador para cadastrar antes de registrar."
        ) from None

    modalidade = regulamento.modalidade_para(entrada, hoje)
    prazo_restante = (
        regulamento.prazo_do_restante(entrada)
        if modalidade == regulamento.SINAL else None
    )

    try:
        identificador = reserva_repo.criar_pedido(
            {
                "chale_id": chale_id,
                "hospede_id": dados.get("hospede_id"),
                "data_entrada": entrada,
                "data_saida": saida,
                "num_pessoas": dados.get("num_pessoas"),
                "observacoes": dados.get("observacoes"),
                "valor_total": total,
            },
            modalidade=modalidade,
            minutos_para_o_pix=MINUTOS_PARA_O_PIX,
            prazo_restante=prazo_restante,
            limite_em_aberto=regulamento.LIMITE_DE_PEDIDOS_EM_ABERTO,
            usuario_id=usuario["id"],
        )
    except reserva_repo.PedidoEmAberto as erro:
        raise ErroDeRegistro(
            "Este hóspede já tem um pedido aguardando o Pix ou a "
            "conferência. Resolva aquele pedido em Pagamentos antes de "
            "registrar outro."
        ) from erro
    # As demais recusas (conflito de datas, capacidade, hóspede
    # inexistente) já vêm escritas para a equipe e sobem como estão.

    auditoria_repo.registrar(
        auditoria_repo.PEDIDO_DE_RESERVA,
        usuario_id=usuario["id"],
        tabela="reserva",
        registro_id=identificador,
        # Quem registrou é o `usuario_id` da linha; o hóspede, nunca.
        detalhe=(
            f"Registrada pela recepção, pagamento {modalidade}, Pix em "
            f"{regulamento.HORAS_PARA_O_PIX_PELA_RECEPCAO} h; regras lidas "
            f"ao hóspede pela recepção"
        ),
    )
    return identificador
