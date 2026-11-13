"""Acesso aos dados dos hóspedes, com cifragem transparente.

O resto da aplicação nunca vê ciphertext. Entrega texto, recebe texto, e
não sabe que existe criptografia no caminho:

    criar({"nome": "João da Silva", "documento": "123.456.789-00"})
    buscar_por_id(7)   ->  {"id": 7, "nome": "João da Silva", ...}

A ignorância é deliberada. Se a cifragem ficasse a cargo de quem chama,
bastaria um caminho de código esquecer de cifrar para que um documento
fosse gravado em texto claro — e o esquecimento não apareceria em teste
nenhum, porque o sistema continuaria funcionando.

---------------------------------------------------------------------------
O caso de borda do dado adicional autenticado

A seção 6.2 do plano manda usar o identificador do hóspede como dado
adicional autenticado, para que um registro cifrado não possa ser movido
para outra pessoa. Mas num `INSERT` novo o identificador ainda não existe:
ele é gerado pelo banco.

A seção 12 registrava a escolha entre duas saídas. Adotada a primeira,
**transação em duas fases**, por não alterar o schema:

    1. INSERT com as colunas cifradas vazias, dentro de uma transação.
       Os campos de busca vão já preenchidos, porque o HMAC não depende
       do identificador — e assim uma violação de unicidade aparece aqui,
       antes de gastar trabalho de cifragem.
    2. O banco devolve o identificador gerado.
    3. Cifragem de verdade, com esse identificador como dado adicional.
    4. UPDATE da mesma linha, ainda dentro da transação.
    5. COMMIT.

Se qualquer passo falhar, a transação é desfeita e a linha incompleta não
existe para ninguém. Nenhuma outra sessão enxerga o estado intermediário.

**Consequência aceita:** a criação de um hóspede gera DUAS linhas de
auditoria, um `INSERT` e um `UPDATE`, para uma única operação de negócio.
A alternativa seria acrescentar uma coluna de identificador próprio ao
schema, alterando a Etapa 1 já documentada e entregue.
"""

import mysql.connector

from app.dados.conexao import obter_conexao, obter_cursor
from app.seguranca import cripto

# Campos pessoais, na ordem em que aparecem no formulário.
OBRIGATORIOS = ("nome", "documento")
OPCIONAIS = ("telefone", "email")
CAMPOS = OBRIGATORIOS + OPCIONAIS

# Tamanho máximo de cada campo em texto, antes de cifrar. O limite protege
# as colunas: o ciphertext do AES-GCM tem o mesmo tamanho do texto, e um
# nome de mil caracteres estouraria o VARBINARY(512).
LIMITES = {"nome": 150, "documento": 60, "telefone": 40, "email": 150}

VERSAO_DA_CRIPTOGRAFIA = 1

_SQL_INSERIR = (
    "INSERT INTO hospede "
    "(nome_cif, nome_nonce, nome_tag, "
    " documento_cif, documento_nonce, documento_tag, "
    " email_busca, documento_busca, cripto_versao) "
    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)"
)

_SQL_GRAVAR_CIFRADOS = (
    "UPDATE hospede SET "
    "  nome_cif = %s, nome_nonce = %s, nome_tag = %s, "
    "  documento_cif = %s, documento_nonce = %s, documento_tag = %s, "
    "  telefone_cif = %s, telefone_nonce = %s, telefone_tag = %s, "
    "  email_cif = %s, email_nonce = %s, email_tag = %s "
    "WHERE id = %s"
)

_SQL_ATUALIZAR_BUSCA = (
    "UPDATE hospede SET email_busca = %s, documento_busca = %s WHERE id = %s"
)

_COLUNAS_DE_LEITURA = (
    "id, nome_cif, nome_nonce, nome_tag, "
    "documento_cif, documento_nonce, documento_tag, "
    "telefone_cif, telefone_nonce, telefone_tag, "
    "email_cif, email_nonce, email_tag, "
    "cripto_versao, criado_em, alterado_em"
)

