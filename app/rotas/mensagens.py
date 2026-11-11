"""Caixa de saída: as mensagens que o sistema produziu em modo arquivo.

Existe por uma razão concreta: **a demonstração do projeto roda sem
rede.** Sem ela, o cadastro com confirmação por e-mail seria impossível de
mostrar — a mensagem sairia para um servidor inalcançável e o link nunca
chegaria a lugar nenhum.

Com ela, a sequência fica inteira: cadastra-se na tela pública, abre-se a
caixa de saída, clica-se no link, e a conta é ativada.

Não é improviso. MailHog, Mailtrap e o backend de arquivo do Django
existem pelo mesmo motivo e fazem a mesma coisa.

**Só aparece em modo arquivo, e só ao administrador.** Em modo `smtp` a
tela responde 404 — não porque o segredo esteja na existência da rota,
mas porque uma caixa de saída vazia num sistema que envia de verdade só
confundiria quem a abrisse.

**A restrição ao administrador importa mais do que parece.** As mensagens
carregam o link de confirmação, e o link de confirmação ativa uma conta.
Quem lê a caixa de saída consegue ativar a conta de outra pessoa — e, pior,
definir o momento. Por isso a tela não está aberta à recepção.
"""

from flask import Blueprint, abort, current_app, render_template

from app.seguranca.autorizacao import ADMINISTRADOR, requer_perfil, usuario_atual
from app.servicos import correio

mensagens = Blueprint("mensagens", __name__, url_prefix="/mensagens")


@mensagens.route("/")
@requer_perfil(ADMINISTRADOR)
def listar():
    """As mensagens gravadas, da mais recente para a mais antiga."""
    if current_app.config.get("EMAIL_MODO") != "arquivo":
        abort(404)

    class _Configuracao:
        EMAIL_PASTA = current_app.config["EMAIL_PASTA"]

    return render_template(
        "painel/mensagens.html",
        usuario=usuario_atual(),
        mensagens=correio.listar_enviadas(_Configuracao),
        pasta=current_app.config["EMAIL_PASTA"],
    )
