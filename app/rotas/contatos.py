"""Atendimento aos pedidos de contato, e a conversão em hóspede.

Esta é a outra ponta do formulário público. O que entra por lá é uma
**alegação** feita por um desconhecido; o que sai daqui, quando a recepção
confirma, é um **registro verificado**. Entre os dois há um telefonema, e
é esse telefonema que a tela existe para apoiar.

**A conversão não copia à mão.** Ao confirmar o pedido, a recepção clica
em "Converter em hóspede" e o formulário de cadastro abre já preenchido
com nome, e-mail e telefone que a pessoa informou. Ela acrescenta apenas o
documento — justamente o campo que não se pede a um estranho pela
internet, e que por isso o formulário público não tem.

**O contato permanece.** Convertido, ele não é apagado nem movido: fica
como registro de origem, com `hospede_id` apontando para o cadastro que
originou. É o que permite responder de onde vieram os hóspedes de um
período, e quantos pedidos viraram estadia.

**Quando a pessoa já é hóspede.** A mesma pessoa pode perguntar em
janeiro, desistir, e voltar em julho. A tela procura pelo e-mail antes de
oferecer o cadastro: achando, liga ao registro existente em vez de criar
um segundo. Duplicar o cadastro espalharia o histórico de uma pessoa por
duas linhas.

**Acesso.** Administrador e recepcionista. O perfil `hospede` não tem o
que fazer aqui: os pedidos são de terceiros.
"""

import logging

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for

from app.dados import contato_repo, hospede_repo
from app.seguranca.autorizacao import (
    ADMINISTRADOR,
    RECEPCIONISTA,
    requer_perfil,
    usuario_atual,
)
from app.seguranca.cripto import FalhaDeAutenticacao

contatos = Blueprint("contatos", __name__, url_prefix="/contatos")

_registrador = logging.getLogger(__name__)


@contatos.route("/")
@requer_perfil(ADMINISTRADOR, RECEPCIONISTA)
def listar():
    """Lista de atendimento, da mais recente para a mais antiga.

    O filtro por situação é o que a recepção usa no dia a dia: abre em
    `novo` para ver o que chegou, e passa a `em_atendimento` para
    retomar o que ficou pela metade.
    """
    usuario = usuario_atual()
    situacao = (request.args.get("situacao") or "").strip() or None

    if situacao and situacao not in contato_repo.SITUACOES:
        situacao = None

    return render_template(
        "painel/contatos.html",
        usuario=usuario,
        contatos=contato_repo.listar(situacao=situacao, usuario_id=usuario["id"]),
        situacao=situacao,
        situacoes=contato_repo.SITUACOES,
        resumo=contato_repo.contar_por_situacao(usuario_id=usuario["id"]),
    )


@contatos.route("/<int:identificador>")
@requer_perfil(ADMINISTRADOR, RECEPCIONISTA)
def detalhe(identificador):
    """O pedido inteiro, com a mensagem e o histórico da pessoa."""
    usuario = usuario_atual()

    try:
        contato = contato_repo.buscar_por_id(identificador, usuario_id=usuario["id"])
    except FalhaDeAutenticacao:
        flash(
            "Este pedido não passou na verificação de integridade e não "
            "pode ser aberto.",
            "erro",
        )
        return redirect(url_for("contatos.listar"))

    if contato is None:
        abort(404)

    # Quantas outras vezes esta pessoa já escreveu. Serve ao atendimento,
    # que é diferente para quem pergunta pela primeira vez, e serve como
    # sinal de abuso quando o número é alto.
    anteriores = contato_repo.contatos_anteriores(
        identificador, contato["email"], usuario_id=usuario["id"]
    )

    return render_template(
        "painel/contato_detalhe.html",
        usuario=usuario,
        contato=contato,
        anteriores=anteriores,
        transicoes=contato_repo.TRANSICOES[contato["situacao"]],
        hospede_existente=_hospede_com_o_mesmo_email(contato, usuario["id"]),
    )


