"""Consulta de disponibilidade e gestão de reservas.

Acessível a administrador e recepcionista. O perfil `hospede` consulta
apenas as próprias reservas, em rota separada.
"""

import datetime as dt
import logging

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from app.dados import (
    acompanhante_repo,
    auditoria_repo,
    chale_repo,
    pagamento_repo,
    reserva_repo,
    usuario_repo,
)
from app.dados.erros import ErroDeDados
from app.dados.reserva_repo import ConflitoDePeriodo
from app.seguranca import senhas
from app.seguranca.autorizacao import (
    ADMINISTRADOR,
    HOSPEDE,
    RECEPCIONISTA,
    requer_perfil,
    usuario_atual,
)
from app.extensoes import limiter
from app.seguranca.cripto import FalhaDeAutenticacao
from app.servicos import (
    conversa,
    painel_equipe,
    pagamentos,
    pedido,
    registro_pela_recepcao,
    regulamento,
)

# A exigência de tamanho da senha vive em `app/servicos/cadastro.py`, que
# é onde a política foi escrita. Importada e não repetida: uma política
# de senha em dois lugares é uma política que vai divergir, e o cadastro
# aceitaria o que a troca recusa.
from app.servicos.cadastro import TAMANHO_MINIMO_DA_SENHA

_registrador = logging.getLogger(__name__)

reservas = Blueprint("reservas", __name__, url_prefix="/reservas")


def _quem():
    return usuario_atual()["id"]


def _codigo(erro) -> int:
    """409 para falta de disponibilidade, 400 para dado inválido.

    A reserva conflitante não está mal escrita: está impedida pelo estado
    atual do calendário. Responder 400 a ela diria ao cliente que o pedido
    estava malformado, e repetir o mesmo pedido depois de um cancelamento
    funcionaria — o que não é comportamento de 400.
    """
    return 409 if isinstance(erro, ConflitoDePeriodo) else 400


@reservas.route("/disponibilidade")
@requer_perfil(ADMINISTRADOR, RECEPCIONISTA)
def disponibilidade():
    """Dado um período, quais chalés estão livres e quais estão ocupados.

    Sem período informado, a tela abre com o formulário vazio em vez de
    assumir uma data. Adivinhar o que a recepção quer consultar produziria
    uma resposta que parece informação e não é.
    """
    entrada = (request.args.get("entrada") or "").strip()
    saida = (request.args.get("saida") or "").strip()

    contexto = {
        "usuario": usuario_atual(),
        "entrada": entrada,
        "saida": saida,
        "livres": None,
        "ocupados": None,
        "diarias": None,
        "erro": None,
    }

    if not entrada and not saida:
        return render_template("painel/disponibilidade.html", **contexto)

    try:
        inicio, fim = reserva_repo.validar_periodo(entrada, saida)
        contexto["livres"] = reserva_repo.chales_disponiveis(
            inicio, fim, usuario_id=_quem()
        )
        contexto["ocupados"] = reserva_repo.chales_ocupados(
            inicio, fim, usuario_id=_quem()
        )
        contexto["diarias"] = (fim - inicio).days
    except ErroDeDados as erro:
        contexto["erro"] = str(erro)
        return render_template("painel/disponibilidade.html", **contexto), 400

    return render_template("painel/disponibilidade.html", **contexto)


# ---------------------------------------------------------------------------
# Gestão de reservas
# ---------------------------------------------------------------------------

def _hospedes_para_selecao():
    """Lista de hóspedes para o campo de seleção do formulário.

    Os nomes chegam decifrados pelo repositório. Um registro ilegível
    aparece com o identificador no lugar do nome, em vez de derrubar a
    tela — a recepção precisa poder trabalhar mesmo com um registro
    corrompido na base.
    """
    from app.dados import hospede_repo

    lista = []
    for hospede in hospede_repo.listar(usuario_id=_quem()):
        rotulo = (
            hospede["nome"]
            if hospede.get("legivel", True) and hospede.get("nome")
            else f"(registro {hospede['id']} não legível)"
        )
        lista.append({"id": hospede["id"], "rotulo": rotulo})
    return lista


@reservas.route("/")
@requer_perfil(ADMINISTRADOR, RECEPCIONISTA)
def listar():
    """Bloco 16: a lista de reservas virou o Painel, em cards.

    O endereço continua respondendo — links salvos e favoritos levam ao
    mesmo lugar, com o filtro de situação preservado.
    """
    situacao = (request.args.get("situacao") or "").strip() or None
    if situacao not in reserva_repo.SITUACOES:
        situacao = None
    return redirect(url_for("painel.inicio", situacao=situacao))


