"""Cifragem dos dados pessoais do hóspede e índice de busca.

Este módulo resolve dois problemas que andam juntos.

O primeiro é proteger nome, documento, telefone e e-mail do hóspede, de
modo que quem obtenha acesso ao banco — por cópia de backup, por falha da
aplicação ou por acesso direto ao servidor — não consiga ler nada sem a
chave. A resposta é AES-256-GCM.

O segundo aparece por consequência do primeiro: o AES-GCM é NÃO
determinístico por projeto, o que significa que cifrar duas vezes o mesmo
e-mail produz bytes diferentes. Isso é desejável — impede que alguém
descubra, só olhando o banco, que duas linhas guardam o mesmo valor — mas
destrói a possibilidade de `WHERE email_cif = ?` e de restrição de
unicidade. A resposta é guardar ao lado um HMAC-SHA256 do valor
normalizado, com chave separada.

    cifrar(texto, aad)                    -> (ciphertext, nonce, tag)
    decifrar(ciphertext, nonce, tag, aad) -> texto
    indice(valor)                         -> 32 bytes determinísticos

As três chaves vêm de variável de ambiente e nunca aparecem no código.
Quem carrega o arquivo .env é o ponto de entrada da aplicação, não este
módulo: aqui apenas se lê `os.environ`.
"""

import base64
import hashlib
import hmac
import os
import secrets
import unicodedata

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

# 32 bytes = 256 bits, exigido pelo AES-256 e tamanho ideal para a chave de
# um HMAC-SHA256, cujo bloco interno tem 64 bytes.
TAMANHO_CHAVE = 32

# 96 bits é o tamanho de nonce recomendado pelo NIST para o GCM. Com esse
# tamanho o algoritmo usa o valor diretamente como contador inicial; com
# qualquer outro, precisa derivá-lo por GHASH, o que é mais lento e menos
# estudado.
TAMANHO_NONCE = 12

# 128 bits é a tag completa do GCM. Truncá-la reduziria proporcionalmente a
# dificuldade de forjar uma mensagem válida.
TAMANHO_TAG = 16

VARIAVEL_CIFRAGEM = "VILLAJU_CHAVE_CIFRAGEM"
VARIAVEL_INDICE = "VILLAJU_CHAVE_INDICE"


class ErroDeCriptografia(Exception):
    """Falha ao cifrar, decifrar ou derivar índice."""


class ChaveAusente(ErroDeCriptografia):
    """A variável de ambiente com a chave não está definida ou é inválida."""


class FalhaDeAutenticacao(ErroDeCriptografia):
    """O dado cifrado não passou na verificação de integridade.

    Significa uma destas três coisas, e o GCM não distingue entre elas:
    o ciphertext foi alterado, a tag foi alterada, ou o dado adicional
    autenticado informado não é o mesmo usado na cifragem.

    Em qualquer dos casos o texto decifrado NÃO deve ser usado. É por isso
    que esta exceção existe em vez de um retorno nulo: ignorar um valor de
    retorno é fácil, ignorar uma exceção não.
    """


# ---------------------------------------------------------------------------
# Chaves
# ---------------------------------------------------------------------------

def _ler_chave(variavel: str) -> bytes:
    """Lê uma chave de 32 bytes em base64 a partir do ambiente.

    Falha ruidosamente em vez de recorrer a um valor padrão. Uma chave
    ausente que virasse constante embutida no código produziria um sistema
    que funciona, passa nos testes, e cifra tudo com uma chave pública.
    """
    valor = os.environ.get(variavel)
    if not valor:
        raise ChaveAusente(
            f"{variavel} não está definida no ambiente. "
            f"Gere as chaves com: python scripts/gerar_chaves.py"
        )

    try:
        bruta = base64.b64decode(valor, validate=True)
    except Exception as erro:
        raise ChaveAusente(
            f"{variavel} não é base64 válido: {erro}"
        ) from erro

    if len(bruta) != TAMANHO_CHAVE:
        raise ChaveAusente(
            f"{variavel} tem {len(bruta)} bytes; são exigidos {TAMANHO_CHAVE}"
        )

    return bruta


