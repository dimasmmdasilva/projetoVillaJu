"""Cifragem dos dados pessoais e índice de busca.

Estes testes cobrem os casos obrigatórios da Etapa 2 e as propriedades que
sustentam a proteção dos dados do hóspede.

O que se verifica não é apenas que a cifragem funciona, e sim que ela
FALHA quando deveria: com ciphertext adulterado, com tag adulterada e com
dado adicional autenticado trocado. Uma cifragem que aceita dado alterado
não protege nada — ela apenas esconde.
"""

import base64
import os
import secrets

import pytest

from app.seguranca import cripto
from app.seguranca.cripto import (
    ChaveAusente,
    ErroDeCriptografia,
    FalhaDeAutenticacao,
    aad_hospede,
    cifrar,
    decifrar,
    indice,
    indice_documento,
    normalizar,
    normalizar_documento,
)

AAD = aad_hospede(42)


# ---------------------------------------------------------------------------
# Ida e volta
# ---------------------------------------------------------------------------

TEXTOS = [
    pytest.param("João da Silva", id="acentos"),
    pytest.param("MARIA JOSÉ DE SOUZA", id="maiusculas-com-acento"),
    pytest.param("123.456.789-00", id="documento"),
    pytest.param("contato@villaju.com.br", id="email"),
    pytest.param("(12) 99999-8888", id="telefone"),
    pytest.param("", id="string-vazia"),
    pytest.param(" ", id="apenas-espaco"),
    pytest.param("Família Gonçalves 🏖️🌴", id="emoji"),
    pytest.param("Ação, coração e não", id="cedilha-e-til"),
    pytest.param("x" * 400, id="texto-longo"),
    pytest.param("linha um\nlinha dois\ttabulado", id="quebras-e-tabulacao"),
    pytest.param("'; DROP TABLE hospede; --", id="tentativa-de-injecao"),
]


@pytest.mark.parametrize("texto", TEXTOS)
def test_ida_e_volta_devolve_o_original(texto):
    """Cifrar e decifrar precisa devolver exatamente o que entrou."""
    ciphertext, nonce, tag = cifrar(texto, AAD)
    assert decifrar(ciphertext, nonce, tag, AAD) == texto


@pytest.mark.parametrize("texto", TEXTOS)
def test_ciphertext_nao_contem_o_texto_claro(texto):
    """Verificação elementar: o original não pode aparecer no cifrado."""
    if not texto.strip():
        pytest.skip("texto vazio não tem conteúdo a vazar")

    ciphertext, _, _ = cifrar(texto, AAD)
    assert texto.encode("utf-8") not in ciphertext


def test_tamanhos_do_nonce_e_da_tag():
    """96 bits de nonce e 128 de tag, conforme a seção 6.2 do plano."""
    _, nonce, tag = cifrar("qualquer coisa", AAD)
    assert len(nonce) == 12
    assert len(tag) == 16


def test_ciphertext_tem_o_tamanho_do_texto():
    """O GCM é cifra de fluxo: não há preenchimento, o tamanho é o mesmo.

    É por isso que o tamanho do campo cifrado revela o tamanho do nome do
    hóspede. Esse vazamento é conhecido e aceito: proteger contra ele
    exigiria preenchimento de tamanho fixo, que o plano não prevê.
    """
    texto = "Joana"
    ciphertext, _, _ = cifrar(texto, AAD)
    assert len(ciphertext) == len(texto.encode("utf-8"))


# ---------------------------------------------------------------------------
# Detecção de adulteração
# ---------------------------------------------------------------------------

def test_ciphertext_adulterado_em_um_bit_e_rejeitado():
    """Um único bit invertido precisa derrubar a verificação."""
    ciphertext, nonce, tag = cifrar("Documento do hóspede", AAD)

    adulterado = bytearray(ciphertext)
    adulterado[0] ^= 0b00000001

    with pytest.raises(FalhaDeAutenticacao):
        decifrar(bytes(adulterado), nonce, tag, AAD)


@pytest.mark.parametrize("posicao", [0, 5, -1])
def test_ciphertext_adulterado_em_qualquer_posicao_e_rejeitado(posicao):
    ciphertext, nonce, tag = cifrar("Texto com tamanho suficiente", AAD)

    adulterado = bytearray(ciphertext)
    adulterado[posicao] ^= 0xFF

    with pytest.raises(FalhaDeAutenticacao):
        decifrar(bytes(adulterado), nonce, tag, AAD)


