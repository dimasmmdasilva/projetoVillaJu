"""Pedidos de contato, com cifragem transparente.

Um `contato` é uma **alegação** feita por um desconhecido na internet; um
`hospede` é um **registro verificado** de alguém com quem a hospedaria
transacionou. A diferença não está em quem é a pessoa — frequentemente é
a mesma —, e sim em quanto o sistema sabe. Entre os dois há um telefonema.

É por isso que são tabelas separadas, ligadas por `contato.hospede_id`
depois que a recepção converte o pedido. A decisão está registrada na
Etapa 4 de `docs/contexto.md`, com o raciocínio completo; em resumo:
`hospede` exige documento, e documento não se pede a um estranho que só
quer saber se há vaga no feriado.

**Por que isto aqui é cifrado.** A primeira versão da tabela guardava
nome, e-mail, telefone e mensagem em texto simples, com o argumento de
que pedido de contato é dado de menor sensibilidade. O argumento não se
sustenta: quem obtiver acesso de leitura ao banco não vai achar a
distinção relevante, e o nome e o telefone são os mesmos que estariam em
`hospede`. Decidido em 06/11: tudo o que a hospedaria obtém de uma pessoa
nasce cifrado, sem exceção por categoria.

**O que não é cifrado, e por quê.** Chalé de interesse, datas desejadas,
origem, situação e endereço de origem ficam em claro. Nenhum identifica
uma pessoa por si: são dados da *solicitação*, não de quem a fez. Mantê-
los legíveis permite que a tela de atendimento filtre e ordene sem
decifrar a tabela inteira, e que a limitação de taxa conte envios por
origem com um índice.

**A transação em duas fases** é a mesma de `hospede_repo`, e pelo mesmo
motivo: o dado adicional autenticado inclui o identificador da linha, que
só existe depois do `INSERT`. Grava-se com as colunas cifradas vazias,
obtém-se o `lastrowid`, cifra-se com `aad_contato(id)` e atualiza-se —
tudo dentro de uma transação, de modo que uma falha no meio não deixe
registro ilegível no banco.
"""

import datetime as dt
import re

import mysql.connector

from app.dados.conexao import obter_conexao, obter_cursor
from app.seguranca import cripto

# Campos pessoais, na ordem em que aparecem no formulário.
OBRIGATORIOS = ("nome", "email")
OPCIONAIS = ("telefone", "mensagem")
CAMPOS = OBRIGATORIOS + OPCIONAIS

# Limite de cada campo em caracteres, imposto antes de cifrar. O
# ciphertext do AES-GCM tem o mesmo tamanho do texto claro, então estes
# limites são o que protege as colunas VARBINARY de estourar.
#
# A mensagem aceita mil caracteres: o suficiente para uma pergunta sobre
# disponibilidade, e pouco o bastante para que um envio automatizado não
# use o formulário como depósito de texto.
LIMITES = {"nome": 150, "email": 150, "telefone": 40, "mensagem": 1000}

SITUACOES = ("novo", "em_atendimento", "respondido", "convertido", "descartado")

# De onde se pode ir para onde. `convertido` e `descartado` são terminais:
# de um pedido que virou hóspede não se volta, e um descartado que
# precisasse de atenção seria reaberto como pedido novo.
TRANSICOES = {
    "novo": ("em_atendimento", "respondido", "descartado"),
    "em_atendimento": ("respondido", "convertido", "descartado"),
    "respondido": ("convertido", "em_atendimento", "descartado"),
    "convertido": (),
    "descartado": (),
}

VERSAO_DA_CRIPTOGRAFIA = 1

# Validação de e-mail deliberadamente frouxa: exige uma arroba com algo
# de cada lado e um ponto no domínio. A especificação completa de endereço
# de e-mail é notoriamente intrincada, e uma expressão que tente cobri-la
# recusa endereços válidos — o que, num formulário público, significa
# perder um pedido de reserva real. Quem escreveu errado não recebe
# resposta, e isso é problema de quem escreveu.
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class ErroDeContato(Exception):
    """Dado de contato inválido, recusado antes de chegar ao banco."""


