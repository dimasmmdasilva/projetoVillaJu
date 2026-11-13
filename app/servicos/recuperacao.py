"""Recuperação de senha: a outra metade do ciclo de vida da conta.

A Etapa 5 passou a criar contas com senha escolhida pela própria pessoa.
Isso tem uma consequência que não é opcional: **ninguém mais conhece a
senha de ninguém.** Nem a recepção, nem o administrador, nem o banco — só
o hash Argon2id, que não volta.

Sem caminho de recuperação, quem esquece a senha fica sem conta. Não é
inconveniência: é perda de acesso definitiva, e a recepção não pode
ajudar nem querendo.

---

## O mesmo mecanismo da confirmação, com outro prazo

O tipo `recuperacao` do `token_acesso` existe desde o Bloco 2, com prazo
de **48 horas** — e a diferença em relação aos minutos da confirmação foi
decidida lá, com razão escrita: recuperar senha **não cria nem descarta
dado nenhum**. Quem pede o link já é cliente e já teve o endereço
comprovado; um prazo de minutos aqui só produziria gente trancada fora da
própria conta.

## Resposta uniforme, pela mesma razão do cadastro

`solicitar` nunca informa se o endereço tem conta. Se informasse, a tela
viraria um levantamento de quem é cliente da hospedaria — exatamente o
que o Bloco 3 se esforçou para evitar no cadastro, e seria inútil
evitá-lo ali e abri-lo aqui.

## Só conta ativa recebe link

Quem nunca confirmou o cadastro não usa a recuperação como atalho para
pular a confirmação. A resposta continua idêntica, e nenhum e-mail sai.

A distinção importa porque os dois fluxos provam a mesma coisa — posse do
endereço — mas **autorizam coisas diferentes**: a confirmação ativa uma
conta nova; a recuperação troca a senha de uma conta que já funciona.
Deixar a segunda fazer o trabalho da primeira contornaria a janela de
descarte do Bloco 4.

## A senha é validada ANTES de o token ser consumido

Caso contrário, digitar uma senha curta queimaria o link, e a pessoa
teria de pedir outro por causa de um erro de digitação.

## Limitação declarada

Redefinir a senha **não derruba sessões abertas em outros navegadores**.
As sessões do Flask são assinadas com a `SECRET_KEY` e não ficam
guardadas no servidor: não há lista para invalidar.

Se a pessoa está recuperando a senha porque alguém tomou a conta, quem
tomou continua dentro até a sessão expirar — oito horas, pelo
`PERMANENT_SESSION_LIFETIME`. Resolver exigiria uma marca no banco
conferida a cada requisição, o que é decisão de arquitetura com custo em
toda requisição, e não cabe neste bloco.
"""

import logging

from app.dados import auditoria_repo, token_repo, usuario_repo
from app.seguranca import senhas
from app.servicos import correio
from app.servicos.cadastro import (
    _FORMATO_DE_EMAIL,
    TAMANHO_MINIMO_DA_SENHA,
    ErroDeCadastro,
)

_registrador = logging.getLogger(__name__)

ASSUNTO = "Redefinir a sua senha na VillaJu"


def _enviar(destinatario: str, assunto: str, corpo: str, configuracao) -> None:
    """Envia, e trata a falha sem derrubar o pedido.

    Um erro de SMTP não pode transformar "não consegui mandar o e-mail"
    em página de erro: a resposta precisa ser a mesma de sempre, senão o
    tempo e o código de estado passam a distinguir endereço com conta de
    endereço sem conta — exatamente o que a resposta uniforme existe para
    impedir.

    Escrito aqui e não importado de `cadastro`: os dois serviços são
    separáveis de propósito, e importar função privada de um para o outro
    criaria um acoplamento que nenhum dos dois declara. São seis linhas.
    """
    try:
        destino = correio.enviar(destinatario, assunto, corpo, configuracao)
    except Exception:  # noqa: BLE001
        _registrador.exception("falha ao enviar o link de redefinição")
        return

    _registrador.info("link de redefinição entregue em %s", destino)


class ErroDeRecuperacao(ErroDeCadastro):
    """O formulário trouxe dado que não serve.

    Herda de `ErroDeCadastro` porque a regra é a mesma e a mensagem vai
    para a tela do mesmo jeito: fala de campo em branco e de senha curta,
    nunca de o endereço existir.
    """


def _corpo(link: str) -> str:
    # O prazo vem da mesma constante que `token_repo.gerar` usa. Escrito à
    # mão, a mensagem continuaria prometendo 48 horas depois de o prazo
    # mudar — e quem confia no e-mail chegaria com o link já vencido.
    prazo = correio.duracao_por_extenso(
        token_repo.VALIDADE_POR_TIPO[token_repo.RECUPERACAO]
    )

    return (
        "Olá.\n"
        "\n"
        "Alguém pediu para redefinir a senha desta conta na VillaJu.\n"
        "Para escolher uma senha nova, abra o endereço abaixo:\n"
        "\n"
        f"{link}\n"
        "\n"
        f"O link vale por {prazo} e só pode ser usado uma vez.\n"
        "\n"
        "Se não foi você, pode ignorar esta mensagem: nada foi alterado, e\n"
        "ninguém entra na sua conta sabendo apenas o seu endereço de\n"
        "e-mail.\n"
        "\n"
        "VillaJu — Hospedaria em São Sebastião\n"
        "Esta mensagem é automática. Não responda a este endereço.\n"
    )


