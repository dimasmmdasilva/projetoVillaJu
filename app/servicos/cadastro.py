"""Autocadastro do hóspede: a única escrita anônima em `hospede` e `usuario`.

Este módulo existe para ser **o único ponto** por onde a face pública
alcança essas duas tabelas, e é escrito com essa restrição em mente.

---

## Por que um serviço, e não código dentro da rota

A Etapa 4 estabeleceu, e verificou por análise de código, que
`app/rotas/publico.py` não importa `hospede_repo`, `reserva_repo`,
`usuario_repo` nem `auditoria_repo`. O autocadastro precisa escrever em
duas dessas tabelas.

A regra não foi abandonada — foi estreitada, do mesmo modo que a
especificação prescreve para o Bloco 8. A face pública passa a alcançar
este módulo, que tem duas entradas e **nenhuma função que devolva dado**:

    registrar(dados, configuracao) -> None
    confirmar(token, configuracao)  -> bool

Nenhuma das duas devolve registro, lista ou campo. A propriedade
essencial da Etapa 4 se mantém e continua verificável: **a face pública
não tem como exibir dado de hóspede nem de reserva.** Ela pode criar um
cadastro; não pode ler nenhum.

## Por que a resposta é sempre a mesma

`registrar` nunca informa se o endereço já tinha conta. Se informasse, o
formulário viraria um oráculo de enumeração: bastaria submeter endereços
e ler as respostas para levantar a lista de clientes da hospedaria — que
é precisamente o risco que fez a Etapa 4 recusar escrita anônima em
`hospede`.

O que destrava o autocadastro em segurança é o e-mail. Quem já tem conta
recebe um aviso de que alguém tentou cadastrar o endereço; quem não tem
recebe o convite. **Do lado de fora, nada distingue os dois casos** — nem
a resposta HTTP, nem o código de estado, nem o tempo, porque os dois
caminhos fazem o mesmo trabalho: uma consulta e um envio.

## Por que o cadastro público nunca altera hóspede existente

Se o endereço já pertence a um hóspede cadastrado pela recepção — o caso
comum de quem pediu informação pelo formulário de contato e depois quis
conta —, a conta é **vinculada** ao registro que existe. Os dados dele
não são tocados.

A tentação seria atualizar nome e telefone com o que veio do formulário,
"para manter em dia". Seria uma falha grave: qualquer pessoa que
soubesse o endereço de e-mail de um hóspede poderia reescrever os dados
pessoais dele sem autenticar-se. O formulário público cria; não edita.

## A linha de auditoria sem autor

Estas escritas acontecem com `usuario_id=None`, e a trigger grava a
linha de auditoria com autor nulo. A Etapa 4 citou exatamente isso como
razão para recusar escrita anônima.

A diferença agora é que a escrita **não é anônima por muito tempo**: a
conta nasce inativa e só existe de verdade quando o link do e-mail é
aberto, o que prova a posse do endereço. O autor da linha é nulo, mas o
registro criado está amarrado a um endereço comprovado — o que não era
possível antes de o Bloco 1 existir.
"""

import logging
import re

from app.dados import hospede_repo, token_repo, usuario_repo
from app.seguranca import senhas
from app.servicos import correio

_registrador = logging.getLogger(__name__)

# Mínimo de caracteres da senha escolhida no cadastro público.
#
# Dez, e nenhuma exigência de maiúscula, número ou símbolo. A recomendação
# do NIST SP 800-63B é essa desde 2017: comprimento ajuda, regra de
# composição não — ela produz `Senha@123` em toda parte, que é curto e
# está em qualquer dicionário de ataque.
#
# `senhas.TAMANHO_MINIMO` é 1 e não serve aqui: ele protege a função de
# hash contra entrada vazia, não é política de senha.
TAMANHO_MINIMO_DA_SENHA = 10

# Teto do endereço, igual ao limite da coluna cifrada de `hospede`.
TAMANHO_MAXIMO_DO_EMAIL = 150

# Verificação deliberadamente frouxa: um arroba, texto antes, um ponto
# depois, e nenhum espaço. Validar endereço de e-mail por expressão
# regular rigorosa é um problema conhecidamente mal resolvido — a gramática
# do RFC 5322 aceita coisas que nenhum servidor real entrega, e toda
# tentativa de ser exata acaba recusando endereço legítimo.
#
# Não faz falta: a prova de que o endereço funciona é o link chegar. Uma
# expressão regular não prova nada sobre entrega, e o e-mail prova tudo.
_FORMATO_DE_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

ASSUNTO_DA_CONFIRMACAO = "Confirme o seu cadastro na VillaJu"
ASSUNTO_DO_AVISO = "Tentativa de cadastro com o seu e-mail na VillaJu"