_SQL_POR_ID = (
    "SELECT id, nome_cif, nome_nonce, nome_tag, "
    "       documento_cif, documento_nonce, documento_tag, "
    "       telefone_cif, telefone_nonce, telefone_tag, "
    "       email_cif, email_nonce, email_tag, "
    "       cripto_versao, criado_em, alterado_em "
    "FROM hospede WHERE id = %s LIMIT 1"
)

_SQL_POR_EMAIL_BUSCA = (
    "SELECT id, nome_cif, nome_nonce, nome_tag, "
    "       documento_cif, documento_nonce, documento_tag, "
    "       telefone_cif, telefone_nonce, telefone_tag, "
    "       email_cif, email_nonce, email_tag, "
    "       cripto_versao, criado_em, alterado_em "
    "FROM hospede WHERE email_busca = %s LIMIT 1"
)

_SQL_POR_DOCUMENTO_BUSCA = (
    "SELECT id, nome_cif, nome_nonce, nome_tag, "
    "       documento_cif, documento_nonce, documento_tag, "
    "       telefone_cif, telefone_nonce, telefone_tag, "
    "       email_cif, email_nonce, email_tag, "
    "       cripto_versao, criado_em, alterado_em "
    "FROM hospede WHERE documento_busca = %s LIMIT 1"
)

# Etapa 5, Bloco 15: o documento de um acompanhante não pode ser o do
# próprio titular. A comparação é pelo índice, sem decifrar nada.
_SQL_DOCUMENTO_DO_HOSPEDE = (
    "SELECT COUNT(*) FROM hospede WHERE id = %s AND documento_busca = %s"
)

_SQL_LISTAR = (
    "SELECT id, nome_cif, nome_nonce, nome_tag, "
    "       documento_cif, documento_nonce, documento_tag, "
    "       telefone_cif, telefone_nonce, telefone_tag, "
    "       email_cif, email_nonce, email_tag, "
    "       cripto_versao, criado_em, alterado_em "
    "FROM hospede ORDER BY id DESC LIMIT %s"
)

_SQL_EXCLUIR = "DELETE FROM hospede WHERE id = %s"
_SQL_CONTAR = "SELECT COUNT(*) FROM hospede"


class ErroDeHospede(Exception):
    """Falha ao gravar ou ler um hóspede."""


class DadoObrigatorioAusente(ErroDeHospede):
    pass


class HospedeDuplicado(ErroDeHospede):
    """Já existe hóspede com este documento ou e-mail.

    A detecção vem da restrição de unicidade sobre os campos de busca, que
    funciona justamente porque o HMAC é determinístico.
    """


# ---------------------------------------------------------------------------
# Validação e normalização
# ---------------------------------------------------------------------------

def _limpar(dados: dict) -> dict:
    """Normaliza a entrada e recusa o que não serve.

    Campo opcional vazio vira None, e não string vazia: as três colunas de
    um campo ausente ficam nulas em conjunto, e não com ciphertext de
    texto vazio, que ocuparia espaço e confundiria a leitura.
    """
    limpo = {}

    for campo in CAMPOS:
        valor = dados.get(campo)
        valor = valor.strip() if isinstance(valor, str) else valor

        if campo in OBRIGATORIOS and not valor:
            raise DadoObrigatorioAusente(f"o campo {campo} é obrigatório")

        if valor and len(valor) > LIMITES[campo]:
            raise ErroDeHospede(
                f"o campo {campo} tem {len(valor)} caracteres; "
                f"o limite é {LIMITES[campo]}"
            )

        limpo[campo] = valor or None

    return limpo


def _indices(dados: dict) -> tuple:
    """Calcula os campos de busca, que não dependem do identificador.

    Por isso eles podem ir já no `INSERT`: uma violação de unicidade
    aparece ali, antes de gastar trabalho de cifragem.
    """
    email = cripto.indice(dados["email"]) if dados.get("email") else None
    documento = cripto.indice_documento(dados["documento"])
    return email, documento


