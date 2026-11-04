"""Proteção das senhas da equipe com Argon2id.

Os testes cobrem três grupos de propriedades: que a verificação funciona e
que senha errada é rejeitada; que o hash armazenado não revela a senha nem
repete entre usuários; e que os parâmetros calibrados são de fato os que
estão sendo usados.

Argon2id é lento de propósito — cada hash custa cerca de 0,66 s nesta
máquina. Os testes são organizados para reaproveitar hashes sempre que
possível, em vez de gerar um por caso.
"""

import re
import time

import pytest

from app.seguranca import senhas
from app.seguranca.senhas import (
    ErroDeSenha,
    HashInvalido,
    consumir_tempo_equivalente,
    descrever_parametros,
    gerar_hash,
    precisa_recalibrar,
    verificar,
)

SENHA = "Villa@Recepcao2026"


@pytest.fixture(scope="module")
def hash_da_senha():
    """Um único hash para todo o arquivo: gerar custa quase um segundo."""
    return gerar_hash(SENHA)


# ---------------------------------------------------------------------------
# Verificação
# ---------------------------------------------------------------------------

def test_senha_correta_verifica(hash_da_senha):
    assert verificar(hash_da_senha, SENHA) is True


@pytest.mark.parametrize(
    "errada",
    [
        pytest.param("Villa@Recepcao2025", id="um-digito-diferente"),
        pytest.param("villa@recepcao2026", id="caixa-diferente"),
        pytest.param("Villa@Recepcao2026 ", id="espaco-no-fim"),
        pytest.param(" Villa@Recepcao2026", id="espaco-no-inicio"),
        pytest.param("Villa@Recepcao202", id="um-caractere-a-menos"),
        pytest.param("outra-coisa-completamente", id="sem-relacao"),
    ],
)
def test_senha_errada_nao_verifica(hash_da_senha, errada):
    """Nenhuma variação próxima pode passar.

    Os casos de caixa e de espaço importam: um sistema que normalizasse a
    senha antes do hash reduziria silenciosamente o espaço de busca.
    """
    assert verificar(hash_da_senha, errada) is False


def test_senha_errada_devolve_falso_e_nao_levanta(hash_da_senha):
    """Senha incorreta é fluxo normal de login, não situação excepcional."""
    resultado = verificar(hash_da_senha, "errada")
    assert resultado is False


# ---------------------------------------------------------------------------
# O que fica armazenado
# ---------------------------------------------------------------------------

def test_hash_nao_contem_a_senha(hash_da_senha):
    assert SENHA not in hash_da_senha
    assert SENHA.lower() not in hash_da_senha.lower()


def test_hash_nao_contem_a_senha_em_base64(hash_da_senha):
    """Verificação de paranoia: nem codificada a senha pode aparecer."""
    import base64

    codificada = base64.b64encode(SENHA.encode()).decode().rstrip("=")
    assert codificada not in hash_da_senha


def test_dois_hashes_da_mesma_senha_sao_diferentes():
    """O sal é sorteado por senha.

    Sem sal, duas pessoas com a mesma senha teriam o mesmo hash, e uma
    tabela pré-calculada quebraria as duas de uma vez.
    """
    assert gerar_hash("mesma-senha") != gerar_hash("mesma-senha")


def test_ambos_os_hashes_verificam_a_mesma_senha():
    """Sais diferentes, mesma senha: os dois precisam conferir."""
    primeiro = gerar_hash("mesma-senha")
    segundo = gerar_hash("mesma-senha")
    assert verificar(primeiro, "mesma-senha")
    assert verificar(segundo, "mesma-senha")


def test_hash_de_um_usuario_nao_verifica_a_senha_de_outro():
    hash_a = gerar_hash("senha-do-admin")
    assert verificar(hash_a, "senha-da-recepcao") is False


# ---------------------------------------------------------------------------
# Parâmetros calibrados
# ---------------------------------------------------------------------------

def test_hash_usa_a_variante_id(hash_da_senha):
    """Argon2i e Argon2d também existem; o projeto exige a variante id."""
    assert hash_da_senha.startswith("$argon2id$")