def _chave_de_cifragem() -> bytes:
    return _ler_chave(VARIAVEL_CIFRAGEM)


def _chave_de_indice() -> bytes:
    return _ler_chave(VARIAVEL_INDICE)


# ---------------------------------------------------------------------------
# Dado adicional autenticado
# ---------------------------------------------------------------------------

def _normalizar_aad(aad) -> bytes:
    """Converte o dado adicional autenticado para bytes.

    Aceita texto, bytes ou inteiro por conveniência de quem chama: o
    identificador do hóspede chega do banco como inteiro, mas em outros
    pontos é mais natural escrever uma etiqueta legível.
    """
    if isinstance(aad, bytes):
        return aad
    if isinstance(aad, int):
        return str(aad).encode("utf-8")
    if isinstance(aad, str):
        return aad.encode("utf-8")
    raise ErroDeCriptografia(
        f"dado adicional autenticado precisa ser str, bytes ou int, "
        f"não {type(aad).__name__}"
    )


def aad_hospede(identificador) -> bytes:
    """Etiqueta que amarra um registro cifrado ao hóspede a que pertence.

    Sem esse vínculo, quem tivesse acesso de escrita ao banco poderia
    copiar o bloco cifrado de um hóspede para a linha de outro, e a
    decifragem funcionaria normalmente — o sistema passaria a exibir os
    dados da pessoa errada sem que nada falhasse.

    Com o identificador como dado adicional autenticado, mover o bloco faz
    a verificação de integridade falhar.
    """
    return f"hospede:{identificador}".encode("utf-8")


def aad_contato(identificador) -> bytes:
    """A mesma etiqueta, para os pedidos de contato.

    O prefixo não é decoração. Sem ele, `hospede:7` e `contato:7` teriam
    o mesmo dado adicional, e um bloco cifrado poderia ser movido de uma
    tabela para a outra sem que a verificação de integridade reclamasse —
    o nome de um hóspede apareceria como o nome de quem pediu informação,
    ou o contrário.

    Com o prefixo, o vínculo é com a linha *daquela* tabela.
    """
    return f"contato:{identificador}".encode("utf-8")


# ---------------------------------------------------------------------------
# Cifragem
# ---------------------------------------------------------------------------

def cifrar(texto: str, aad) -> tuple:
    """Cifra um texto com AES-256-GCM.

    Devolve (ciphertext, nonce, tag), que é como as três partes são
    guardadas: em colunas separadas da tabela hospede.

    O nonce é sorteado a cada chamada e JAMAIS pode se repetir com a mesma
    chave. A reutilização de nonce no GCM é catastrófica: permite recuperar
    o texto claro de ambas as mensagens e, pior, forjar mensagens válidas.
    Por isso ele vem de `secrets.token_bytes`, não de `random`.

    Com 96 bits sorteados uniformemente, a probabilidade de colisão só se
    torna relevante na casa dos bilhões de cifragens com a mesma chave —
    muito além do que este sistema produzirá.
    """
    if not isinstance(texto, str):
        raise ErroDeCriptografia(
            f"o texto a cifrar precisa ser str, não {type(texto).__name__}"
        )

    nonce = secrets.token_bytes(TAMANHO_NONCE)

    # A biblioteca devolve ciphertext e tag concatenados, nessa ordem.
    # O modelo de dados os guarda separados, para deixar explícito o que é
    # cada coisa e evitar serializar e desserializar a cada leitura.
    selado = AESGCM(_chave_de_cifragem()).encrypt(
        nonce, texto.encode("utf-8"), _normalizar_aad(aad)
    )

    return selado[:-TAMANHO_TAG], nonce, selado[-TAMANHO_TAG:]