@reservas.route("/nova", methods=["GET", "POST"])
@requer_perfil(ADMINISTRADOR, RECEPCIONISTA)
def nova():
    from app.dados import chale_repo

    contexto = {
        "usuario": usuario_atual(),
        "chales": chale_repo.listar(apenas_ativos=True, usuario_id=_quem()),
        "hospedes": _hospedes_para_selecao(),
        "novo": True,
        # O texto do aviso sai das constantes do regulamento, como a página
        # pública: mudar o prazo é mudar um número só.
        "horas_para_o_pix": regulamento.HORAS_PARA_O_PIX_PELA_RECEPCAO,
        "percentual_do_sinal": regulamento.PERCENTUAL_DO_SINAL,
        "antecedencia_para_sinal": regulamento.ANTECEDENCIA_PARA_SINAL_DIAS,
        "aceite_da_recepcao": registro_pela_recepcao.ACEITE_DA_RECEPCAO,
    }

    if request.method == "GET":
        # A tela de disponibilidade encaminha para cá com chalé e período
        # já escolhidos, para não obrigar a redigitar o que foi consultado.
        contexto["reserva"] = {
            "chale_id": request.args.get("chale_id", type=int),
            "data_entrada": request.args.get("entrada", ""),
            "data_saida": request.args.get("saida", ""),
            "num_pessoas": request.args.get("pessoas", type=int),
        }
        return render_template("painel/reserva_form.html", **contexto)

    # Bloco 16b: a reserva da recepção entra no controle do Pix, pelo mesmo
    # `criar_pedido` do site. A caixa do aceite é da recepção — ela declara
    # que leu as regras ao hóspede.
    dados = _dados_do_formulario()
    dados["aceite"] = request.form.get("aceite") == "sim"

    try:
        identificador = registro_pela_recepcao.registrar(usuario_atual(), dados)
    except ErroDeDados as erro:
        flash(str(erro), "erro")
        contexto["reserva"] = dados
        return render_template("painel/reserva_form.html", **contexto), _codigo(erro)

    flash(
        f"Reserva {identificador} registrada. O hóspede tem "
        f"{regulamento.HORAS_PARA_O_PIX_PELA_RECEPCAO} horas para o Pix; "
        f"a conferência é em Pagamentos.",
        "sucesso",
    )
    return redirect(url_for("reservas.detalhe", identificador=identificador))


@reservas.route("/<int:identificador>")
@requer_perfil(ADMINISTRADOR, RECEPCIONISTA)
def detalhe(identificador):
    reserva = reserva_repo.buscar_por_id(identificador, usuario_id=_quem())
    if reserva is None:
        abort(404)

    return render_template(
        "painel/reserva_detalhe.html",
        usuario=usuario_atual(),
        reserva=reserva,
        transicoes=reserva_repo.TRANSICOES[reserva["situacao"]],
        hospede=_hospede_da_reserva(reserva),
        acompanhantes=_acompanhantes_da_reserva(reserva),
        conta=_conta_da_reserva(reserva),
        mensagens=conversa.conversa_da_equipe(usuario_atual(), identificador),
        # Bloco 17e: quanto voltaria ao hóspede em cada motivo, hoje.
        previa=(
            pagamentos.previa_do_cancelamento(reserva, _quem())
            if "cancelada" in reserva_repo.TRANSICOES[reserva["situacao"]]
            else None
        ),
        rotulos_do_motivo=pagamentos.ROTULOS_DO_MOTIVO,
        limite_da_mensagem=conversa.LIMITE_DO_TEXTO,
        # Bloco 18f: motivo, quando e quem, se a reserva foi cancelada.
        cancelamento=painel_equipe.cancelamento(reserva, _quem()),
    )


def _acompanhantes_da_reserva(reserva):
    """A lista de quem vai com o titular, para a equipe (20/11).

    Documento inteiro: é para a conferência na chegada. `None` se a lista
    não passa na verificação de integridade — a página abre e diz isso,
    em vez de cair.
    """
    from app.seguranca.cripto import FalhaDeAutenticacao

    try:
        return acompanhante_repo.listar_da_reserva(
            reserva["id"], usuario_id=_quem()
        )
    except FalhaDeAutenticacao:
        _registrador.warning(
            "Acompanhante ilegível na reserva %s", reserva["id"]
        )
        return None