_SQL_INSERIR = (
    "INSERT INTO contato "
    "  (nome_cif, nome_nonce, nome_tag, "
    "   email_cif, email_nonce, email_tag, "
    "   email_busca, chale_interesse_id, "
    "   data_entrada_desejada, data_saida_desejada, "
    "   origem, ip_origem, situacao, criado_em) "
    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'novo', NOW())"
)

_SQL_GRAVAR_CIFRADOS = (
    "UPDATE contato SET "
    "  nome_cif = %s, nome_nonce = %s, nome_tag = %s, "
    "  email_cif = %s, email_nonce = %s, email_tag = %s, "
    "  telefone_cif = %s, telefone_nonce = %s, telefone_tag = %s, "
    "  mensagem_cif = %s, mensagem_nonce = %s, mensagem_tag = %s, "
    "  cripto_versao = %s "
    "WHERE id = %s"
)

# As consultas abaixo são escritas por extenso, sem f-string e sem
# concatenação. É regra do projeto desde a Etapa 2, e a razão é que o
# Semgrep deixou passar uma injeção de SQL deliberada que o Bandit e os
# testes pegaram: a análise estática não distingue uma f-string que
# interpola constante de módulo de outra que interpola entrada de usuário,
# e quem revisa o código também não, de relance.
#
# A primeira versão deste módulo montava estas consultas com f-string
# sobre uma lista de colunas. Não era injetável — as colunas são
# constantes —, mas o Bandit apontou as três como B608 de severidade
# média, e estava certo em apontar: o formato é o mesmo do defeito.

_COLUNAS_DO_CONTATO = (
    "id, nome_cif, nome_nonce, nome_tag, "
    "email_cif, email_nonce, email_tag, "
    "telefone_cif, telefone_nonce, telefone_tag, "
    "mensagem_cif, mensagem_nonce, mensagem_tag, "
    "chale_interesse_id, data_entrada_desejada, data_saida_desejada, "
    "origem, situacao, hospede_id, criado_em"
)

_SQL_POR_ID = (
    "SELECT id, nome_cif, nome_nonce, nome_tag, "
    "       email_cif, email_nonce, email_tag, "
    "       telefone_cif, telefone_nonce, telefone_tag, "
    "       mensagem_cif, mensagem_nonce, mensagem_tag, "
    "       chale_interesse_id, data_entrada_desejada, data_saida_desejada, "
    "       origem, situacao, hospede_id, criado_em "
    "FROM contato WHERE id = %s"
)

# As listagens juntam `chale` para trazer o nome do chalé de interesse, e
# as duas tabelas têm `id`. Sem o prefixo `ct.`, o MySQL recusa por
# ambiguidade — e recusa na execução, não na escrita, o que faz o defeito
# aparecer só quando a tela é aberta.
_SQL_LISTAR = (
    "SELECT ct.id, ct.nome_cif, ct.nome_nonce, ct.nome_tag, "
    "       ct.email_cif, ct.email_nonce, ct.email_tag, "
    "       ct.telefone_cif, ct.telefone_nonce, ct.telefone_tag, "
    "       ct.mensagem_cif, ct.mensagem_nonce, ct.mensagem_tag, "
    "       ct.chale_interesse_id, ct.data_entrada_desejada, "
    "       ct.data_saida_desejada, ct.origem, ct.situacao, "
    "       ct.hospede_id, ct.criado_em, "
    "       c.nome AS chale_nome "
    "FROM contato ct "
    "LEFT JOIN chale c ON c.id = ct.chale_interesse_id "
    "ORDER BY ct.criado_em DESC, ct.id DESC "
    "LIMIT %s"
)

