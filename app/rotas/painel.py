"""Área autenticada: tela inicial e consulta do log de auditoria.

As duas rotas aqui mostram a diferença entre os dois decoradores. O painel
exige apenas sessão válida, de qualquer perfil. A auditoria exige perfil
de administrador — é a rota que demonstra o 403 e o registro de
`ACESSO_NEGADO`.

A tela inicial muda conforme o perfil: administrador e recepcionista veem
as contagens da hospedaria, o hóspede vê apenas o caminho para as próprias
reservas.
"""

from flask import Blueprint, render_template, request

from app.dados import auditoria_repo, reserva_repo
from app.servicos import conversa, estado_do_backup, painel_equipe
from app.seguranca.autorizacao import (
    ADMINISTRADOR,
    HOSPEDE,
    requer_login,
    requer_perfil,
    usuario_atual,
)

painel = Blueprint("painel", __name__)

# Teto de linhas na tela de auditoria. O repositório impõe o seu próprio
# limite; este existe para a página não ficar impossível de ler.
LINHAS_POR_PAGINA = 100


@painel.route("/painel")
@requer_login
def inicio():
    usuario = usuario_atual()

    # O que não é do hóspede não é lido para ele — esconder no template
    # bastaria para a tela, mas o dado teria sido lido, e o que não se lê
    # não vaza.
    #
    # 20/11: as contagens (hóspedes, reservas ativas, contatos novos) e os
    # atalhos saíram, a pedido do Dimas. No lugar, um card por chalé. A
    # equipe vê a ocupação de hoje; o hóspede, não — é dado de outras
    # pessoas. O hóspede também não recebe as reservas da pousada.
    respostas = []
    backup = None
    if usuario["perfil"] == ADMINISTRADOR:
        # Bloco 18c: quando foi o último backup. Backup que falha em silêncio
        # dá falsa segurança; o Painel avisa se passar de dois dias.
        backup = estado_do_backup.situacao()
    if usuario["perfil"] == HOSPEDE:
        cards = None
        chales = painel_equipe.chales_para_o_hospede(usuario["id"])
        # Bloco 17b: o aviso de "nova resposta da pousada".
        respostas = conversa.respostas_novas(usuario)
    else:
        chales = painel_equipe.chales_para_a_equipe(usuario["id"])

        # Bloco 16: as reservas em cards, em três grupos, com o filtro por
        # situação e o histórico paginado — tudo na URL, sem JavaScript.
        situacao = (request.args.get("situacao") or "").strip() or None
        if situacao not in reserva_repo.SITUACOES:
            situacao = None
        cards = painel_equipe.montar(
            usuario["id"], situacao=situacao,
            pagina=request.args.get("pagina", 1),
        )

    return render_template(
        "painel/inicio.html",
        usuario=usuario,
        chales=chales,
        cards=cards,
        respostas=respostas,
        backup=backup,
        situacoes=reserva_repo.SITUACOES,
    )


@painel.route("/auditoria")
@requer_perfil(ADMINISTRADOR)
def auditoria():
    """Log de auditoria. Restrito ao administrador.

    É a rota que o plano usa para demonstrar o controle de acesso: um
    recepcionista que digite este endereço recebe 403, e a tentativa fica
    registrada no próprio log que ele tentou ver.

    A aplicação tem SELECT e INSERT em `log_auditoria`, e não tem UPDATE
    nem DELETE. Esta tela lê; nada aqui consegue apagar ou reescrever o
    que já foi gravado.
    """
    usuario = usuario_atual()

    limite = request.args.get("limite", default=LINHAS_POR_PAGINA, type=int)
    limite = max(1, min(limite or LINHAS_POR_PAGINA, 500))

    eventos = auditoria_repo.listar(limite=limite, usuario_id=usuario["id"])
    resumo = auditoria_repo.contar_por_acao(usuario_id=usuario["id"])

    return render_template(
        "painel/auditoria.html",
        usuario=usuario,
        eventos=eventos,
        resumo=resumo,
        limite=limite,
    )