@reservas.route("/<int:identificador>/editar", methods=["GET", "POST"])
@requer_perfil(ADMINISTRADOR, RECEPCIONISTA)
def editar(identificador):
    from app.dados import chale_repo

    reserva = reserva_repo.buscar_por_id(identificador, usuario_id=_quem())
    if reserva is None:
        abort(404)

    contexto = {
        "usuario": usuario_atual(),
        "chales": chale_repo.listar(apenas_ativos=True, usuario_id=_quem()),
        "hospedes": _hospedes_para_selecao(),
        "novo": False,
    }

    if request.method == "GET":
        contexto["reserva"] = reserva
        return render_template("painel/reserva_form.html", **contexto)

    dados = _dados_do_formulario()

    try:
        reserva_repo.atualizar(identificador, dados, usuario_id=_quem())
    except ErroDeDados as erro:
        flash(str(erro), "erro")
        contexto["reserva"] = dict(reserva, **dados)
        return render_template(
            "painel/reserva_form.html", **contexto
        ), _codigo(erro)

    flash("Reserva atualizada.", "sucesso")
    return redirect(url_for("reservas.detalhe", identificador=identificador))


@reservas.route("/<int:identificador>/situacao", methods=["POST"])
@requer_perfil(ADMINISTRADOR, RECEPCIONISTA)
def mudar_situacao(identificador):
    """Move a reserva no ciclo de vida.

    Aceita apenas POST, com token CSRF. A situação não vem do formulário
    de edição de propósito: cancelar e concluir têm regra de transição
    própria, e misturá-las com a edição permitiria reabilitar uma reserva
    cancelada por um campo oculto. Confirmar não passa por aqui desde o
    Bloco 16b — é a conferência do Pix, em Pagamentos; um envio forjado
    com `situacao=confirmada` é recusado pelo mapa de transições.
    """
    reserva = reserva_repo.buscar_por_id(identificador, usuario_id=_quem())
    if reserva is None:
        abort(404)

    nova_situacao = (request.form.get("situacao") or "").strip()
    motivo = (request.form.get("motivo") or "").strip() or None

    try:
        if nova_situacao == "cancelada":
            # Bloco 17e: o cancelamento diz o motivo, e o motivo decide a
            # devolução, pela mesma regra do site. Bloco 18e: o pago é lido
            # antes, para o aviso não dizer "Não há devolução" de uma
            # reserva que ninguém pagou — soava como dinheiro retido.
            nada_pago = pagamentos.total_pago(reserva["id"], _quem()) == 0
            devolver = pagamentos.cancelar_pela_equipe(
                usuario_atual(), reserva, motivo
            )
        else:
            reserva_repo.mudar_situacao(
                identificador, nova_situacao, usuario_id=_quem()
            )
    except ErroDeDados as erro:
        flash(str(erro), "erro")
    else:
        if nova_situacao == "cancelada":
            flash(
                f"Reserva cancelada {pagamentos.ROTULOS_DO_MOTIVO[motivo]}. "
                + (
                    f"Devolva {pedido.em_reais(devolver)} ao hóspede pelo "
                    f"Pix e registre em Pagamentos, em Devoluções."
                    if devolver > 0
                    else "Nada tinha sido pago, então não há o que devolver."
                    if nada_pago else "Não há devolução."
                ),
                "sucesso",
            )
        else:
            flash(f"Reserva marcada como {nova_situacao}.", "sucesso")

    return redirect(url_for("reservas.detalhe", identificador=identificador))


def _dados_do_formulario() -> dict:
    """Lê o formulário, com os identificadores como inteiros.

    `type=int` não é detalhe. O formulário devolve texto, e o template
    marca a opção escolhida comparando com o identificador do chalé, que
    é inteiro. Em Jinja2, "1" == 1 é falso — então, num envio recusado, o
    chalé e o hóspede voltavam ao campo vazio e a recepção precisava
    escolher os dois de novo, depois de já ter preenchido tudo.

    Valor ausente ou não numérico vira None, e o repositório responde
    "Selecione o chalé e o hóspede".
    """
    return {
        "chale_id": request.form.get("chale_id", type=int),
        "hospede_id": request.form.get("hospede_id", type=int),
        "data_entrada": request.form.get("data_entrada"),
        "data_saida": request.form.get("data_saida"),
        "num_pessoas": request.form.get("num_pessoas"),
        "observacoes": request.form.get("observacoes"),
    }


def _hospede_da_reserva(reserva):
    """Nome do hóspede, decifrado, para a tela de detalhe.

    Um registro que não decifra não derruba a página: a tela mostra que
    ele existe e não pôde ser lido.
    """
    from app.dados import hospede_repo
    from app.seguranca.cripto import FalhaDeAutenticacao

    try:
        return hospede_repo.buscar_por_id(
            reserva["hospede_id"], usuario_id=_quem()
        )
    except FalhaDeAutenticacao:
        return None