def test_hash_carrega_os_parametros_calibrados(hash_da_senha):
    """A string embutida precisa refletir o que o módulo declara.

    É o que permite recalibrar no futuro sem invalidar senhas antigas:
    cada hash sabe com quais parâmetros foi gerado.
    """
    esperado = (
        f"m={senhas.MEMORIA_KIB},"
        f"t={senhas.ITERACOES},"
        f"p={senhas.PARALELISMO}"
    )
    assert esperado in hash_da_senha


def test_memoria_e_de_pelo_menos_64_mib():
    """A RFC 9106 recomenda 64 MiB como piso; o projeto usa 256."""
    assert senhas.MEMORIA_KIB >= 65536


def test_memoria_supera_o_ponto_de_partida_da_rfc():
    """A calibração mediu 0,044 s com 64 MiB e elevou o custo."""
    assert senhas.MEMORIA_KIB > 65536


def test_iteracoes_superam_o_ponto_de_partida_da_rfc():
    assert senhas.ITERACOES >= 3


def test_sal_tem_pelo_menos_128_bits():
    assert senhas.TAMANHO_DO_SAL >= 16


def test_hash_tem_pelo_menos_256_bits():
    assert senhas.TAMANHO_DO_HASH >= 32


def test_descricao_dos_parametros_bate_com_as_constantes():
    """A documentação gerada não pode divergir do que roda."""
    descrito = descrever_parametros()
    assert descrito["variante"] == "Argon2id"
    assert descrito["memoria_kib"] == senhas.MEMORIA_KIB
    assert descrito["iteracoes"] == senhas.ITERACOES
    assert descrito["paralelismo"] == senhas.PARALELISMO


def test_formato_do_hash_e_o_esperado(hash_da_senha):
    """`$argon2id$v=19$m=...,t=...,p=...$<sal>$<hash>`"""
    padrao = r"^\$argon2id\$v=19\$m=\d+,t=\d+,p=\d+\$[A-Za-z0-9+/]+\$[A-Za-z0-9+/]+$"
    assert re.match(padrao, hash_da_senha), hash_da_senha


# ---------------------------------------------------------------------------
# Custo computacional
# ---------------------------------------------------------------------------

def test_verificacao_e_lenta_de_proposito(hash_da_senha):
    """Lentidão é a propriedade que torna a força bruta cara.

    O limite de 50 ms é folgado: pega uma configuração acidentalmente
    rebaixada para parâmetros triviais, sem quebrar em máquina lenta.
    """
    inicio = time.perf_counter()
    verificar(hash_da_senha, SENHA)
    decorrido = time.perf_counter() - inicio

    assert decorrido > 0.05, (
        f"verificação levou {decorrido:.3f} s; parâmetros podem ter sido "
        f"rebaixados"
    )


def test_senha_errada_custa_o_mesmo_que_a_certa(hash_da_senha):
    """O tempo de resposta não pode indicar se a senha está próxima.

    A verificação precisa percorrer todo o trabalho antes de comparar, e
    não abandonar cedo ao encontrar diferença.
    """
    inicio = time.perf_counter()
    verificar(hash_da_senha, SENHA)
    com_senha_certa = time.perf_counter() - inicio

    inicio = time.perf_counter()
    verificar(hash_da_senha, "completamente-diferente")
    com_senha_errada = time.perf_counter() - inicio

    proporcao = max(com_senha_certa, com_senha_errada) / min(
        com_senha_certa, com_senha_errada
    )
    assert proporcao < 2.0, (
        f"certa levou {com_senha_certa:.3f} s, errada {com_senha_errada:.3f} s"
    )


# ---------------------------------------------------------------------------
# Defesa contra enumeração de usuários
# ---------------------------------------------------------------------------

def test_consumir_tempo_gasta_tempo_comparavel(hash_da_senha):
    """Para usuário inexistente, o login precisa demorar o mesmo.

    Sem isso, a rota responderia em microssegundos para login inválido e
    em centenas de milissegundos para login válido com senha errada. A
    diferença é mensurável de fora e revela quais contas existem — mesmo
    com a mensagem de erro idêntica nos dois casos.
    """
    consumir_tempo_equivalente()  # primeira chamada calcula o hash interno

    inicio = time.perf_counter()
    verificar(hash_da_senha, "errada")
    tempo_real = time.perf_counter() - inicio

    inicio = time.perf_counter()
    consumir_tempo_equivalente()
    tempo_falso = time.perf_counter() - inicio

    proporcao = max(tempo_real, tempo_falso) / min(tempo_real, tempo_falso)
    assert proporcao < 2.0, (
        f"verificação real {tempo_real:.3f} s, simulada {tempo_falso:.3f} s"
    )