_SQL_LISTAR_POR_SITUACAO = (
    "SELECT ct.id, ct.nome_cif, ct.nome_nonce, ct.nome_tag, "
    "       ct.email_cif, ct.email_nonce, ct.email_tag, "
    "       ct.telefone_cif, ct.telefone_nonce, ct.telefone_tag, "
    "       ct.mensagem_cif, ct.mensagem_nonce, ct.mensagem_tag, "
    "       ct.chale_interesse_id, ct.data_entrada_desejada, "
    "       ct.data_saida_desejada, ct.origem, ct.situacao, "
    "       ct.hospede_id, ct.criado_em, "
    "       c.nome AS chale_nome "
    "FROM contato ct "
    "LEFT JOIN chale c ON c.id = ct.chale_interesse_id "
    "WHERE ct.situacao = %s "
    "ORDER BY ct.criado_em DESC, ct.id DESC "
    "LIMIT %s"
)

_SQL_CONTAR_POR_SITUACAO = (
    "SELECT situacao, COUNT(*) AS quantos FROM contato GROUP BY situacao"
)

_SQL_CONTAR_DA_MESMA_PESSOA = (
    "SELECT COUNT(*) FROM contato WHERE email_busca = %s AND id <> %s"
)

_SQL_MUDAR_SITUACAO = "UPDATE contato SET situacao = %s WHERE id = %s"

_SQL_LIGAR_HOSPEDE = (
    "UPDATE contato SET hospede_id = %s, situacao = 'convertido' WHERE id = %s"
)


# ---------------------------------------------------------------------------
# Validação
# ---------------------------------------------------------------------------

def _limpar(dados: dict) -> dict:
    """Valida e normaliza no servidor.

    O que o HTML valida não vale nada: `required` e `maxlength` são
    conveniência para quem preenche, e não chegam a existir para quem
    envia a requisição por fora do navegador — que é exatamente o que um
    envio automatizado faz.
    """
    limpo = {}

    for campo in CAMPOS:
        valor = (dados.get(campo) or "").strip()

        if not valor:
            if campo in OBRIGATORIOS:
                raise ErroDeContato(f"O campo {campo} é obrigatório.")
            limpo[campo] = None
            continue

        if len(valor) > LIMITES[campo]:
            raise ErroDeContato(
                f"O campo {campo} aceita no máximo {LIMITES[campo]} "
                f"caracteres."
            )

        limpo[campo] = valor

    if not _EMAIL.match(limpo["email"]):
        raise ErroDeContato("Informe um e-mail válido.")

    limpo["chale_interesse_id"] = _inteiro_ou_nulo(dados.get("chale_interesse_id"))
    limpo["origem"] = (dados.get("origem") or "site")[:50]

    entrada = _data_ou_nula(dados.get("data_entrada_desejada"), "entrada")
    saida = _data_ou_nula(dados.get("data_saida_desejada"), "saída")

    # As datas são um desejo, não uma reserva: podem vir vazias, e vir
    # vazias é o caso comum de quem só quer saber o preço. O que não se
    # aceita é um par incoerente, que a recepção teria de perguntar de
    # novo.
    if entrada and saida and saida <= entrada:
        raise ErroDeContato(
            "A data de saída precisa ser posterior à data de entrada."
        )

    limpo["data_entrada_desejada"] = entrada
    limpo["data_saida_desejada"] = saida
    return limpo


def _inteiro_ou_nulo(valor):
    if valor in (None, "", "nenhum"):
        return None
    try:
        numero = int(valor)
    except (TypeError, ValueError):
        raise ErroDeContato("Chalé de interesse inválido.") from None
    return numero if numero > 0 else None


def _data_ou_nula(valor, rotulo: str):
    if not valor:
        return None
    if isinstance(valor, dt.date):
        return valor
    try:
        return dt.date.fromisoformat(str(valor).strip())
    except ValueError:
        raise ErroDeContato(
            f"A data de {rotulo} precisa estar no formato AAAA-MM-DD."
        ) from None


# ---------------------------------------------------------------------------
# Cifragem
# ---------------------------------------------------------------------------

def _cifrar_campos(limpo: dict, identificador) -> list:
    """Cifra os quatro campos pessoais com o identificador como etiqueta."""
    aad = cripto.aad_contato(identificador)
    valores = []

    for campo in CAMPOS:
        texto = limpo.get(campo)
        if texto is None:
            valores.extend([None, None, None])
        else:
            valores.extend(cripto.cifrar(texto, aad))

    return valores