def _conta_da_reserva(reserva):
    """Conta aberta da estadia, noite a noite.

    Mostrar só o total esconde de onde ele vem. Com os trechos à vista, a
    recepção explica ao hóspede por que oito noites custaram mais do que
    oito vezes a diária de baixa temporada — e confere a conta sem
    precisar confiar no sistema.

    Lacuna no cadastro de tarifas devolve None: a tela mostra um traço, e
    a reserva continua existindo.
    """
    from app.dados import calculo

    try:
        return calculo.detalhar(
            reserva["chale_id"],
            reserva["data_entrada"],
            reserva["data_saida"],
            usuario_id=_quem(),
        )
    except ErroDeDados:
        return None


# ---------------------------------------------------------------------------
# Área do hóspede
# ---------------------------------------------------------------------------

@reservas.route("/minhas")
@requer_perfil(HOSPEDE)
def minhas():
    """As reservas de quem está conectado. Somente as dele.

    **De onde vem o `hospede_id`.** Da linha do usuário na tabela
    `usuario`, lida do banco a cada requisição por `usuario_atual()`. Não
    vem da URL, não vem do formulário e não vem da sessão do navegador.
    Essa é a diferença entre esta tela e um furo de autorização: se o
    identificador fosse parâmetro, trocar o número no endereço mostraria
    as reservas de outro hóspede — a falha de controle de acesso mais
    comum que existe em sistema web.

    **Onde a filtragem acontece.** No `WHERE` do SQL, dentro de
    `listar_por_hospede`. Trazer todas as reservas e descartar as alheias
    em Python faria o banco entregar à aplicação dado que o solicitante
    não pode ver, e bastaria um esquecimento no laço para exibi-lo.

    O detalhe da reserva não é aberto aqui: `reservas.detalhe` exige
    administrador ou recepcionista, e a conta aberta da estadia aparece
    direto na linha.
    """
    usuario = usuario_atual()

    # A trigger de integridade de perfil exige o vínculo quando o perfil é
    # `hospede`. Se ele não existe, a conta está malformada: não se adivinha
    # a quem ela pertence, e mostrar tudo seria exatamente o erro oposto.
    if not usuario.get("hospede_id"):
        flash(
            "Sua conta não está vinculada a um cadastro de hóspede. "
            "Procure a recepção.",
            "erro",
        )
        return render_template(
            "painel/minhas_reservas.html",
            usuario=usuario,
            reservas=[],
            vinculada=False,
        )

    lista = reserva_repo.listar_por_hospede(
        usuario["hospede_id"], usuario_id=usuario["id"]
    )

    em_andamento, historico = _separar_reservas(lista)

    # A conta de cada estadia, calculada noite a noite. Lacuna de tarifa
    # devolve None e a linha mostra um traço.
    contas = {reserva["id"]: _conta_da_reserva(reserva) for reserva in lista}

    # Etapa 5, Bloco 7: cada reserva em andamento vira um card, com o que
    # falta pagar, o prazo e os botões que o banco aceitaria agora.
    # Bloco 8b: o que a equipe já conferiu entra no card, para a linha do
    # tempo e o "Valor pago". O filtro de dono está no SQL.
    lancamentos = pagamento_repo.lancamentos_por_reserva_do_hospede(
        usuario["hospede_id"], usuario_id=usuario["id"]
    )
    # 20/11: no Histórico, a situação da devolução de quem cancelou.
    devolvidas = pagamento_repo.devolucoes_do_hospede(
        usuario["hospede_id"], usuario_id=usuario["id"]
    )
    devolucoes = {
        reserva["id"]: pedido.situacao_da_devolucao(
            reserva, lancamentos.get(reserva["id"], {}),
            devolvidas.get(reserva["id"]),
        )
        for reserva in historico
    }

    cartoes = {
        reserva["id"]: pedido.cartao(
            {**reserva, "lancamentos": lancamentos.get(reserva["id"], {})}
        )
        for reserva in em_andamento
    }

    # Etapa 5, Bloco 15: quem vai em cada reserva. O documento sai daqui
    # já mascarado — o texto inteiro nem chega ao template.
    acompanhantes = {
        reserva_id: [
            {
                "nome": pessoa["nome"],
                "documento": pedido.mascarar_documento(pessoa["documento"]),
                "menor_de_idade": pessoa["menor_de_idade"],
            }
            for pessoa in lista_de_pessoas
        ]
        for reserva_id, lista_de_pessoas in acompanhante_repo.listar_por_hospede(
            usuario["hospede_id"], usuario_id=usuario["id"]
        ).items()
    }

    return render_template(
        "painel/minhas_reservas.html",
        usuario=usuario,
        em_andamento=em_andamento,
        historico=historico,
        reservas=lista,
        contas=contas,
        cartoes=cartoes,
        # Bloco 17b: reservas com resposta da pousada ainda não vista.
        respostas_novas={
            resposta["reserva_id"] for resposta in conversa.respostas_novas(usuario)
        },
        acompanhantes=acompanhantes,
        devolucoes=devolucoes,
        rotulo_do_cancelamento=pedido.rotulo_do_cancelamento,
        # Ajuste 15.9: o titular no topo da lista de quem vai.
        titular=pedido.titular(usuario["hospede_id"], usuario_id=usuario["id"]),
        chave_pix=getattr(current_app.villaju, "PIX_CHAVE", ""),
        atualizar=any(c["atualizar"] for c in cartoes.values()),
        vinculada=True,
    )


