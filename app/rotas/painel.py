"""Área autenticada: tela inicial e consulta do log de auditoria.

As duas rotas aqui mostram a diferença entre os dois decoradores. O painel
exige apenas sessão válida, de qualquer perfil. A auditoria exige perfil
de administrador — é a rota que demonstra o 403 e o registro de
`ACESSO_NEGADO`.

As telas de chalés, hóspedes e reservas entram na Etapa 3.
"""

from flask import Blueprint, render_template, request

from app.dados import auditoria_repo, conexao
from app.seguranca.autorizacao import (
    ADMINISTRADOR,
    requer_login,
    requer_perfil,
    usuario_atual,
)

painel = Blueprint("painel", __name__)

# Teto de linhas na tela de auditoria. O repositório impõe o seu próprio
# limite; este existe para a página não ficar impossível de ler.
LINHAS_POR_PAGINA = 100


def _resumo(usuario_id):
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
    with conexao.obter_cursor(usuario_id=usuario_id) as cursor:
        for chave, sql in consultas.items():
            cursor.execute(sql)
            resumo[chave] = cursor.fetchone()[0]
    return resumo


@painel.route("/painel")
@requer_login
def inicio():
    usuario = usuario_atual()

    try:
        resumo = _resumo(usuario["id"])
    except Exception:  # noqa: BLE001 - o painel não pode cair por causa do resumo
        resumo = None

    return render_template("painel/inicio.html", usuario=usuario, resumo=resumo)


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