def _decifrar(linha: dict, tolerante: bool = False) -> dict:
    """Devolve a linha com os campos pessoais legíveis.

    `tolerante` existe para a listagem: um registro que não passa na
    verificação de integridade aparece marcado, em vez de derrubar a tela
    inteira. Na leitura de um registro só, a falha sobe — ali ela precisa
    ser tratada, não escondida.
    """
    aberto = dict(linha)
    aad = cripto.aad_contato(linha["id"])
    aberto["legivel"] = True

    for campo in CAMPOS:
        ciphertext = linha.get(f"{campo}_cif")
        nonce = linha.get(f"{campo}_nonce")
        tag = linha.get(f"{campo}_tag")

        if ciphertext is None:
            aberto[campo] = None
            continue

        try:
            aberto[campo] = cripto.decifrar(ciphertext, nonce, tag, aad)
        except cripto.FalhaDeAutenticacao:
            if not tolerante:
                raise
            aberto[campo] = None
            aberto["legivel"] = False

    for campo in CAMPOS:
        for sufixo in ("_cif", "_nonce", "_tag"):
            aberto.pop(f"{campo}{sufixo}", None)

    return aberto


# ---------------------------------------------------------------------------
# Escrita
# ---------------------------------------------------------------------------

def criar(dados: dict, ip_origem: str = None, usuario_id=None) -> int:
    """Grava um pedido de contato. Devolve o identificador.

    `usuario_id` é None no caso normal: quem envia o formulário público é
    um desconhecido, e não há usuário a quem atribuir a operação no log
    de auditoria. É o único ponto do sistema em que isso é esperado.
    """
    limpo = _limpar(dados)
    email_busca = cripto.indice(limpo["email"])

    with obter_conexao(usuario_id=usuario_id) as conexao:
        cursor = conexao.cursor()
        try:
            # Primeira fase: a linha nasce com os campos pessoais vazios,
            # só para obter o identificador que a etiqueta precisa.
            cursor.execute(
                _SQL_INSERIR,
                (
                    b"", b"", b"",          # nome
                    b"", b"", b"",          # email
                    email_busca,
                    limpo["chale_interesse_id"],
                    limpo["data_entrada_desejada"],
                    limpo["data_saida_desejada"],
                    limpo["origem"],
                    _ip_binario(ip_origem),
                ),
            )
            identificador = cursor.lastrowid

            # Segunda fase: agora a etiqueta existe.
            cursor.execute(
                _SQL_GRAVAR_CIFRADOS,
                (
                    *_cifrar_campos(limpo, identificador),
                    VERSAO_DA_CRIPTOGRAFIA,
                    identificador,
                ),
            )
        except mysql.connector.Error as erro:
            if erro.errno == 1452:
                raise ErroDeContato(
                    "O chalé de interesse informado não existe."
                ) from erro
            raise
        finally:
            cursor.close()

    return identificador


def _ip_binario(ip_origem):
    """Converte o endereço para a forma binária de 4 ou 16 bytes.

    Devolve None para endereço ausente ou malformado, em vez de recusar o
    envio: a origem serve à limitação de taxa e ao relatório, e perder um
    pedido de reserva real porque o cabeçalho veio estranho seria o
    prejuízo maior.
    """
    if not ip_origem:
        return None
    import ipaddress

    try:
        return ipaddress.ip_address(str(ip_origem).strip()).packed
    except ValueError:
        return None


def atualizar(identificador, dados: dict, usuario_id=None) -> None:
    """Regrava os campos pessoais de um pedido existente.

    Aqui não há caso de borda: o identificador já existe, então a
    cifragem acontece de uma vez só.

    Serve à correção de um dado digitado errado e ao script que substitui
    a massa fictícia por registros cifrados de verdade.
    """
    limpo = _limpar(dados)

    with obter_conexao(usuario_id=usuario_id) as conexao_aberta:
        cursor = conexao_aberta.cursor()
        try:
            cursor.execute(
                "UPDATE contato SET email_busca = %s WHERE id = %s",
                (cripto.indice(limpo["email"]), identificador),
            )
            cursor.execute(
                _SQL_GRAVAR_CIFRADOS,
                (
                    *_cifrar_campos(limpo, identificador),
                    VERSAO_DA_CRIPTOGRAFIA,
                    identificador,
                ),
            )
        finally:
            cursor.close()