# ---------------------------------------------------------------------------
# Bloco 6 — o portal do hóspede
# ---------------------------------------------------------------------------

# As situações que mantêm uma reserva viva.
#
# As mesmas duas da trigger de não-sobreposição, e isso não é
# coincidência: uma reserva que ocupa o chalé é uma reserva em andamento.
SITUACOES_VIVAS = ("pendente", "confirmada")


def _separar_reservas(lista):
    """Divide as reservas em **em andamento** e **histórico**.

    Em andamento é a conjunção de duas coisas, e as duas são
    necessárias: situação que ainda vale E saída de hoje em diante.

    Só a situação não basta — uma reserva confirmada cuja saída foi mês
    passado está confirmada e acabada, e deixá-la no topo empurraria para
    baixo a que começa amanhã. Só a data também não basta — uma reserva
    cancelada para a semana que vem não está em andamento coisa nenhuma.

    A separação é feita aqui e não no SQL de propósito: `listar_por_hospede`
    já devolve as reservas desta pessoa, filtradas no `WHERE` por
    `hospede_id`. Uma segunda consulta repetiria esse filtro — e repetir
    filtro de autorização é criar um segundo lugar onde ele pode ser
    esquecido.
    """
    hoje = dt.date.today()

    em_andamento = []
    historico = []

    for reserva in lista:
        saida = reserva["data_saida"]
        if isinstance(saida, dt.datetime):
            saida = saida.date()

        viva = reserva["situacao"] in SITUACOES_VIVAS and saida >= hoje
        (em_andamento if viva else historico).append(reserva)

    # Em andamento, a mais próxima primeiro: é a que interessa.
    # No histórico, a mais recente primeiro: é a última lembrança.
    em_andamento.sort(key=lambda r: r["data_entrada"])
    historico.sort(key=lambda r: r["data_entrada"], reverse=True)

    return em_andamento, historico


@reservas.route("/perfil")
@requer_perfil(HOSPEDE)
def perfil():
    """Os próprios dados do hóspede, decifrados para quem é dono deles.

    **De onde vem o identificador.** Da linha do usuário na tabela
    `usuario`, lida do banco a cada requisição. Não da URL, não do
    formulário, não da sessão do navegador — a mesma regra de `minhas`, e
    pela mesma razão: se fosse parâmetro, trocar o número no endereço
    mostraria o cadastro de outra pessoa.

    **Por que é só leitura.** Documento e e-mail não se editam aqui, e
    não por esquecimento:

    O **documento** é identidade. Quem errou ao digitar precisa falar com
    a recepção, que confere com o papel — deixar trocá-lo sozinho
    permitiria a alguém com a sessão tomada assumir outra identidade no
    sistema.

    O **e-mail é o login**: o login desta conta é o HMAC do endereço.
    Trocá-lo aqui mudaria a chave de acesso, e teria de reemitir
    confirmação para o endereço novo antes de valer — é um fluxo próprio,
    com prova de posse, e não um campo de formulário.

    Nome e telefone são editáveis pela recepção, e o serão por aqui num
    bloco futuro.
    """
    from app.dados import hospede_repo

    usuario = usuario_atual()

    if not usuario.get("hospede_id"):
        flash(
            "Sua conta não está vinculada a um cadastro de hóspede. "
            "Procure a recepção.",
            "erro",
        )
        return render_template(
            "painel/perfil.html", usuario=usuario, hospede=None
        )

    try:
        hospede = hospede_repo.buscar_por_id(
            usuario["hospede_id"], usuario_id=usuario["id"]
        )
    except FalhaDeAutenticacao:
        # O registro não passou na verificação de integridade. Para a
        # pessoa dona dele, mostrar dado alterado seria pior do que não
        # mostrar nada.
        _registrador.exception("cadastro do hóspede não pôde ser decifrado")
        flash(
            "Não foi possível abrir o seu cadastro. Procure a recepção.",
            "erro",
        )
        hospede = None

    return render_template(
        "painel/perfil.html", usuario=usuario, hospede=hospede
    )


