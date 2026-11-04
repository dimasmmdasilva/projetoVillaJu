"""Proteção das senhas da equipe com Argon2id.

Senha não se guarda — guarda-se uma função irreversível dela. A escolha
dessa função é o que separa um vazamento de banco de dados contornável de
um irreparável.

Por que Argon2id e não SHA-256: funções de hash genéricas são RÁPIDAS por
projeto, e velocidade é exatamente a propriedade errada aqui. Uma placa de
vídeo comum calcula bilhões de SHA-256 por segundo, o que torna trivial
testar todas as senhas de um dicionário contra um banco vazado. O Argon2id
é lento de propósito e, mais importante, consome MEMÓRIA — o recurso que
não se multiplica barato em hardware dedicado a quebrar senhas.

Por que a variante `id` e não `i` ou `d`: a `d` resiste melhor a ataque por
hardware dedicado mas vaza informação por canal lateral de tempo; a `i`
resiste ao canal lateral mas é mais fraca contra hardware. A `id` combina
as duas abordagens e é a recomendada pela RFC 9106 para senhas.

O sal é sorteado pela biblioteca, único por senha. Sem sal, duas pessoas
com a mesma senha teriam o mesmo hash, e uma tabela pré-calculada
quebraria as duas de uma vez.
"""

import os

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from argon2.low_level import Type

# ---------------------------------------------------------------------------
# Parâmetros, calibrados e medidos
# ---------------------------------------------------------------------------
# A seção 6.1 do plano manda partir da RFC 9106 — 64 MiB, 3 iterações,
# paralelismo 4 — e calibrar para custo próximo de um segundo na máquina de
# referência.
#
# A medição nessa máquina (Windows 11, 16 núcleos lógicos, Python 3.12.3)
# mostrou que os parâmetros de partida custam apenas 0,044 s, vinte vezes
# abaixo do alvo. Foram medidas combinações mais caras:
#
#      64 MiB,  3 iterações   0,044 s   <- ponto de partida da RFC
#     128 MiB,  8 iterações   0,232 s
#     256 MiB,  6 iterações   0,350 s
#     256 MiB, 10 iterações   0,658 s   <- escolhido
#     512 MiB,  5 iterações   0,594 s
#     512 MiB,  9 iterações   1,337 s
#    1024 MiB,  4 iterações   1,340 s
#
# Escolhidos 256 MiB e 10 iterações, que custam 0,658 s para gerar e
# 0,712 s para verificar. Três razões para não perseguir o segundo exato:
#
#   1. Memória por login concorrente. Cada verificação reserva o valor
#      inteiro. Com 512 MiB, cinco logins simultâneos consomem 2,5 GiB, e
#      uma enxurrada de tentativas vira vetor de exaustão de memória
#      mesmo com limitação de taxa.
#   2. Variação entre as máquinas do grupo. Só uma das cinco foi medida.
#      Uma configuração que custa 1,3 s aqui pode custar 4 s num
#      computador mais modesto, e login de quatro segundos parece defeito.
#   3. 256 MiB já é quatro vezes a memória recomendada pela RFC 9106 e
#      mais de cinco vezes o mínimo sugerido pelo OWASP. O ganho marginal
#      de dobrar não compensa os dois pontos acima.
#
# Recalibrar depois é seguro: os parâmetros vão embutidos na string do
# hash, então senhas antigas continuam verificando com os valores com que
# foram geradas. A função `precisa_recalibrar` identifica quais hashes
# estão defasados.

MEMORIA_KIB = 262144      # 256 MiB
ITERACOES = 10
PARALELISMO = 4
TAMANHO_DO_HASH = 32      # 256 bits de saída
TAMANHO_DO_SAL = 16       # 128 bits, sorteados pela biblioteca

# Senha vazia ou só com espaços é defeito de quem chamou, não escolha do
# usuário: a validação de formulário deveria tê-la barrado antes.
TAMANHO_MINIMO = 1

# Limite superior contra abuso. Sem ele, enviar uma senha de dez megabytes
# faria o servidor gastar minutos de CPU numa única requisição - negação de
# serviço com uma linha de comando. O valor é folgado para qualquer senha
# ou frase-senha legítima.
TAMANHO_MAXIMO = 1024


class ErroDeSenha(Exception):
    """Falha ao gerar ou verificar hash de senha."""


class HashInvalido(ErroDeSenha):
    """A string armazenada não é um hash Argon2 reconhecível.

    Indica corrupção do registro ou tentativa de verificar contra algo que
    nunca foi um hash. Distinto de senha incorreta, que é situação normal.
    """