def mudar_situacao(identificador, nova_situacao: str, usuario_id=None) -> None:
    """Move o pedido no ciclo de vida, respeitando as transições."""
    if nova_situacao not in SITUACOES:
        raise ErroDeContato("Situação de contato inválida.")

    atual = buscar_por_id(identificador, usuario_id=usuario_id, tolerante=True)
    if atual is None:
        raise ErroDeContato("O pedido de contato informado não existe.")

    if nova_situacao not in TRANSICOES[atual["situacao"]]:
        raise ErroDeContato(
            f"Um pedido {atual['situacao']} não passa a {nova_situacao}."
        )

    with obter_cursor(usuario_id=usuario_id) as cursor:
        cursor.execute(_SQL_MUDAR_SITUACAO, (nova_situacao, identificador))


def ligar_ao_hospede(identificador, hospede_id, usuario_id=None) -> None:
    """Registra que este pedido virou um cadastro de hóspede.

    O contato NÃO é apagado nem movido: ele permanece como registro de
    origem, e é o que permite responder de onde vieram os hóspedes de um
    período, e quantos pedidos viraram estadia.
    """
    with obter_cursor(usuario_id=usuario_id) as cursor:
        cursor.execute(_SQL_LIGAR_HOSPEDE, (hospede_id, identificador))


# ---------------------------------------------------------------------------
# Leitura
# ---------------------------------------------------------------------------

def buscar_por_id(identificador, usuario_id=None, tolerante: bool = False):
    with obter_cursor(usuario_id=usuario_id, dicionario=True) as cursor:
        cursor.execute(_SQL_POR_ID, (identificador,))
        linha = cursor.fetchone()

    return None if linha is None else _decifrar(linha, tolerante=tolerante)


def listar(situacao: str = None, limite: int = 200, usuario_id=None) -> list:
    """Lista para a tela de atendimento, da mais recente para a mais antiga.

    Tolerante por projeto: um registro ilegível aparece marcado com
    `legivel=False`, em vez de derrubar a tela. A massa fictícia tem bytes
    aleatórios nas colunas cifradas, e a tela precisa sobreviver a ela.
    """
    with obter_cursor(usuario_id=usuario_id, dicionario=True) as cursor:
        if situacao:
            if situacao not in SITUACOES:
                raise ErroDeContato("Situação de contato inválida.")
            cursor.execute(_SQL_LISTAR_POR_SITUACAO, (situacao, limite))
        else:
            cursor.execute(_SQL_LISTAR, (limite,))
        linhas = cursor.fetchall()

    return [_decifrar(linha, tolerante=True) for linha in linhas]


def contar_por_situacao(usuario_id=None) -> dict:
    with obter_cursor(usuario_id=usuario_id, dicionario=True) as cursor:
        cursor.execute(_SQL_CONTAR_POR_SITUACAO)
        return {linha["situacao"]: linha["quantos"] for linha in cursor.fetchall()}


def contatos_anteriores(identificador, email: str, usuario_id=None) -> int:
    """Quantas outras vezes esta pessoa já escreveu.

    Serve à recepção, que atende melhor sabendo se é a primeira vez, e
    serve como sinal de abuso quando o número é alto. Usa o índice HMAC:
    o campo cifrado não é pesquisável, porque o AES-GCM é não
    determinístico e cifrar o mesmo e-mail duas vezes produz bytes
    diferentes.
    """
    with obter_cursor(usuario_id=usuario_id) as cursor:
        cursor.execute(
            _SQL_CONTAR_DA_MESMA_PESSOA, (cripto.indice(email), identificador)
        )
        return cursor.fetchone()[0]
