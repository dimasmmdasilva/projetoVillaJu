"""Tradução dos erros do banco para exceções do domínio.

A regra de negócio deste projeto mora no banco: as triggers interrompem
operações inválidas com `SIGNAL SQLSTATE '45000'`, que chega ao driver
como erro 1644. As restrições de unicidade e de chave estrangeira têm os
próprios códigos.

Esse erro não pode chegar à tela como veio. A seção 6.4 do plano é
explícita: página de erro genérica, detalhe só no log. Mostrar
`mysql.connector.errors.DatabaseError: 1644 (45000)` ao recepcionista
revela a tecnologia em uso, o nome das tabelas, e às vezes o comando
inteiro — informação útil a quem está sondando o sistema, e inútil para
quem só quer saber que o chalé já está reservado.

    try:
        cursor.execute(...)
    except mysql.connector.Error as erro:
        raise traduzir(erro) from erro

As mensagens das triggers são exceção à regra: elas foram escritas em
português, para pessoas, justamente para poderem ser exibidas. "Já existe
reserva ativa para este chalé no período informado" é exatamente o que o
usuário precisa ler.
"""

import mysql.connector

# SIGNAL SQLSTATE '45000' chega ao driver com este número.
ERRO_DE_SIGNAL = 1644

# Entrada duplicada numa coluna com restrição de unicidade.
ERRO_DE_DUPLICIDADE = 1062

# Tentativa de apagar linha referenciada por outra tabela (ON DELETE RESTRICT).
ERRO_DE_REFERENCIA_EM_USO = 1451

# Tentativa de referenciar linha que não existe.
ERRO_DE_REFERENCIA_INVALIDA = 1452

# Valor fora da faixa da coluna, como num TINYINT UNSIGNED negativo.
ERRO_DE_FAIXA = 1264


class ErroDeDados(Exception):
    """Base das exceções de domínio da camada de dados."""


class RegraDoBanco(ErroDeDados):
    """Uma trigger interrompeu a operação.

    A mensagem vem da própria trigger, escrita em português para ser
    exibida. É a regra de negócio falando, não o banco.
    """


class RegistroDuplicado(ErroDeDados):
    """Violação de restrição de unicidade."""


class ReferenciaEmUso(ErroDeDados):
    """O registro não pode ser apagado porque outro depende dele."""


class ReferenciaInvalida(ErroDeDados):
    """O registro referenciado não existe."""


MENSAGENS = {
    ERRO_DE_DUPLICIDADE: "Já existe um registro com esse valor.",
    ERRO_DE_REFERENCIA_EM_USO: (
        "Este registro não pode ser removido porque está sendo usado."
    ),
    ERRO_DE_REFERENCIA_INVALIDA: (
        "O registro referenciado não existe ou foi removido."
    ),
    ERRO_DE_FAIXA: "Um dos valores informados está fora da faixa permitida.",
}


def _texto_da_trigger(erro) -> str:
    """Extrai a mensagem que a trigger definiu no SIGNAL.

    O driver entrega algo como "1644 (45000): Já existe reserva ativa...".
    Só interessa o que vem depois dos dois pontos.
    """
    bruto = str(getattr(erro, "msg", "") or str(erro))
    if ":" in bruto:
        bruto = bruto.split(":", 1)[1]
    return bruto.strip() or "A operação foi recusada por uma regra do sistema."


def traduzir(erro) -> ErroDeDados:
    """Converte um erro do driver na exceção de domínio correspondente.

    Erros não mapeados viram `ErroDeDados` com mensagem genérica. O
    original continua disponível pelo encadeamento de exceções, para o
    log — mas não para a tela.
    """
    numero = getattr(erro, "errno", None)

    if numero == ERRO_DE_SIGNAL:
        return RegraDoBanco(_texto_da_trigger(erro))

    if numero == ERRO_DE_DUPLICIDADE:
        return RegistroDuplicado(MENSAGENS[numero])

    if numero == ERRO_DE_REFERENCIA_EM_USO:
        return ReferenciaEmUso(MENSAGENS[numero])

    if numero == ERRO_DE_REFERENCIA_INVALIDA:
        return ReferenciaInvalida(MENSAGENS[numero])

    if numero in MENSAGENS:
        return ErroDeDados(MENSAGENS[numero])

    # Erro desconhecido: mensagem genérica, sem revelar nada. O original
    # chega ao log pelo `raise ... from erro`.
    return ErroDeDados("Não foi possível concluir a operação.")


def executar(cursor, comando: str, parametros=()) -> None:
    """Executa traduzindo o erro, para não repetir o try em cada chamada."""
    try:
        cursor.execute(comando, parametros)
    except mysql.connector.Error as erro:
        raise traduzir(erro) from erro