def test_tag_adulterada_e_rejeitada():
    ciphertext, nonce, tag = cifrar("Documento do hóspede", AAD)

    adulterada = bytearray(tag)
    adulterada[0] ^= 0b00000001

    with pytest.raises(FalhaDeAutenticacao):
        decifrar(ciphertext, nonce, bytes(adulterada), AAD)


def test_nonce_trocado_e_rejeitado():
    ciphertext, _, tag = cifrar("Documento do hóspede", AAD)
    outro_nonce = secrets.token_bytes(12)

    with pytest.raises(FalhaDeAutenticacao):
        decifrar(ciphertext, outro_nonce, tag, AAD)


def test_aad_errado_e_rejeitado():
    """O caso que impede mover um registro cifrado para outro hóspede.

    Sem o dado adicional autenticado, quem tivesse acesso de escrita ao
    banco copiaria o bloco cifrado da linha de um hóspede para a de outro,
    e a decifragem funcionaria — o sistema exibiria os dados da pessoa
    errada sem que nada falhasse.
    """
    ciphertext, nonce, tag = cifrar("Dados do hóspede 42", aad_hospede(42))

    with pytest.raises(FalhaDeAutenticacao):
        decifrar(ciphertext, nonce, tag, aad_hospede(99))


def test_aad_ausente_quando_houve_aad_e_rejeitado():
    ciphertext, nonce, tag = cifrar("Dados do hóspede", aad_hospede(42))

    with pytest.raises(FalhaDeAutenticacao):
        decifrar(ciphertext, nonce, tag, b"")


def test_ciphertext_de_outra_chave_e_rejeitado():
    """Trocar a chave de cifragem invalida tudo o que já foi gravado.

    É a consequência aceita do modelo: perder a chave torna os dados
    irrecuperáveis. Este teste documenta esse comportamento.
    """
    ciphertext, nonce, tag = cifrar("Dados do hóspede", AAD)

    original = os.environ[cripto.VARIAVEL_CIFRAGEM]
    os.environ[cripto.VARIAVEL_CIFRAGEM] = base64.b64encode(
        secrets.token_bytes(32)
    ).decode("ascii")
    try:
        with pytest.raises(FalhaDeAutenticacao):
            decifrar(ciphertext, nonce, tag, AAD)
    finally:
        os.environ[cripto.VARIAVEL_CIFRAGEM] = original


# ---------------------------------------------------------------------------
# Unicidade do nonce
# ---------------------------------------------------------------------------

def test_duas_cifragens_do_mesmo_texto_usam_nonces_diferentes():
    _, nonce_a, _ = cifrar("mesmo texto", AAD)
    _, nonce_b, _ = cifrar("mesmo texto", AAD)
    assert nonce_a != nonce_b


def test_duas_cifragens_do_mesmo_texto_produzem_ciphertexts_diferentes():
    """A cifragem é não determinística por projeto.

    Se fosse determinística, bastaria olhar o banco para descobrir que
    duas linhas guardam o mesmo valor — o que, num cadastro de hóspedes,
    revelaria relações entre registros sem precisar de chave alguma.
    """
    primeiro, _, _ = cifrar("mesmo texto", AAD)
    segundo, _, _ = cifrar("mesmo texto", AAD)
    assert primeiro != segundo


def test_dez_mil_cifragens_sem_repetir_nonce():
    """Caso obrigatório do plano.

    A reutilização de nonce no GCM é catastrófica: permite recuperar o
    texto claro de ambas as mensagens cifradas com o mesmo par chave-nonce
    e forjar mensagens válidas. Com 96 bits sorteados, uma colisão em dez
    mil é praticamente impossível — este teste detectaria um gerador
    quebrado, com semente fixa ou com espaço reduzido.
    """
    nonces = {cifrar("texto", AAD)[1] for _ in range(10_000)}
    assert len(nonces) == 10_000


def test_gerador_de_nonce_nao_usa_random():
    """`random` é pseudoaleatório previsível, impróprio aqui."""
    codigo = (
        cripto.__file__.replace("\\", "/")
    )
    with open(codigo, encoding="utf-8") as arquivo:
        fonte = arquivo.read()

    assert "import random" not in fonte
    assert "random." not in fonte
    assert "secrets.token_bytes" in fonte


# ---------------------------------------------------------------------------
# Índice de busca
# ---------------------------------------------------------------------------

def test_indice_e_deterministico():
    """Sem determinismo não há busca nem restrição de unicidade."""
    assert indice("joao@exemplo.com") == indice("joao@exemplo.com")