class ErroDeCadastro(Exception):
    """O formulário trouxe dado que não serve.

    **A mensagem destas exceções vai para a tela**, e por isso nenhuma
    delas pode falar do endereço já existir. Elas falam de campo em
    branco, senha curta e formato de e-mail — coisas que a própria pessoa
    vê no que digitou, e que não revelam nada sobre o banco.
    """


# ---------------------------------------------------------------------------
# Validação
# ---------------------------------------------------------------------------

def _limpar(dados: dict) -> dict:
    """Normaliza e recusa o que não serve, antes de qualquer acesso ao banco.

    A ordem importa: validar primeiro evita gastar uma operação AES-GCM e
    uma consulta por causa de um formulário vazio, que é o que um robô
    manda.
    """
    limpo = {}

    for campo in ("nome", "documento", "email", "telefone"):
        valor = dados.get(campo)
        limpo[campo] = valor.strip() if isinstance(valor, str) else None

    if not limpo["nome"]:
        raise ErroDeCadastro("Informe o seu nome.")
    if not limpo["documento"]:
        raise ErroDeCadastro("Informe o seu CPF ou documento de identidade.")
    if not limpo["email"]:
        raise ErroDeCadastro("Informe o seu e-mail.")

    if len(limpo["email"]) > TAMANHO_MAXIMO_DO_EMAIL:
        raise ErroDeCadastro(
            f"O e-mail tem {len(limpo['email'])} caracteres; o limite é "
            f"{TAMANHO_MAXIMO_DO_EMAIL}."
        )

    if not _FORMATO_DE_EMAIL.match(limpo["email"]):
        raise ErroDeCadastro("O e-mail informado não parece um endereço válido.")

    senha = dados.get("senha")
    confirmacao = dados.get("senha_confirmacao")

    if not isinstance(senha, str) or not senha:
        raise ErroDeCadastro("Escolha uma senha.")

    # Sem `strip()` na senha: espaço no começo ou no fim é parte dela. Apará-lo
    # faria a senha gravada ser diferente da digitada, e a pessoa não
    # conseguiria entrar com o que escolheu.
    if len(senha) < TAMANHO_MINIMO_DA_SENHA:
        raise ErroDeCadastro(
            f"A senha precisa ter ao menos {TAMANHO_MINIMO_DA_SENHA} "
            f"caracteres."
        )
    if len(senha) > senhas.TAMANHO_MAXIMO:
        raise ErroDeCadastro(
            f"A senha tem {len(senha)} caracteres; o limite é "
            f"{senhas.TAMANHO_MAXIMO}."
        )

    if senha != confirmacao:
        raise ErroDeCadastro("A senha e a confirmação não são iguais.")

    limpo["senha"] = senha
    return limpo


# ---------------------------------------------------------------------------
# Mensagens
# ---------------------------------------------------------------------------

def _janela(configuracao) -> int:
    """Minutos que o cadastro tem para ser confirmado.

    Lido da configuração a cada chamada, e não guardado em constante de
    módulo, porque é o que permite a um teste subir uma aplicação com
    janela própria sem alterar as demais.
    """
    return int(getattr(configuracao, "CADASTRO_MINUTOS", 5) or 5)


def _texto_do_prazo(minutos: int) -> str:
    """O prazo em português, para o corpo do e-mail.

    Escrito à mão e não com uma biblioteca de formatação: são três casos,
    e o texto precisa dizer também que o link é de uso único — informação
    que nenhuma formatação de duração traria.
    """
    if minutos < 60:
        quanto = f"{minutos} minutos"
    elif minutos % 60 == 0:
        horas = minutos // 60
        quanto = "1 hora" if horas == 1 else f"{horas} horas"
    else:
        quanto = f"{minutos // 60} h {minutos % 60} min"

    return (
        f"O link vale por {quanto} e só pode ser usado uma vez. "
        "Passado o prazo, o cadastro é descartado e você pode fazê-lo de novo."
    )


def descartar_vencidos() -> int:
    """Remove cadastros cujo prazo de confirmação venceu. Devolve quantos.

    Exposta aqui, e não apenas em `usuario_repo`, porque quem a dispara
    alcança este módulo e não o repositório.

    **Não recebe a janela.** O prazo está no token, que é o mesmo valor
    que decide se o link abre — um relógio só. A configuração
    `CADASTRO_MINUTOS` governa a emissão do token, e não o descarte.

    Devolve um inteiro, que é contagem e não dado de pessoa.
    """
    return usuario_repo.descartar_nao_confirmados()


