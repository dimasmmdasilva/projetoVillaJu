"""Os relatórios da equipe — Etapa 5, Bloco 18a.

Administrador e recepção. O período vai na URL (`?de=...&ate=...`), sem
JavaScript, como o Controle de pagamentos: o relatório de um mês pode ser
salvo nos favoritos e aberto de novo.
"""

from flask import Blueprint, render_template, request

from app.seguranca.autorizacao import (
    ADMINISTRADOR,
    RECEPCIONISTA,
    requer_perfil,
    usuario_atual,
)
from app.servicos import relatorios as servico

relatorios = Blueprint("relatorios", __name__, url_prefix="/relatorios")


@relatorios.route("")
@requer_perfil(ADMINISTRADOR, RECEPCIONISTA)
def ver():
    de = (request.args.get("de") or "").strip() or None
    ate = (request.args.get("ate") or "").strip() or None
    try:
        dados = servico.montar(de, ate, usuario_id=usuario_atual()["id"])
    except servico.ErroDeRelatorio as erro:
        return render_template(
            "painel/relatorios.html", relatorio=None, erro=str(erro),
            de=de or "", ate=ate or "",
        ), 400
    return render_template(
        "painel/relatorios.html", relatorio=dados, erro=None,
        de=dados["de"].isoformat(), ate=dados["ate"].isoformat(),
    )