@reservas.route("/perfil/senha", methods=["GET", "POST"])
@requer_perfil(HOSPEDE)
def trocar_senha():
    """Troca da própria senha, exigindo a senha atual.

    **Por que a senha atual é exigida.** Sem ela, quem tomasse uma sessão
    emprestada — um navegador aberto na recepção, um cookie capturado —
    trocaria a senha e transformaria um acesso temporário em permanente,
    expulsando a dona da conta. A senha atual é a prova de que quem está
    pedindo a troca é quem sabe a senha, e não apenas quem está com a
    sessão.

    **A mesma recusa para senha atual errada e para qualquer outra
    falha**, e nenhuma delas revela mais do que precisa.

    **A sessão é renovada ao fim.** Trocar a senha é o momento em que se
    quer ter certeza de que o identificador de sessão em uso não é um que
    alguém tenha visto antes.
    """
    usuario = usuario_atual()

    if request.method == "GET":
        return render_template(
            "painel/trocar_senha.html",
            usuario=usuario,
            minimo_da_senha=TAMANHO_MINIMO_DA_SENHA,
        )

    atual = request.form.get("senha_atual") or ""
    nova = request.form.get("senha_nova") or ""
    confirmacao = request.form.get("senha_confirmacao") or ""

    def recusar(mensagem):
        flash(mensagem, "erro")
        return render_template(
            "painel/trocar_senha.html",
            usuario=usuario,
            minimo_da_senha=TAMANHO_MINIMO_DA_SENHA,
        ), 400

    # A senha atual primeiro, e sempre: validar o formato da nova antes
    # de conferir a atual diria a quem não sabe a senha se a nova
    # proposta seria aceita, o que é informação de graça.
    if not senhas.verificar(usuario["senha_hash"], atual):
        auditoria_repo.registrar(
            auditoria_repo.LOGIN_FALHA,
            usuario_id=usuario["id"],
            detalhe="Senha atual incorreta na troca de senha",
        )
        return recusar("A senha atual não confere.")

    if len(nova) < TAMANHO_MINIMO_DA_SENHA:
        return recusar(
            f"A senha nova precisa ter ao menos "
            f"{TAMANHO_MINIMO_DA_SENHA} caracteres."
        )
    if len(nova) > senhas.TAMANHO_MAXIMO:
        return recusar(
            f"A senha nova tem {len(nova)} caracteres; o limite é "
            f"{senhas.TAMANHO_MAXIMO}."
        )
    if nova != confirmacao:
        return recusar("A senha nova e a confirmação não são iguais.")

    if nova == atual:
        return recusar("A senha nova precisa ser diferente da atual.")

    # Trocar a senha encerra as OUTRAS sessões da conta (Relatório 2,
    # achado K1): se a troca é porque alguém tomou a conta, quem tomou sai
    # na próxima requisição. Quem trocou continua dentro porque o cookie
    # dele recebe a versão nova logo abaixo.
    #
    # Até 23/11 isto era uma limitação declarada. A primeira versão só
    # limpava e reescrevia a sessão — teatro, porque a sessão do Flask vive
    # no cookie assinado e o servidor aceitava qualquer cookie antigo bem
    # assinado e no prazo. Uma sabotagem mostrou que remover aquilo não
    # quebrava nada; a análise dinâmica mostrou o mesmo no "Sair" (K1). A
    # versão no banco é a marca conferida a cada requisição que faltava,
    # e não custa consulta extra: `usuario_atual` já relê a conta sempre.
    versao = usuario_repo.trocar_senha(
        usuario["id"], senhas.gerar_hash(nova), usuario_id=usuario["id"]
    )
    session["versao_sessao"] = versao

    auditoria_repo.registrar(
        auditoria_repo.SENHA_ALTERADA,
        usuario_id=usuario["id"],
        detalhe="Senha alterada pela própria pessoa",
    )

    flash("Senha alterada.", "sucesso")
    return redirect(url_for("reservas.perfil"))


# ---------------------------------------------------------------------------
# Etapa 5, Bloco 7 — o pedido do hóspede e o Pix
# ---------------------------------------------------------------------------
#
# Três rotas, todas do perfil hóspede:
#
# - `/reservas/pedir`: escolher, ver o valor e os prazos, aceitar o
#   regulamento, pedir;
# - `/reservas/<n>/pix`: declarar que fez o Pix;
# - `/reservas/<n>/desistir`: desistir de um pedido ainda sem Pix.
#
# **As duas últimas levam o número da reserva no endereço**, e por isso o
# dono da reserva vai na condição do UPDATE, no repositório. Uma reserva
# de outra pessoa não é encontrada, e a resposta é **idêntica** à de uma
# reserva que não existe ou que já saiu do prazo: o mesmo redirecionamento,
# a mesma mensagem. Quem troca o número no endereço não aprende nada.