def _cifrar_campos(dados: dict, identificador) -> list:
    """Cifra os quatro campos com o identificador como dado adicional.

    Devolve a lista de valores na ordem do comando de atualização: para
    cada campo, ciphertext, nonce e tag — ou três nulos quando o campo não
    foi informado.
    """
    aad = cripto.aad_hospede(identificador)
    valores = []

    for campo in CAMPOS:
        texto = dados.get(campo)
        if texto is None:
            valores.extend([None, None, None])
        else:
            ciphertext, nonce, tag = cripto.cifrar(texto, aad)
            valores.extend([ciphertext, nonce, tag])

    return valores


def _decifrar(linha: dict, tolerante: bool = False) -> dict:
    """Devolve o hóspede em texto claro.

    A verificação de integridade acontece dentro de `decifrar`. Uma falha
    significa uma de três coisas, e o AES-GCM não distingue entre elas: o
    ciphertext foi adulterado, a tag foi adulterada, ou o registro foi
    movido de outro hóspede.

    O que fazer diante da falha depende de quem pergunta, e a diferença é
    deliberada:

    **Leitura de um registro específico** (`tolerante=False`): a exceção
    sobe. Quem pediu aquele hóspede não pode receber dado de outro nem
    dado alterado — é melhor a tela quebrar do que exibir a pessoa errada.

    **Listagem** (`tolerante=True`): o registro ilegível é marcado e a
    lista continua. Derrubar a página inteira por causa de uma linha
    corrompida esconderia as outras e tornaria o problema mais difícil de
    diagnosticar. A linha aparece com `legivel = False`, e a tela mostra
    que ela existe mas não pôde ser lida — que é exatamente a informação
    que o operador precisa para investigar.
    """
    aad = cripto.aad_hospede(linha["id"])

    hospede = {
        "id": linha["id"],
        "cripto_versao": linha["cripto_versao"],
        "criado_em": linha["criado_em"],
        "alterado_em": linha["alterado_em"],
        "legivel": True,
    }

    for campo in CAMPOS:
        ciphertext = linha[f"{campo}_cif"]
        if ciphertext is None:
            hospede[campo] = None
            continue

        try:
            hospede[campo] = cripto.decifrar(
                ciphertext, linha[f"{campo}_nonce"], linha[f"{campo}_tag"], aad
            )
        except cripto.FalhaDeAutenticacao:
            if not tolerante:
                raise
            hospede[campo] = None
            hospede["legivel"] = False

    return hospede


# ---------------------------------------------------------------------------
# Escrita
# ---------------------------------------------------------------------------

def criar(dados: dict, usuario_id=None) -> int:
    """Grava um hóspede novo e devolve o identificador.

    As quatro fases descritas no cabeçalho acontecem dentro de uma única
    transação. Um erro em qualquer uma desfaz tudo.
    """
    limpo = _limpar(dados)
    email_busca, documento_busca = _indices(limpo)

    with obter_conexao(usuario_id=usuario_id) as conexao:
        cursor = conexao.cursor()
        try:
            # Fase 1: a linha nasce com as colunas cifradas vazias. Os
            # campos de busca já vão preenchidos, de modo que uma violação
            # de unicidade apareça antes do trabalho de cifragem.
            try:
                cursor.execute(
                    _SQL_INSERIR,
                    (
                        b"", b"", b"",      # nome
                        b"", b"", b"",      # documento
                        email_busca,
                        documento_busca,
                        VERSAO_DA_CRIPTOGRAFIA,
                    ),
                )
            except mysql.connector.Error as erro:
                if erro.errno == 1062:
                    raise HospedeDuplicado(
                        "já existe hóspede com este documento ou e-mail"
                    ) from erro
                raise

            # Fase 2: o banco gera o identificador.
            identificador = cursor.lastrowid

            # Fases 3 e 4: cifragem com o identificador como dado adicional
            # autenticado, e gravação na mesma linha.
            cursor.execute(
                _SQL_GRAVAR_CIFRADOS,
                (*_cifrar_campos(limpo, identificador), identificador),
            )
        finally:
            cursor.close()

    return identificador