def test_consumir_tempo_nao_levanta_excecao():
    """Ela roda dentro da rota de login e não pode derrubar a requisição."""
    consumir_tempo_equivalente()
    consumir_tempo_equivalente()


# ---------------------------------------------------------------------------
# Recalibração
# ---------------------------------------------------------------------------

def test_hash_atual_nao_precisa_recalibrar(hash_da_senha):
    assert precisa_recalibrar(hash_da_senha) is False


def test_hash_com_parametros_fracos_precisa_recalibrar():
    """Detecta hashes gerados antes de a calibração ser elevada.

    A massa de dados fictícios usa `m=65536,t=3,p=4`, os parâmetros de
    partida da RFC. Este teste confirma que eles seriam marcados para
    recalibração no próximo login bem-sucedido.
    """
    from argon2 import PasswordHasher

    fraco = PasswordHasher(
        memory_cost=65536, time_cost=3, parallelism=4
    ).hash(SENHA)

    assert precisa_recalibrar(fraco) is True
    assert verificar(fraco, SENHA) is True, (
        "o hash antigo precisa continuar verificando"
    )


# ---------------------------------------------------------------------------
# Validação de entrada
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("vazia", ["", "   ", "\t", "\n"])
def test_gerar_hash_recusa_senha_vazia(vazia):
    with pytest.raises(ErroDeSenha):
        gerar_hash(vazia)


def test_gerar_hash_recusa_senha_absurdamente_longa():
    """Sem limite, uma senha de megabytes vira negação de serviço.

    Cada caractere entra no cálculo; dez megabytes consumiriam minutos de
    CPU numa única requisição.
    """
    with pytest.raises(ErroDeSenha) as erro:
        gerar_hash("x" * (senhas.TAMANHO_MAXIMO + 1))
    assert str(senhas.TAMANHO_MAXIMO) in str(erro.value)


def test_senha_no_limite_e_aceita():
    assert gerar_hash("x" * senhas.TAMANHO_MAXIMO)


@pytest.mark.parametrize("invalida", [None, 123, b"bytes", ["lista"]])
def test_gerar_hash_recusa_tipo_invalido(invalida):
    with pytest.raises(ErroDeSenha):
        gerar_hash(invalida)


def test_verificar_com_senha_longa_devolve_falso(hash_da_senha):
    """Rejeitar por tamanho não pode virar canal de informação.

    Se levantasse exceção, o comportamento da rota para senha longa
    diferiria do comportamento para senha errada.
    """
    assert verificar(hash_da_senha, "x" * (senhas.TAMANHO_MAXIMO + 1)) is False


@pytest.mark.parametrize(
    "invalido",
    ["", "nao-e-um-hash", "$argon2id$incompleto", "$2b$12$bcrypt-nao-argon"],
)
def test_verificar_recusa_hash_invalido(invalido):
    """Hash corrompido é defeito, não senha errada: precisa aparecer."""
    with pytest.raises(HashInvalido):
        verificar(invalido, SENHA)


def test_precisa_recalibrar_recusa_hash_invalido():
    with pytest.raises(HashInvalido):
        precisa_recalibrar("nao-e-um-hash")


# ---------------------------------------------------------------------------
# Senhas com acento e símbolos
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "senha",
    [
        pytest.param("SenhaComAcentuação123", id="acentos"),
        pytest.param("çãõáéíóú", id="so-acentos"),
        pytest.param("senha com espaços no meio", id="espacos"),
        pytest.param("!@#$%^&*()_+-=[]{}|;:,.<>?", id="simbolos"),
        pytest.param("🔐🏖️senha-com-emoji", id="emoji"),
        pytest.param("frase-senha longa e fácil de lembrar", id="frase-senha"),
    ],
)
def test_senha_com_caracteres_especiais_funciona(senha):
    """Nenhuma classe de caractere pode ser recusada ou normalizada.

    Normalizar silenciosamente reduziria o espaço de busca sem que o
    usuário soubesse.
    """
    gerado = gerar_hash(senha)
    assert verificar(gerado, senha) is True
