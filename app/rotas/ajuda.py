"""O roteiro de uso da equipe, dentro do sistema — Etapa 5, Bloco 18b.

Pedido do Dimas em 21/11: em vez de um documento solto, uma página "Como
usar" para administrador e recepção, organizada por tarefa. Texto fixo,
sem banco e sem JavaScript; os prazos e percentuais vêm do regulamento,
para que o texto não fique para trás quando uma regra mudar.
"""

from flask import Blueprint, render_template

from app.seguranca.autorizacao import ADMINISTRADOR, RECEPCIONISTA, requer_perfil
from app.servicos import regulamento

ajuda = Blueprint("ajuda", __name__)


@ajuda.route("/como-usar")
@requer_perfil(ADMINISTRADOR, RECEPCIONISTA)
def como_usar():
    return render_template("painel/como_usar.html", r=regulamento)