def _corpo_da_confirmacao(nome: str, link: str, prazo: str) -> str:
    return (
        f"Olá, {nome}.\n"
        "\n"
        "Recebemos um cadastro com este endereço de e-mail na VillaJu.\n"
        "Para ativar o seu acesso, abra o endereço abaixo:\n"
        "\n"
        f"{link}\n"
        "\n"
        f"{prazo}\n"
        "\n"
        "Se não foi você que se cadastrou, não faça nada: a conta não é\n"
        "ativada sem que este link seja aberto.\n"
        "\n"
        "VillaJu — Hospedaria em São Sebastião\n"
        "Esta mensagem é automática. Não responda a este endereço.\n"
    )


def _corpo_do_aviso(link_de_entrada: str) -> str:
    """Mensagem para quem já tem conta.

    Não contém link de ativação nem de troca de senha. Se contivesse,
    quem submeteu o endereço de outra pessoa no formulário conseguiria
    provocar o envio de um link capaz de alterar a conta alheia — e a
    defesa do autocadastro viraria a ferramenta do ataque.

    Contém apenas o endereço da tela de entrada, que é público e não
    carrega poder algum.
    """
    return (
        "Olá.\n"
        "\n"
        "Alguém tentou criar um cadastro na VillaJu com este endereço de\n"
        "e-mail, que já tem conta.\n"
        "\n"
        "Nada foi alterado e nenhuma conta nova foi criada.\n"
        "\n"
        "Se foi você, entre normalmente com a sua senha:\n"
        "\n"
        f"{link_de_entrada}\n"
        "\n"
        "Se não foi você, pode ignorar esta mensagem. Ninguém consegue\n"
        "entrar na sua conta sabendo apenas o seu endereço de e-mail.\n"
        "\n"
        "VillaJu — Hospedaria em São Sebastião\n"
        "Esta mensagem é automática. Não responda a este endereço.\n"
    )


def _enviar(destinatario: str, assunto: str, corpo: str, configuracao) -> None:
    """Envia, e trata a falha sem derrubar o cadastro.

    Um erro de SMTP não pode desfazer a conta já criada. A escolha da
    Forma B foi justamente esta: a conta existe inativa, e a recepção
    consegue ativá-la depois de falar com a pessoa. Derrubar tudo por
    causa do correio deixaria a pessoa sem nada.

    A falha vai para o log com o endereço, porque quem administra precisa
    saber para quem o envio não funcionou.
    """
    try:
        destino = correio.enviar(destinatario, assunto, corpo, configuracao)
    except Exception:  # noqa: BLE001
        _registrador.exception("falha ao enviar mensagem de cadastro")
        return

    _registrador.info("mensagem de cadastro entregue em %s", destino)


# ---------------------------------------------------------------------------
# As duas entradas públicas
# ---------------------------------------------------------------------------