# Um pedido por vez já limita o estrago de quem tentasse bloquear chalés;
# o limite de taxa limita o ciclo de pedir e desistir.
_LIMITE_DE_PEDIDOS = "10 per hour"

_RECUSA_UNIFORME = (
    "Esta reserva não aceita mais essa operação. Confira a situação abaixo."
)


def _hospede_conectado():
    """A conta conectada, relida do banco, com o vínculo de hóspede.

    O vínculo é exigido pela trigger de integridade de perfil. Sem ele, a
    conta está malformada, e não se adivinha a quem ela pertence.
    """
    usuario = usuario_atual()
    if not usuario.get("hospede_id"):
        abort(403)
    return usuario


# A revisão (passo 4) não grava nada, mas confere o documento do titular
# no banco a cada envio. Um limite próprio, folgado o bastante para quem
# corrige um nome três ou quatro vezes.
_LIMITE_DE_REVISOES = "60 per hour"


def _escolha_da_url():
    return {
        campo: (request.args.get(campo) or "").strip()
        for campo in ("chale", "entrada", "saida", "pessoas")
    }


def _escolha_do_formulario():
    return {
        campo: (request.form.get(campo) or "").strip()
        for campo in ("chale", "entrada", "saida", "pessoas")
    }


def _tela_do_pedido(passo, escolha, resumo=None, erro=None, digitados=None):
    return render_template(
        "painel/pedir_reserva.html",
        passo=passo,
        chales=chale_repo.listar(apenas_ativos=True),
        escolha=escolha,
        resumo=resumo,
        erro=erro,
        digitados=digitados or [],
        regras=regulamento.regras(),
        minutos_para_o_pix=regulamento.MINUTOS_PARA_O_PIX,
        hoje=dt.date.today().isoformat(),
    )


def _mensagem_de_indisponivel(resumo):
    return (
        f"O chalé {resumo['chale_nome']} já está reservado em parte desse "
        f"período. Escolha outras datas."
    )


def _tela_do_passo_3_com_erro(escolha, erro):
    """Volta ao passo 3 com o que foi digitado, sem redirecionar.

    Redirecionar levaria nomes e documentos para o endereço. Se o erro é
    do chalé ou das datas, o passo 3 não serve, e a tela volta ao 1.
    """
    try:
        resumo = pedido.simular(escolha)
    except pedido.ErroDePedido:
        return _tela_do_pedido(1, escolha, erro=erro)

    if not resumo["disponivel"]:
        return _tela_do_pedido(1, escolha, erro=erro)

    return _tela_do_pedido(
        3,
        escolha,
        resumo=resumo,
        erro=erro,
        digitados=pedido.digitados(request.form, resumo["pessoas"]),
    )


@reservas.route("/pedir", methods=["GET", "POST"])
@requer_perfil(HOSPEDE)
@limiter.limit(_LIMITE_DE_PEDIDOS, methods=["POST"])
def pedir():
    """Pedido de reserva em quatro passos, sem JavaScript.

    1. **Chalé e datas** — GET para esta mesma rota.
    2. **Pessoas** — botões de 1 até a capacidade do chalé escolhido; GET.
    3. **Acompanhantes** — nome e documento de cada um, N - 1 campos. Vai
       por **POST** para `revisar_pedido`: nome e documento numa URL iriam
       para o histórico do navegador e para o log do servidor.
    4. **Resumo** — valor, prazos, a lista de quem vai, regulamento e
       aceite. É a resposta de `revisar_pedido`.

    **POST aqui** grava. Tudo é conferido de novo: o valor é recalculado,
    a capacidade e os acompanhantes revalidados. Nenhum valor vem do
    formulário, e o número de pessoas que vem dele é só uma escolha.
    """
    usuario = _hospede_conectado()

    # 20/11, a pedido do Dimas: com um pedido em aberto, o aviso aparece
    # logo na abertura, e não depois dos quatro passos. Vale também para o
    # POST: o envio final de uma aba antiga cai no mesmo aviso. O banco
    # continua decidindo na gravação.
    if not pedido.pode_pedir(usuario):
        return _tela_do_pedido(0, _escolha_da_url(),
                               erro=pedido.AVISO_DE_PEDIDO_EM_ABERTO)

    if request.method == "POST":
        escolha = _escolha_do_formulario()
        try:
            identificador = pedido.pedir(usuario, request.form)
        except pedido.ErroDePedido as erro:
            return _tela_do_passo_3_com_erro(escolha, str(erro))

        flash(
            "Pedido registrado. Faça o Pix e informe pelo botão do card "
            "abaixo.",
            "sucesso",
        )
        return redirect(
            url_for("reservas.minhas") + f"#reserva-{identificador}"
        )

    escolha = _escolha_da_url()

    if not (escolha["chale"] and escolha["entrada"] and escolha["saida"]):
        return _tela_do_pedido(1, escolha)

    # Chalé e datas conferidos com uma pessoa; o número de verdade é
    # conferido logo abaixo, quando houver.
    try:
        resumo = pedido.simular({**escolha, "pessoas": "1"})
    except pedido.ErroDePedido as falha:
        return _tela_do_pedido(1, escolha, erro=str(falha))
    if not resumo["disponivel"]:
        return _tela_do_pedido(
            1, escolha, erro=_mensagem_de_indisponivel(resumo)
        )

    if not escolha["pessoas"]:
        return _tela_do_pedido(2, escolha, resumo=resumo)

    try:
        completo = pedido.simular(escolha)
    except pedido.ErroDePedido as falha:
        # Envio montado à mão, com mais pessoas que o chalé recebe: volta
        # aos botões, que só oferecem o que cabe.
        return _tela_do_pedido(
            2, {**escolha, "pessoas": ""}, resumo=resumo, erro=str(falha)
        )

    return _tela_do_pedido(
        3, escolha, resumo=completo,
        digitados=pedido.digitados({}, completo["pessoas"]),
    )