@contatos.route("/<int:identificador>/situacao", methods=["POST"])
@requer_perfil(ADMINISTRADOR, RECEPCIONISTA)
def mudar_situacao(identificador):
    """Move o pedido no ciclo de vida.

    Por POST, com token CSRF, e em rota própria — não é campo de um
    formulário de edição. Misturar as duas coisas permitiria mover um
    pedido descartado de volta por um campo oculto.
    """
    usuario = usuario_atual()
    destino = (request.form.get("situacao") or "").strip()

    try:
        contato_repo.mudar_situacao(identificador, destino, usuario_id=usuario["id"])
    except contato_repo.ErroDeContato as erro:
        flash(str(erro), "erro")
        return redirect(url_for("contatos.detalhe", identificador=identificador))

    flash(f"Pedido marcado como {destino}.", "sucesso")
    return redirect(url_for("contatos.detalhe", identificador=identificador))


# ---------------------------------------------------------------------------
# A rota de conversao saiu no Bloco 4 da Etapa 5
#
# Ela abria o formulario de hospede preenchido com o que o visitante
# informou, e pedia a equipe que **completasse o documento** obtido por
# telefone. Duas coisas erradas numa so tela:
#
# A equipe digitava o documento de outra pessoa, a partir de um pedido que
# deliberadamente nao pede documento. E o mesmo registro de hospede passava
# a ter duas origens possiveis — o autocadastro e a recepcao —, o que
# significa dois fluxos para testar, dois caminhos para auditar e duas
# respostas para a pergunta "de onde veio este cadastro?".
#
# Decidido em 12/11/2026: **o autocadastro publico e o unico caminho.** A
# pessoa informa os proprios dados, e so ela.
#
# O que sobrevive e `ligar`, logo abaixo, e a diferenca entre as duas e o
# ponto do bloco: **ligar associa, nao cria.** Quando a pessoa se cadastra
# sozinha, a recepcao liga o pedido ao cadastro que ela fez, e o relatorio
# de origem dos contatos continua possivel sem que ninguem da equipe tenha
# digitado o documento de ninguem.
# ---------------------------------------------------------------------------

@contatos.route("/<int:identificador>/ligar", methods=["POST"])
@requer_perfil(ADMINISTRADOR, RECEPCIONISTA)
def ligar(identificador):
    """Liga o pedido a um cadastro de hóspede que já existe.

    A mesma pessoa pode escrever várias vezes ao longo de um ano. Criar um
    segundo cadastro espalharia o histórico dela por duas linhas, e faria
    a busca por documento devolver uma das duas conforme a sorte.
    """
    usuario = usuario_atual()
    hospede_id = request.form.get("hospede_id", type=int)

    if not hospede_id or hospede_repo.buscar_por_id(
        hospede_id, usuario_id=usuario["id"]
    ) is None:
        flash("O hóspede informado não existe.", "erro")
        return redirect(url_for("contatos.detalhe", identificador=identificador))

    contato_repo.ligar_ao_hospede(identificador, hospede_id, usuario_id=usuario["id"])

    flash(f"Pedido ligado ao hóspede {hospede_id}.", "sucesso")
    return redirect(url_for("contatos.detalhe", identificador=identificador))


def _hospede_com_o_mesmo_email(contato: dict, usuario_id):
    """Procura cadastro existente pelo e-mail do pedido.

    Usa o índice HMAC de `hospede`, não o de `contato`: o que se quer
    saber é se esta pessoa já é hóspede.

    Devolve None diante de qualquer falha. É sugestão de atendimento, não
    regra — e uma sugestão que derrubasse a tela seria pior do que
    sugestão nenhuma.
    """
    if not contato.get("email"):
        return None

    try:
        return hospede_repo.buscar_por_email(contato["email"], usuario_id=usuario_id)
    except Exception:  # noqa: BLE001
        _registrador.warning("falha ao procurar hóspede pelo e-mail do contato")
        return None