def atualizar(identificador, dados: dict, usuario_id=None) -> None:
    """Regrava os dados de um hóspede existente.

    Aqui não há caso de borda: o identificador já existe, então a cifragem
    acontece de uma vez só.
    """
    limpo = _limpar(dados)
    email_busca, documento_busca = _indices(limpo)

    with obter_conexao(usuario_id=usuario_id) as conexao:
        cursor = conexao.cursor()
        try:
            try:
                cursor.execute(
                    _SQL_ATUALIZAR_BUSCA,
                    (email_busca, documento_busca, identificador),
                )
            except mysql.connector.Error as erro:
                if erro.errno == 1062:
                    raise HospedeDuplicado(
                        "já existe hóspede com este documento ou e-mail"
                    ) from erro
                raise

            cursor.execute(
                _SQL_GRAVAR_CIFRADOS,
                (*_cifrar_campos(limpo, identificador), identificador),
            )
        finally:
            cursor.close()


def excluir(identificador, usuario_id=None) -> None:
    """Apaga um hóspede.

    A chave estrangeira `RESTRICT` impede apagar quem tem reserva — o erro
    sobe e a tela precisa tratá-lo.
    """
    with obter_conexao(usuario_id=usuario_id) as conexao:
        cursor = conexao.cursor()
        try:
            cursor.execute(_SQL_EXCLUIR, (identificador,))
        finally:
            cursor.close()


# ---------------------------------------------------------------------------
# Leitura
# ---------------------------------------------------------------------------

def buscar_por_id(identificador, usuario_id=None):
    with obter_cursor(usuario_id=usuario_id, dicionario=True) as cursor:
        cursor.execute(_SQL_POR_ID, (identificador,))
        linha = cursor.fetchone()

    return _decifrar(linha) if linha else None


def buscar_por_email(email: str, usuario_id=None):
    """Busca pelo índice HMAC, não pelo campo cifrado.

    Buscar sobre a coluna cifrada seria impossível: o AES-GCM é não
    determinístico, e cifrar o mesmo e-mail duas vezes produz bytes
    diferentes. O índice resolve isso sem revelar o valor.
    """
    if not email or not email.strip():
        return None

    with obter_cursor(usuario_id=usuario_id, dicionario=True) as cursor:
        cursor.execute(_SQL_POR_EMAIL_BUSCA, (cripto.indice(email),))
        linha = cursor.fetchone()

    return _decifrar(linha) if linha else None


def buscar_por_documento(documento: str, usuario_id=None):
    if not documento or not documento.strip():
        return None

    with obter_cursor(usuario_id=usuario_id, dicionario=True) as cursor:
        cursor.execute(
            _SQL_POR_DOCUMENTO_BUSCA, (cripto.indice_documento(documento),)
        )
        linha = cursor.fetchone()

    return _decifrar(linha) if linha else None


def documento_e_do_hospede(hospede_id, documento: str,
                           usuario_id=None) -> bool:
    """O documento informado é o deste hóspede? Compara pelo índice HMAC.

    A normalização do índice ignora pontuação: "529.982.247-25" e
    "52998224725" são o mesmo documento.
    """
    if not documento or not documento.strip():
        return False

    with obter_cursor(usuario_id=usuario_id) as cursor:
        cursor.execute(
            _SQL_DOCUMENTO_DO_HOSPEDE,
            (hospede_id, cripto.indice_documento(documento)),
        )
        return cursor.fetchone()[0] > 0


def listar(limite: int = 200, usuario_id=None) -> list:
    """Hóspedes em texto claro, do mais recente para o mais antigo.

    Cada linha custa quatro decifragens. Com a massa prevista para a
    hospedaria — algumas centenas de registros — o custo é irrelevante;
    numa base grande, a listagem precisaria paginar de verdade.
    """
    limite = max(1, min(int(limite), 1000))

    with obter_cursor(usuario_id=usuario_id, dicionario=True) as cursor:
        cursor.execute(_SQL_LISTAR, (limite,))
        linhas = cursor.fetchall()

    return [_decifrar(linha, tolerante=True) for linha in linhas]


def contar(usuario_id=None) -> int:
    with obter_cursor(usuario_id=usuario_id) as cursor:
        cursor.execute(_SQL_CONTAR)
        return cursor.fetchone()[0]