@reservas.route("/pedir/revisar", methods=["POST"])
@requer_perfil(HOSPEDE)
@limiter.limit(_LIMITE_DE_REVISOES)
def revisar_pedido():
    """Passo 4: o resumo com a lista de quem vai. Nada é gravado."""
    usuario = _hospede_conectado()
    escolha = _escolha_do_formulario()
    if not pedido.pode_pedir(usuario):
        return _tela_do_pedido(0, escolha, erro=pedido.AVISO_DE_PEDIDO_EM_ABERTO)

    try:
        resumo = pedido.revisar(usuario, request.form)
    except pedido.ErroDePedido as erro:
        return _tela_do_passo_3_com_erro(escolha, str(erro))

    if not resumo["disponivel"]:
        return _tela_do_pedido(
            1, escolha, erro=_mensagem_de_indisponivel(resumo)
        )

    return _tela_do_pedido(4, escolha, resumo=resumo)


@reservas.route("/<int:identificador>/pix", methods=["POST"])
@requer_perfil(HOSPEDE)
def informar_pix(identificador):
    """O hóspede declara que fez o Pix — do primeiro valor ou do restante."""
    usuario = _hospede_conectado()

    if pedido.informar_pix(
        usuario, identificador, request.form.get("etapa", "")
    ):
        flash(
            "Obrigado! A equipe da pousada vai conferir o Pix no extrato "
            "e confirmar.",
            "sucesso",
        )
    else:
        flash(_RECUSA_UNIFORME, "erro")

    return redirect(url_for("reservas.minhas"))


@reservas.route("/<int:identificador>/cancelar", methods=["POST"])
@requer_perfil(HOSPEDE)
def cancelar(identificador):
    """Bloco 9: o hóspede cancela uma reserva confirmada.

    O card disse, antes do clique, se haveria devolução. A resposta para
    reserva alheia, inexistente ou que não aceita cancelamento é a mesma.
    """
    usuario = _hospede_conectado()
    devolve = pedido.cancelar(usuario, identificador)

    if devolve is None:
        flash(_RECUSA_UNIFORME, "erro")
    elif devolve > 0:
        flash(
            f"Reserva cancelada. O chalé voltou a ficar disponível. A equipe "
            f"da pousada vai devolver {pedido.em_reais(devolve)} pelo Pix.",
            "sucesso",
        )
    else:
        flash(
            "Reserva cancelada. O chalé voltou a ficar disponível. Pela regra "
            "do regulamento, não há devolução.",
            "sucesso",
        )
    return redirect(url_for("reservas.minhas"))


@reservas.route("/<int:identificador>/desistir", methods=["POST"])
@requer_perfil(HOSPEDE)
def desistir(identificador):
    """Desiste de um pedido sem Pix. O chalé volta a ficar livre na hora."""
    usuario = _hospede_conectado()

    if pedido.desistir(usuario, identificador):
        flash("Pedido cancelado. O chalé voltou a ficar disponível.", "sucesso")
    else:
        flash(_RECUSA_UNIFORME, "erro")

    return redirect(url_for("reservas.minhas"))

