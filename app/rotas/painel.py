"""Tela inicial da área autenticada.

Mostra quem está conectado, qual o perfil, e um resumo do estado do
sistema. As telas de cadastro e de reservas entram nos blocos seguintes.

A verificação de sessão aqui é feita à mão. No Bloco 5 ela é substituída
pelos decoradores `@requer_login` e `@requer_perfil`, que centralizam a
regra em vez de repeti-la em cada rota — repetição é onde se esquece.
"""

from flask import Blueprint, redirect, render_template, session, url_for

from app.dados import conexao
from app.rotas.auth import usuario_da_sessao

painel = Blueprint("painel", __name__)


def _resumo():
    """Contagens simples para a tela inicial.

    Nenhum dado pessoal: apenas quantidades. A tabela `hospede` é contada,
    não lida — seus campos são cifrados e só o repositório próprio os
    decifra.
    """
    consultas = {
        "chales": "SELECT COUNT(*) FROM chale WHERE ativo = TRUE",
        "hospedes": "SELECT COUNT(*) FROM hospede",
        "reservas_ativas": (
            "SELECT COUNT(*) FROM reserva "
            "WHERE situacao IN ('pendente','confirmada')"
        ),
        "contatos_novos": "SELECT COUNT(*) FROM contato WHERE situacao = 'novo'",
    }

    resumo = {}
    with conexao.obter_cursor(usuario_id=session.get("usuario_id")) as cursor:
        for chave, sql in consultas.items():
            cursor.execute(sql)
            resumo[chave] = cursor.fetchone()[0]
    return resumo


@painel.route("/painel")
def inicio():
    usuario = usuario_da_sessao()
    if usuario is None:
        return redirect(url_for("auth.login"))

    try:
        resumo = _resumo()
    except Exception:  # noqa: BLE001 - o painel não pode cair por causa do resumo
        resumo = None

    return render_template("painel/inicio.html", usuario=usuario, resumo=resumo)