def decifrar(ciphertext: bytes, nonce: bytes, tag: bytes, aad) -> str:
    """Decifra e verifica a integridade. Lança exceção se algo não confere.

    A verificação é obrigatória e acontece ANTES de qualquer uso do texto:
    o GCM só devolve o conteúdo depois de confirmar a tag. É essa
    propriedade que distingue uma cifragem autenticada de uma cifragem
    comum, e é a razão de o projeto usar GCM em vez de CBC.
    """
    if len(nonce) != TAMANHO_NONCE:
        raise ErroDeCriptografia(
            f"nonce tem {len(nonce)} bytes; são exigidos {TAMANHO_NONCE}"
        )
    if len(tag) != TAMANHO_TAG:
        raise ErroDeCriptografia(
            f"tag tem {len(tag)} bytes; são exigidos {TAMANHO_TAG}"
        )

    try:
        aberto = AESGCM(_chave_de_cifragem()).decrypt(
            nonce, bytes(ciphertext) + bytes(tag), _normalizar_aad(aad)
        )
    except InvalidTag as erro:
        raise FalhaDeAutenticacao(
            "o dado cifrado não passou na verificação de integridade: "
            "ciphertext alterado, tag alterada, ou dado adicional "
            "autenticado incorreto"
        ) from erro

    return aberto.decode("utf-8")


# ---------------------------------------------------------------------------
# Índice de busca
# ---------------------------------------------------------------------------

def normalizar(valor: str) -> str:
    """Põe o valor na forma canônica usada pelo índice.

    Três operações, nesta ordem:

    1. Normalização Unicode NFKC, para que 'José' digitado com acento
       composto e com acento pré-combinado produzam a mesma sequência de
       bytes. Sem isso, duas grafias visualmente idênticas gerariam
       índices diferentes e o hóspede seria cadastrado duas vezes.
    2. Remoção de espaços nas pontas.
    3. Conversão para minúsculas.
    """
    return unicodedata.normalize("NFKC", valor).strip().lower()


def normalizar_documento(valor: str) -> str:
    """Normalização específica de documento: mantém apenas letras e dígitos.

    Um CPF digitado como '123.456.789-00' e como '12345678900' é o mesmo
    documento. Sem esta normalização, a restrição de unicidade seria
    contornada por quem digitasse com pontuação diferente.
    """
    return "".join(c for c in normalizar(valor) if c.isalnum())


def indice(valor: str) -> bytes:
    """Devolve o HMAC-SHA256 do valor normalizado, com a chave de índice.

    Por que HMAC e não hash simples: um CPF tem cerca de 10^11
    possibilidades, e um e-mail vem de um conjunto previsível. Com SHA-256
    puro, qualquer pessoa com acesso ao banco recuperaria os valores por
    força bruta em segundos, porque a função é pública e rápida. O HMAC
    exige a chave, que não está no banco.

    Por que a chave é separada da chave de cifragem: usar o mesmo material
    para dois fins criptográficos distintos amplia o impacto de um
    comprometimento e impede rotação independente.

    Por que isto não contradiz a cifragem: o HMAC é irreversível. Ele
    permite responder "existe algum hóspede com este e-mail?" sem permitir
    responder "qual é o e-mail deste hóspede?".
    """
    if not isinstance(valor, str):
        raise ErroDeCriptografia(
            f"o valor do índice precisa ser str, não {type(valor).__name__}"
        )

    return hmac.new(
        _chave_de_indice(), normalizar(valor).encode("utf-8"), hashlib.sha256
    ).digest()


def indice_documento(valor: str) -> bytes:
    """Índice de documento, com a normalização que ignora pontuação."""
    if not isinstance(valor, str):
        raise ErroDeCriptografia(
            f"o valor do índice precisa ser str, não {type(valor).__name__}"
        )

    return hmac.new(
        _chave_de_indice(),
        normalizar_documento(valor).encode("utf-8"),
        hashlib.sha256,
    ).digest()