def _hasher() -> PasswordHasher:
    """Instância configurada com os parâmetros calibrados.

    Criada a cada chamada em vez de guardada em variável de módulo: o
    objeto é barato e isso mantém os parâmetros visíveis no rastro de
    execução, sem estado escondido.
    """
    return PasswordHasher(
        time_cost=ITERACOES,
        memory_cost=MEMORIA_KIB,
        parallelism=PARALELISMO,
        hash_len=TAMANHO_DO_HASH,
        salt_len=TAMANHO_DO_SAL,
        type=Type.ID,
    )


def _validar(senha) -> None:
    if not isinstance(senha, str):
        raise ErroDeSenha(
            f"a senha precisa ser str, não {type(senha).__name__}"
        )
    if len(senha.strip()) < TAMANHO_MINIMO:
        raise ErroDeSenha("a senha não pode ser vazia")
    if len(senha) > TAMANHO_MAXIMO:
        raise ErroDeSenha(
            f"a senha tem {len(senha)} caracteres; o limite é {TAMANHO_MAXIMO}"
        )


def gerar_hash(senha: str) -> str:
    """Devolve a string codificada do Argon2id, pronta para gravar.

    O formato é
    `$argon2id$v=19$m=262144,t=10,p=4$<sal>$<hash>`, e carrega tudo o que
    é preciso para verificar depois: variante, versão, parâmetros e sal.
    Nada além dessa string precisa ser guardado.
    """
    _validar(senha)
    return _hasher().hash(senha)


def verificar(hash_armazenado: str, senha: str) -> bool:
    """Confere a senha contra o hash. Devolve True ou False.

    Senha incorreta é situação normal de um sistema de login, não
    excepcional — por isso devolve False em vez de levantar exceção. Já um
    hash corrompido é defeito, e esse levanta `HashInvalido`.

    A comparação é feita pela biblioteca em tempo constante em relação ao
    conteúdo, de modo que o tempo de resposta não revela quantos
    caracteres iniciais estavam certos.
    """
    if not isinstance(hash_armazenado, str) or not hash_armazenado:
        raise HashInvalido("o hash armazenado está vazio ou não é texto")

    try:
        _validar(senha)
    except ErroDeSenha:
        # Senha fora dos limites não confere com hash nenhum. Devolver
        # False aqui evita que o formato da senha enviada se torne um
        # canal de informação sobre o que existe no banco.
        return False

    try:
        return _hasher().verify(hash_armazenado, senha)
    except VerifyMismatchError:
        return False
    except InvalidHashError as erro:
        raise HashInvalido(
            "a string armazenada não é um hash Argon2 válido"
        ) from erro
    except VerificationError as erro:
        raise HashInvalido(f"falha ao verificar o hash: {erro}") from erro


def precisa_recalibrar(hash_armazenado: str) -> bool:
    """Diz se o hash foi gerado com parâmetros mais fracos que os atuais.

    Usado no login: quando o usuário acerta a senha e o hash está
    defasado, é o único momento em que a senha em claro está disponível
    para regerar o hash com os parâmetros novos. Fora dele, só restaria
    forçar todo mundo a trocar de senha.
    """
    if not isinstance(hash_armazenado, str) or not hash_armazenado:
        raise HashInvalido("o hash armazenado está vazio ou não é texto")

    try:
        return _hasher().check_needs_rehash(hash_armazenado)
    except InvalidHashError as erro:
        raise HashInvalido(
            "a string armazenada não é um hash Argon2 válido"
        ) from erro


# ---------------------------------------------------------------------------
# Defesa contra enumeração de usuários
# ---------------------------------------------------------------------------

_hash_de_referencia = None


def consumir_tempo_equivalente() -> None:
    """Gasta o mesmo tempo de uma verificação, sem verificar nada.

    Serve ao caso em que o login informado não existe. Sem isso, a rota
    responderia em microssegundos para usuário inexistente e em setecentos
    milissegundos para usuário existente com senha errada — e essa
    diferença, mensurável de fora, revelaria quais logins são válidos.

    A mensagem de erro idêntica nos dois casos, prevista no plano, não
    basta sozinha: o relógio também fala.

    O hash de referência é calculado uma única vez, na primeira chamada, e
    guardado em memória. Calculá-lo na importação do módulo atrasaria a
    partida da aplicação em quase um segundo.
    """
    global _hash_de_referencia

    if _hash_de_referencia is None:
        _hash_de_referencia = _hasher().hash(
            os.urandom(16).hex()
        )

    try:
        _hasher().verify(_hash_de_referencia, "senha-que-jamais-confere")
    except (VerifyMismatchError, VerificationError):
        pass


def descrever_parametros() -> dict:
    """Parâmetros em uso, para registro em documentação e relatório."""
    return {
        "variante": "Argon2id",
        "memoria_kib": MEMORIA_KIB,
        "memoria_mib": MEMORIA_KIB // 1024,
        "iteracoes": ITERACOES,
        "paralelismo": PARALELISMO,
        "tamanho_do_hash": TAMANHO_DO_HASH,
        "tamanho_do_sal": TAMANHO_DO_SAL,
    }