def test_indice_normaliza_espacos_e_maiusculas():
    """Caso obrigatório do plano."""
    assert indice(" Joao@EXEMPLO.com ") == indice("joao@exemplo.com")


def test_indice_normaliza_acento_composto():
    """'é' pré-combinado e 'e' + acento combinante são o mesmo caractere.

    Sem normalização Unicode, duas grafias visualmente idênticas gerariam
    índices diferentes e o mesmo hóspede seria cadastrado duas vezes.
    """
    precombinado = "josé@exemplo.com"
    combinante = "josé@exemplo.com"
    assert precombinado != combinante
    assert indice(precombinado) == indice(combinante)


def test_indice_distingue_valores_diferentes():
    assert indice("joao@exemplo.com") != indice("maria@exemplo.com")


def test_indice_tem_32_bytes():
    """O tamanho exato da saída do SHA-256, que a coluna espera."""
    assert len(indice("qualquer@coisa.com")) == 32


def test_indice_nao_contem_o_valor_original():
    assert b"joao" not in indice("joao@exemplo.com")


def test_indice_muda_com_a_chave():
    """O HMAC depende da chave; sem ela não é reproduzível.

    É o que impede recuperar um CPF por força bruta a partir do banco:
    quem tem o índice mas não tem a chave não consegue calcular candidatos.
    """
    original_de = indice("joao@exemplo.com")

    anterior = os.environ[cripto.VARIAVEL_INDICE]
    os.environ[cripto.VARIAVEL_INDICE] = base64.b64encode(
        secrets.token_bytes(32)
    ).decode("ascii")
    try:
        assert indice("joao@exemplo.com") != original_de
    finally:
        os.environ[cripto.VARIAVEL_INDICE] = anterior


def test_chave_de_indice_e_diferente_da_de_cifragem():
    """Usar o mesmo material para dois fins impede rotação independente."""
    assert (
        os.environ[cripto.VARIAVEL_CIFRAGEM]
        != os.environ[cripto.VARIAVEL_INDICE]
    )


# ---------------------------------------------------------------------------
# Normalização de documento
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "escrito",
    ["123.456.789-00", "12345678900", " 123 456 789 00 ", "123.456.789/00"],
)
def test_documento_com_pontuacao_produz_o_mesmo_indice(escrito):
    """Um CPF é o mesmo documento, digitado como for.

    Sem esta normalização a restrição de unicidade seria contornada por
    quem digitasse com pontuação diferente, e o mesmo hóspede entraria
    duas vezes no cadastro.
    """
    assert indice_documento(escrito) == indice_documento("12345678900")


def test_documentos_diferentes_produzem_indices_diferentes():
    assert indice_documento("12345678900") != indice_documento("98765432100")


def test_normalizar_documento_descarta_pontuacao():
    assert normalizar_documento("123.456.789-00") == "12345678900"


def test_normalizar_preserva_conteudo_interno():
    """A normalização geral não remove espaços do meio do texto."""
    assert normalizar("  João  da Silva  ") == "joão  da silva"


# ---------------------------------------------------------------------------
# Dado adicional autenticado
# ---------------------------------------------------------------------------

def test_aad_hospede_e_estavel():
    assert aad_hospede(7) == aad_hospede(7)


def test_aad_hospede_distingue_identificadores():
    assert aad_hospede(7) != aad_hospede(8)


def test_aad_hospede_aceita_texto_e_inteiro():
    assert aad_hospede(7) == aad_hospede("7")


@pytest.mark.parametrize("valor", [7, "etiqueta", b"bytes"])
def test_aad_aceita_os_tres_tipos(valor):
    ciphertext, nonce, tag = cifrar("texto", valor)
    assert decifrar(ciphertext, nonce, tag, valor) == "texto"


def test_aad_de_tipo_invalido_e_recusado():
    with pytest.raises(ErroDeCriptografia):
        cifrar("texto", 3.14)


# ---------------------------------------------------------------------------
# Validação de entrada
# ---------------------------------------------------------------------------

def test_cifrar_recusa_bytes():
    """Receber bytes aqui indica confusão de camada, não um uso válido."""
    with pytest.raises(ErroDeCriptografia):
        cifrar(b"ja sao bytes", AAD)


def test_indice_recusa_bytes():
    with pytest.raises(ErroDeCriptografia):
        indice(b"ja sao bytes")