def solicitar(email: str, configuracao) -> None:
    """Pede o link de redefinição. **Não devolve nada.**

    O retorno vazio é parte da defesa, pela mesma razão do
    `cadastro.registrar`: se devolvesse algo, a rota poderia ramificar
    sobre ele — por descuido, numa alteração futura — e a diferença entre
    endereço com conta e sem conta apareceria na tela.

    Levanta `ErroDeRecuperacao` apenas para endereço em branco ou
    malformado, que é problema do que a pessoa digitou. Nunca para dizer
    que a conta não existe.
    """
    email = (email or "").strip()
    if not email:
        raise ErroDeRecuperacao("Informe o seu e-mail.")

    # O mesmo critério do cadastro, e a mesma expressão — importada, não
    # copiada. Duas verificações de formato de e-mail no projeto seriam
    # duas que podem divergir, e aí o cadastro aceitaria o que a
    # recuperação recusa.
    #
    # Validar o formato não revela nada: fala do que a pessoa digitou,
    # não do que existe no banco. E sem isso, quem erra o endereço recebe
    # a mesma página de sempre e fica esperando um e-mail que nunca vem.
    if not _FORMATO_DE_EMAIL.match(email):
        raise ErroDeRecuperacao(
            "O e-mail informado não parece um endereço válido."
        )

    try:
        login = usuario_repo.login_de_email(email)
    except usuario_repo.ErroDeUsuario as erro:
        raise ErroDeRecuperacao(str(erro)) from erro

    conta = usuario_repo.buscar_por_login(login)

    # As duas recusas são silenciosas e indistinguíveis de fora.
    if conta is None:
        _registrador.info("recuperação pedida para endereço sem conta")
        return

    if not conta["ativo"]:
        # Conta que nunca foi confirmada não usa a recuperação como
        # atalho para pular a confirmação.
        _registrador.info("recuperação pedida para conta inativa")
        return

    token = token_repo.gerar(conta["id"], token_repo.RECUPERACAO)

    base = getattr(configuracao, "EMAIL_BASE_URL", "").rstrip("/")
    _enviar(email, ASSUNTO, _corpo(f"{base}/redefinir/{token}"), configuracao)

    auditoria_repo.registrar(
        auditoria_repo.SENHA_ALTERADA,
        usuario_id=conta["id"],
        detalhe="Link de redefinição de senha enviado",
    )


def token_vale(token: str) -> bool:
    """Se o link ainda serve, **sem consumi-lo**.

    Usada para decidir se a tela do formulário aparece. Devolve booleano
    e não o identificador: a tela não precisa saber de quem é a conta, e
    o que não é devolvido não vaza.
    """
    return token_repo.conferir(token, token_repo.RECUPERACAO) is not None


def redefinir(token: str, nova: str, confirmacao: str) -> bool:
    """Troca a senha pelo link. Devolve se a troca aconteceu.

    **A ordem das operações é a defesa.** A senha é validada antes de o
    token ser consumido: do contrário, digitar uma senha curta queimaria
    o link, e a pessoa teria de pedir outro por causa de um erro de
    digitação.

    O consumo continua sendo o que autoriza — ele é a atualização
    condicional que impede duas aberturas simultâneas de terem sucesso.
    `token_vale`, acima, só decide se a tela aparece.
    """
    if not isinstance(nova, str) or not nova:
        raise ErroDeRecuperacao("Escolha uma senha.")

    # Sem `strip()`: espaço no começo ou no fim é parte da senha.
    if len(nova) < TAMANHO_MINIMO_DA_SENHA:
        raise ErroDeRecuperacao(
            f"A senha precisa ter ao menos {TAMANHO_MINIMO_DA_SENHA} "
            f"caracteres."
        )
    if len(nova) > senhas.TAMANHO_MAXIMO:
        raise ErroDeRecuperacao(
            f"A senha tem {len(nova)} caracteres; o limite é "
            f"{senhas.TAMANHO_MAXIMO}."
        )
    if nova != confirmacao:
        raise ErroDeRecuperacao("A senha e a confirmação não são iguais.")

    usuario_id = token_repo.consumir(token, token_repo.RECUPERACAO)
    if usuario_id is None:
        return False

    usuario_repo.atualizar_hash(
        usuario_id, senhas.gerar_hash(nova), usuario_id=usuario_id
    )

    auditoria_repo.registrar(
        auditoria_repo.SENHA_ALTERADA,
        usuario_id=usuario_id,
        detalhe="Senha redefinida pelo link enviado por e-mail",
    )

    return True


__all__ = [
    "ASSUNTO",
    "ErroDeRecuperacao",
    "redefinir",
    "solicitar",
    "token_vale",
]