def registrar(dados: dict, configuracao) -> None:
    """Processa o formulário de cadastro. **Não devolve nada.**

    O retorno vazio não é descuido: é o que garante que a rota não tenha
    como responder diferente para e-mail novo e para e-mail já
    cadastrado. Se devolvesse, por exemplo, o identificador criado, a
    rota poderia — por descuido, numa alteração futura — ramificar sobre
    ele, e a diferença apareceria na tela.

    Levanta `ErroDeCadastro` apenas para problema do formulário em si:
    campo em branco, senha curta, e-mail malformado. Nunca para dizer que
    o endereço já existe.
    """
    limpo = _limpar(dados)

    # Antes de qualquer consulta: libera o endereço e o documento de
    # cadastros que não foram confirmados na janela.
    #
    # A ordem importa. Se a varredura viesse depois, uma pessoa que
    # digitou o endereço errado e tenta de novo seria recusada pelo
    # cadastro vencido dela mesma — e recusada em silêncio, porque a
    # resposta é uniforme. Ela não teria como descobrir o motivo, e a
    # recepção também não.
    descartar_vencidos()

    login = usuario_repo.login_de_email(limpo["email"])
    base = getattr(configuracao, "EMAIL_BASE_URL", "").rstrip("/")

    # ------------------------------------------------------------------
    # Caso 1: o endereço já tem conta.
    #
    # Nada é criado e nada é alterado. A pessoa dona do endereço é
    # avisada, e quem submeteu o formulário recebe a mesma resposta de
    # sempre.
    # ------------------------------------------------------------------
    existente = hospede_repo.buscar_por_email(limpo["email"])

    # Duas perguntas, e as duas precisam ser feitas.
    #
    # A primeira é se existe conta com este login — o caso de quem já se
    # autocadastrou antes. A segunda é se o hóspede deste endereço já tem
    # conta **de qualquer login**, que é o caso de quem recebeu acesso
    # pela recepção, com login de apelido. Perguntar só a primeira
    # deixaria passar um ataque descrito em `usuario_repo.
    # existe_para_hospede`.
    ja_tem_conta = usuario_repo.buscar_por_login(login) is not None
    if not ja_tem_conta and existente is not None:
        ja_tem_conta = usuario_repo.existe_para_hospede(existente["id"])

    if ja_tem_conta:
        _registrador.info("cadastro recusado: o endereço já tem conta")
        _enviar(
            limpo["email"],
            ASSUNTO_DO_AVISO,
            _corpo_do_aviso(f"{base}/login"),
            configuracao,
        )
        return

    # ------------------------------------------------------------------
    # Caso 2: existe o hóspede, mas ele não tem conta.
    #
    # É o caminho de quem pediu informação pelo formulário de contato,
    # foi cadastrado pela recepção, e agora quer acesso. A conta é
    # vinculada ao registro existente, e **os dados dele não são
    # tocados** — ver o cabeçalho do módulo.
    # ------------------------------------------------------------------
    if existente is not None:
        hospede_id = existente["id"]
        criado_agora = False
    else:
        try:
            hospede_id = hospede_repo.criar(limpo)
        except hospede_repo.HospedeDuplicado:
            # O documento já pertence a um cadastro — possivelmente de
            # outra pessoa, possivelmente da mesma com outro e-mail. Em
            # nenhum dos casos cabe decidir aqui: a recepção resolve.
            # A resposta ao visitante continua idêntica.
            _registrador.info("cadastro recusado: documento já cadastrado")
            return
        except hospede_repo.ErroDeHospede as erro:
            # Mensagem escrita pelo repositório, falando de campo e
            # formato. Pode ir à tela.
            raise ErroDeCadastro(str(erro)) from erro
        criado_agora = True

    # ------------------------------------------------------------------
    # A conta, inativa.
    # ------------------------------------------------------------------
    try:
        usuario_id = usuario_repo.criar(
            login=login,
            nome=limpo["nome"],
            senha_hash=senhas.gerar_hash(limpo["senha"]),
            perfil="hospede",
            hospede_id=hospede_id,
            ativo=False,
        )
    except Exception:
        # Compensação: o hóspede recém-criado não pode ficar órfão.
        #
        # `hospede_repo.criar` tem a própria transação e já confirmou, de
        # modo que um `rollback` aqui não alcançaria. A remoção é possível
        # porque a conta não foi criada — a chave estrangeira é RESTRICT e
        # recusaria apagar hóspede que tivesse conta.
        #
        # Só remove o que este fluxo criou. Um hóspede que já existia
        # antes continua existindo: ele não é nosso para apagar.
        if criado_agora:
            try:
                hospede_repo.excluir(hospede_id)
                _registrador.warning(
                    "hóspede %s removido: a conta não pôde ser criada",
                    hospede_id,
                )
            except Exception:  # noqa: BLE001
                _registrador.exception(
                    "hóspede %s ficou sem conta e não pôde ser removido",
                    hospede_id,
                )
        raise

    # ------------------------------------------------------------------
    # O token e o convite.
    # ------------------------------------------------------------------
    token = token_repo.gerar(
        usuario_id,
        token_repo.CONFIRMACAO,
        minutos=_janela(configuracao),
    )

    _enviar(
        limpo["email"],
        ASSUNTO_DA_CONFIRMACAO,
        _corpo_da_confirmacao(
            limpo["nome"],
            f"{base}/confirmar/{token}",
            _texto_do_prazo(_janela(configuracao)),
        ),
        configuracao,
    )


def confirmar(token: str, configuracao=None) -> bool:
    """Consome o token e ativa a conta. Devolve se a ativação ocorreu.

    Devolve `False` para token inexistente, vencido, já consumido e de
    outro tipo — sem distinguir entre eles, como `token_repo.consumir`
    faz. Quem está sondando não aprende se um token existe.

    O booleano é o máximo que esta função devolve. Nada de identificador,
    nome ou endereço: a rota que a chama é pública, e a resposta dela não
    deve conter dado nenhum da conta ativada.
    """
    usuario_id = token_repo.consumir(token, token_repo.CONFIRMACAO)

    if usuario_id is None:
        return False

    # O token já foi queimado neste ponto. Se a ativação falhar, o link
    # não volta a valer — e isso é deliberado: um token reutilizável
    # depois de uma falha seria um token de uso múltiplo na prática. A
    # pessoa pede outro convite, que invalida o pendente e gera um novo.
    if not usuario_repo.ativar(usuario_id):
        # A conta já estava ativa. Acontece quando a recepção ativou
        # manualmente antes de o link ser aberto, o que a Forma B prevê.
        # Para quem clicou, o resultado é o mesmo: a conta funciona.
        _registrador.info("conta %s já estava ativa ao confirmar", usuario_id)

    return True