@pytest.mark.parametrize("tamanho", [11, 13, 0])
def test_decifrar_recusa_nonce_de_tamanho_errado(tamanho):
    ciphertext, _, tag = cifrar("texto", AAD)
    with pytest.raises(ErroDeCriptografia):
        decifrar(ciphertext, b"n" * tamanho, tag, AAD)


@pytest.mark.parametrize("tamanho", [15, 17, 0])
def test_decifrar_recusa_tag_de_tamanho_errado(tamanho):
    ciphertext, nonce, _ = cifrar("texto", AAD)
    with pytest.raises(ErroDeCriptografia):
        decifrar(ciphertext, nonce, b"t" * tamanho, AAD)


# ---------------------------------------------------------------------------
# Carregamento das chaves
# ---------------------------------------------------------------------------

@pytest.fixture
def ambiente_restaurado():
    """Devolve as variáveis ao estado original ao fim do teste."""
    guardado = {
        nome: os.environ.get(nome)
        for nome in (cripto.VARIAVEL_CIFRAGEM, cripto.VARIAVEL_INDICE)
    }
    yield
    for nome, valor in guardado.items():
        if valor is None:
            os.environ.pop(nome, None)
        else:
            os.environ[nome] = valor


def test_chave_ausente_falha_ruidosamente(ambiente_restaurado):
    """Parar é mais seguro que recorrer a um valor embutido no código."""
    del os.environ[cripto.VARIAVEL_CIFRAGEM]

    with pytest.raises(ChaveAusente) as erro:
        cifrar("texto", AAD)

    assert cripto.VARIAVEL_CIFRAGEM in str(erro.value)
    assert "gerar_chaves.py" in str(erro.value)


def test_chave_vazia_falha(ambiente_restaurado):
    os.environ[cripto.VARIAVEL_CIFRAGEM] = ""
    with pytest.raises(ChaveAusente):
        cifrar("texto", AAD)


def test_chave_que_nao_e_base64_falha(ambiente_restaurado):
    os.environ[cripto.VARIAVEL_CIFRAGEM] = "isto não é base64!!!"
    with pytest.raises(ChaveAusente):
        cifrar("texto", AAD)


@pytest.mark.parametrize("bytes_da_chave", [16, 24, 31, 33, 64])
def test_chave_de_tamanho_errado_falha(ambiente_restaurado, bytes_da_chave):
    """O AES-256 exige exatamente 32 bytes.

    Uma chave de 16 bytes seria AES-128 — ainda seguro, mas não é o que o
    projeto declara usar. Aceitar em silêncio criaria divergência entre o
    que a documentação afirma e o que o sistema faz.
    """
    os.environ[cripto.VARIAVEL_CIFRAGEM] = base64.b64encode(
        secrets.token_bytes(bytes_da_chave)
    ).decode("ascii")

    with pytest.raises(ChaveAusente) as erro:
        cifrar("texto", AAD)

    assert str(bytes_da_chave) in str(erro.value)


def test_chave_de_indice_ausente_falha(ambiente_restaurado):
    del os.environ[cripto.VARIAVEL_INDICE]

    with pytest.raises(ChaveAusente) as erro:
        indice("joao@exemplo.com")

    assert cripto.VARIAVEL_INDICE in str(erro.value)


# ---------------------------------------------------------------------------
# A etiqueta dos pedidos de contato
# ---------------------------------------------------------------------------

def test_aad_de_contato_difere_da_de_hospede():
    """O prefixo impede mover um bloco cifrado entre as duas tabelas.

    Sem ele, `hospede:7` e `contato:7` teriam o mesmo dado adicional
    autenticado, e quem tivesse escrita no banco poderia copiar o nome
    cifrado de um hóspede para a linha de um contato — a decifragem
    funcionaria, e o sistema exibiria a pessoa errada sem que nada
    falhasse.
    """
    assert cripto.aad_contato(7) != cripto.aad_hospede(7)
    assert cripto.aad_contato(7) == b"contato:7"


def test_bloco_de_hospede_nao_decifra_como_contato():
    """A proteção, exercitada de ponta a ponta."""
    ciphertext, nonce, tag = cripto.cifrar(
        "Mariana Alves", cripto.aad_hospede(7)
    )

    # Com a etiqueta certa, lê.
    assert cripto.decifrar(ciphertext, nonce, tag, cripto.aad_hospede(7)) == (
        "Mariana Alves"
    )

    # Movido para a linha 7 de `contato`, não lê.
    with pytest.raises(cripto.FalhaDeAutenticacao):
        cripto.decifrar(ciphertext, nonce, tag, cripto.aad_contato(7))
