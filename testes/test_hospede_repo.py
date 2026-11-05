"""Cadastro de hóspedes com cifragem transparente.

O critério de conclusão da Etapa 2 é verificado aqui: inserir um hóspede
pela aplicação e constatar que a tabela `hospede` não contém nada legível.

Os testes consultam o banco de duas formas. Pelo repositório, para
confirmar que a aplicação enxerga texto claro. E por SQL cru, lendo as
colunas como o MySQL Workbench as mostraria, para confirmar que lá não há
nada legível. A diferença entre as duas leituras é o que a cifragem
entrega.
"""

import pytest

from app.dados import conexao, hospede_repo
from app.seguranca import cripto
from app.seguranca.cripto import FalhaDeAutenticacao

pytestmark = pytest.mark.banco

MARIA = {
    "nome": "Maria Aparecida de Souza",
    "documento": "123.456.789-00",
    "telefone": "(12) 99888-7766",
    "email": "maria.souza@exemplo.com",
}

JOAO = {
    "nome": "João Carlos da Silva",
    "documento": "987.654.321-00",
    "telefone": "(11) 97777-1234",
    "email": "joao.silva@exemplo.com",
}


@pytest.fixture
def banco_limpo(banco_com_triggers):
    """Schema e triggers aplicados, sem massa de dados.

    A massa fictícia tem bytes aleatórios nas colunas cifradas, que não
    decifram. Estes testes criam os próprios registros, cifrados de
    verdade.
    """
    from app.config import Configuracao

    conexao.iniciar_pool(Configuracao(modo_teste=True))
    yield banco_com_triggers
    conexao.encerrar_pool()


def _linha_crua(identificador):
    """Lê a linha como o MySQL Workbench a mostraria."""
    with conexao.obter_cursor(dicionario=True) as cursor:
        cursor.execute("SELECT * FROM hospede WHERE id = %s", (identificador,))
        return cursor.fetchone()


def _todos_os_bytes():
    """Concatena todo conteúdo binário da tabela, para varredura."""
    with conexao.obter_cursor() as cursor:
        cursor.execute("SELECT * FROM hospede")
        return b"".join(
            bytes(valor)
            for linha in cursor.fetchall()
            for valor in linha
            if isinstance(valor, (bytes, bytearray))
        )


# ---------------------------------------------------------------------------
# Ida e volta
# ---------------------------------------------------------------------------

def test_criar_devolve_identificador(banco_limpo):
    assert hospede_repo.criar(MARIA) > 0


def test_ida_e_volta_preserva_os_quatro_campos(banco_limpo):
    identificador = hospede_repo.criar(MARIA)
    lido = hospede_repo.buscar_por_id(identificador)

    for campo in hospede_repo.CAMPOS:
        assert lido[campo] == MARIA[campo], campo


@pytest.mark.parametrize(
    "nome",
    [
        pytest.param("José da Conceição Ávila", id="acentos"),
        pytest.param("Nguyễn Thị Hương", id="vietnamita"),
        pytest.param("Família Gonçalves 🏖️", id="emoji"),
        pytest.param("O'Brien-Santos", id="apostrofo-e-hifen"),
        pytest.param("x" * 150, id="no-limite"),
    ],
)
def test_nomes_dificeis_voltam_identicos(banco_limpo, nome):
    dados = dict(MARIA, nome=nome)
    identificador = hospede_repo.criar(dados)
    assert hospede_repo.buscar_por_id(identificador)["nome"] == nome


def test_campos_opcionais_ausentes_ficam_nulos(banco_limpo):
    identificador = hospede_repo.criar(
        {"nome": "Sem Contato", "documento": "111.222.333-44"}
    )
    lido = hospede_repo.buscar_por_id(identificador)

    assert lido["telefone"] is None
    assert lido["email"] is None

    crua = _linha_crua(identificador)
    for coluna in ("telefone_cif", "telefone_nonce", "telefone_tag",
                   "email_cif", "email_nonce", "email_tag", "email_busca"):
        assert crua[coluna] is None, (
            f"{coluna} deveria ser nula quando o campo não foi informado"
        )


def test_buscar_por_id_inexistente_devolve_nulo(banco_limpo):
    assert hospede_repo.buscar_por_id(999999) is None


# ---------------------------------------------------------------------------
# O critério de conclusão da Etapa 2
# ---------------------------------------------------------------------------

def test_banco_nao_contem_nenhum_texto_legivel(banco_limpo):
    """O teste que fecha a Etapa 2.

    Cadastra dois hóspedes e varre toda a tabela procurando qualquer
    pedaço dos dados digitados. Nada pode aparecer.
    """
    hospede_repo.criar(MARIA)
    hospede_repo.criar(JOAO)

    bruto = _todos_os_bytes()

    fragmentos = [
        b"Maria", b"Aparecida", b"Souza", b"123.456", b"12345678900",
        b"maria.souza", b"exemplo.com", b"99888",
        b"Jo", b"Carlos", b"Silva", b"987.654", b"joao.silva", b"97777",
    ]
    vazados = [f.decode(errors="replace") for f in fragmentos if f in bruto]

    assert not vazados, f"texto legível encontrado no banco: {vazados}"


def test_colunas_cifradas_tem_os_tamanhos_do_aes_gcm(banco_limpo):
    identificador = hospede_repo.criar(MARIA)
    crua = _linha_crua(identificador)

    for campo in hospede_repo.CAMPOS:
        assert len(crua[f"{campo}_nonce"]) == 12, campo
        assert len(crua[f"{campo}_tag"]) == 16, campo
        # O GCM é cifra de fluxo: o ciphertext tem o tamanho do texto.
        assert len(crua[f"{campo}_cif"]) == len(MARIA[campo].encode("utf-8"))


def test_cada_campo_usa_nonce_proprio(banco_limpo):
    """Reutilizar nonce com a mesma chave é catastrófico no GCM."""
    identificador = hospede_repo.criar(MARIA)
    crua = _linha_crua(identificador)

    nonces = [crua[f"{campo}_nonce"] for campo in hospede_repo.CAMPOS]
    assert len(set(nonces)) == len(nonces)


def test_dois_hospedes_com_o_mesmo_nome_tem_ciphertext_diferente(banco_limpo):
    """A cifragem é não determinística: o banco não denuncia a repetição."""
    primeiro = hospede_repo.criar(dict(MARIA, documento="111.111.111-11",
                                       email="a@exemplo.com"))
    segundo = hospede_repo.criar(dict(MARIA, documento="222.222.222-22",
                                      email="b@exemplo.com"))

    assert _linha_crua(primeiro)["nome_cif"] != _linha_crua(segundo)["nome_cif"]


# ---------------------------------------------------------------------------
# O dado adicional autenticado
# ---------------------------------------------------------------------------

def test_registro_movido_para_outro_hospede_nao_decifra(banco_limpo):
    """A razão de existir o dado adicional autenticado.

    Sem ele, alguém com acesso de escrita ao banco copiaria o bloco
    cifrado da linha de um hóspede para a de outro, e a decifragem
    funcionaria — o sistema exibiria os dados da pessoa errada sem que
    nada falhasse.
    """
    origem = hospede_repo.criar(MARIA)
    destino = hospede_repo.criar(JOAO)

    bloco = _linha_crua(origem)

    with conexao.obter_cursor() as cursor:
        cursor.execute(
            "UPDATE hospede SET nome_cif = %s, nome_nonce = %s, nome_tag = %s "
            "WHERE id = %s",
            (bloco["nome_cif"], bloco["nome_nonce"], bloco["nome_tag"], destino),
        )

    with pytest.raises(FalhaDeAutenticacao):
        hospede_repo.buscar_por_id(destino)


def test_ciphertext_adulterado_no_banco_nao_decifra(banco_limpo):
    """Alteração direta na tabela é detectada na leitura."""
    identificador = hospede_repo.criar(MARIA)
    crua = _linha_crua(identificador)

    adulterado = bytearray(crua["nome_cif"])
    adulterado[0] ^= 0b00000001

    with conexao.obter_cursor() as cursor:
        cursor.execute(
            "UPDATE hospede SET nome_cif = %s WHERE id = %s",
            (bytes(adulterado), identificador),
        )

    with pytest.raises(FalhaDeAutenticacao):
        hospede_repo.buscar_por_id(identificador)


def test_listagem_marca_registro_ilegivel_sem_derrubar(banco_limpo):
    """Uma linha corrompida não pode esconder as outras.

    Derrubar a página inteira tornaria o problema mais difícil de
    diagnosticar, além de inutilizar a tela.
    """
    bom = hospede_repo.criar(MARIA)
    ruim = hospede_repo.criar(JOAO)

    with conexao.obter_cursor() as cursor:
        cursor.execute(
            "UPDATE hospede SET nome_cif = %s WHERE id = %s",
            (b"bytes que nao decifram", ruim),
        )

    lista = hospede_repo.listar()
    por_id = {h["id"]: h for h in lista}

    assert por_id[bom]["legivel"] is True
    assert por_id[bom]["nome"] == MARIA["nome"]
    assert por_id[ruim]["legivel"] is False
    assert por_id[ruim]["nome"] is None


# ---------------------------------------------------------------------------
# Busca pelo índice
# ---------------------------------------------------------------------------

def test_busca_por_documento(banco_limpo):
    identificador = hospede_repo.criar(MARIA)
    achado = hospede_repo.buscar_por_documento("123.456.789-00")
    assert achado["id"] == identificador


@pytest.mark.parametrize(
    "escrito", ["123.456.789-00", "12345678900", " 123 456 789 00 "]
)
def test_busca_por_documento_ignora_pontuacao(banco_limpo, escrito):
    identificador = hospede_repo.criar(MARIA)
    assert hospede_repo.buscar_por_documento(escrito)["id"] == identificador


def test_busca_por_email(banco_limpo):
    identificador = hospede_repo.criar(MARIA)
    assert hospede_repo.buscar_por_email(MARIA["email"])["id"] == identificador


@pytest.mark.parametrize(
    "escrito",
    ["maria.souza@exemplo.com", "MARIA.SOUZA@EXEMPLO.COM",
     "  Maria.Souza@Exemplo.com  "],
)
def test_busca_por_email_ignora_caixa_e_espacos(banco_limpo, escrito):
    identificador = hospede_repo.criar(MARIA)
    assert hospede_repo.buscar_por_email(escrito)["id"] == identificador


def test_busca_sem_resultado_devolve_nulo(banco_limpo):
    hospede_repo.criar(MARIA)
    assert hospede_repo.buscar_por_documento("000.000.000-00") is None
    assert hospede_repo.buscar_por_email("ninguem@exemplo.com") is None


@pytest.mark.parametrize("vazio", ["", "   ", None])
def test_busca_vazia_devolve_nulo(banco_limpo, vazio):
    assert hospede_repo.buscar_por_documento(vazio) is None
    assert hospede_repo.buscar_por_email(vazio) is None


def test_indice_nao_revela_o_valor(banco_limpo):
    """O HMAC é irreversível e não contém o original."""
    identificador = hospede_repo.criar(MARIA)
    crua = _linha_crua(identificador)

    assert b"maria" not in crua["email_busca"].lower()
    assert b"123" not in crua["documento_busca"]
    assert crua["documento_busca"] != cripto.indice_documento("outro-documento")


# ---------------------------------------------------------------------------
# Unicidade
# ---------------------------------------------------------------------------

def test_documento_repetido_e_recusado(banco_limpo):
    hospede_repo.criar(MARIA)
    with pytest.raises(hospede_repo.HospedeDuplicado):
        hospede_repo.criar(dict(JOAO, documento=MARIA["documento"]))


def test_documento_repetido_com_pontuacao_diferente_e_recusado(banco_limpo):
    """Sem normalização, a restrição de unicidade seria contornável."""
    hospede_repo.criar(MARIA)
    with pytest.raises(hospede_repo.HospedeDuplicado):
        hospede_repo.criar(dict(JOAO, documento="12345678900"))


def test_email_repetido_e_recusado(banco_limpo):
    hospede_repo.criar(MARIA)
    with pytest.raises(hospede_repo.HospedeDuplicado):
        hospede_repo.criar(dict(JOAO, email=MARIA["email"]))


def test_email_repetido_em_caixa_diferente_e_recusado(banco_limpo):
    hospede_repo.criar(MARIA)
    with pytest.raises(hospede_repo.HospedeDuplicado):
        hospede_repo.criar(dict(JOAO, email="MARIA.SOUZA@EXEMPLO.COM"))


def test_duplicata_nao_deixa_registro_parcial(banco_limpo):
    """A transação é desfeita: nenhuma linha incompleta sobra.

    Esta é a prova de que a criação em duas fases é atômica. Sem a
    transação, a linha do passo 1 ficaria no banco com as colunas
    cifradas vazias.
    """
    hospede_repo.criar(MARIA)
    antes = hospede_repo.contar()

    with pytest.raises(hospede_repo.HospedeDuplicado):
        hospede_repo.criar(dict(JOAO, documento=MARIA["documento"]))

    assert hospede_repo.contar() == antes


def test_varios_hospedes_sem_email_convivem(banco_limpo):
    """A restrição de unicidade sobre coluna nula não impede repetição."""
    hospede_repo.criar({"nome": "Um", "documento": "111.111.111-11"})
    hospede_repo.criar({"nome": "Dois", "documento": "222.222.222-22"})
    assert hospede_repo.contar() == 2


# ---------------------------------------------------------------------------
# Validação de entrada
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("faltando", ["nome", "documento"])
def test_campo_obrigatorio_ausente_e_recusado(banco_limpo, faltando):
    dados = dict(MARIA)
    dados[faltando] = ""
    with pytest.raises(hospede_repo.DadoObrigatorioAusente):
        hospede_repo.criar(dados)


def test_campo_acima_do_limite_e_recusado(banco_limpo):
    """O ciphertext tem o tamanho do texto; sem limite, a coluna estoura."""
    with pytest.raises(hospede_repo.ErroDeHospede) as erro:
        hospede_repo.criar(dict(MARIA, nome="x" * 151))
    assert "150" in str(erro.value)


def test_espacos_nas_pontas_sao_removidos(banco_limpo):
    identificador = hospede_repo.criar(dict(MARIA, nome="  Maria Souza  "))
    assert hospede_repo.buscar_por_id(identificador)["nome"] == "Maria Souza"


# ---------------------------------------------------------------------------
# Alteração e exclusão
# ---------------------------------------------------------------------------

def test_atualizar_regrava_os_campos(banco_limpo):
    identificador = hospede_repo.criar(MARIA)
    hospede_repo.atualizar(
        identificador, dict(MARIA, nome="Maria Souza Ferreira")
    )
    assert hospede_repo.buscar_por_id(identificador)["nome"] == "Maria Souza Ferreira"


def test_atualizar_mantem_o_dado_cifrado(banco_limpo):
    identificador = hospede_repo.criar(MARIA)
    hospede_repo.atualizar(identificador, dict(MARIA, nome="Nome Novo Aqui"))

    assert b"Nome Novo Aqui" not in _todos_os_bytes()


def test_atualizar_para_documento_de_outro_e_recusado(banco_limpo):
    primeiro = hospede_repo.criar(MARIA)
    hospede_repo.criar(JOAO)

    with pytest.raises(hospede_repo.HospedeDuplicado):
        hospede_repo.atualizar(primeiro, dict(MARIA, documento=JOAO["documento"]))


def test_excluir_remove_o_registro(banco_limpo):
    identificador = hospede_repo.criar(MARIA)
    hospede_repo.excluir(identificador)
    assert hospede_repo.buscar_por_id(identificador) is None


# ---------------------------------------------------------------------------
# Auditoria
# ---------------------------------------------------------------------------

def _eventos_do_hospede(identificador):
    with conexao.obter_cursor(dicionario=True) as cursor:
        cursor.execute(
            "SELECT acao, detalhe FROM log_auditoria "
            "WHERE tabela_afetada = 'hospede' AND registro_id = %s ORDER BY id",
            (identificador,),
        )
        return cursor.fetchall()


def test_criacao_gera_duas_linhas_de_auditoria(banco_limpo):
    """A consequência aceita da criação em duas fases.

    Uma operação de negócio produz um INSERT e um UPDATE, porque o
    identificador só existe depois da primeira. A alternativa seria
    acrescentar uma coluna ao schema da Etapa 1.
    """
    identificador = hospede_repo.criar(MARIA)
    acoes = [evento["acao"] for evento in _eventos_do_hospede(identificador)]
    assert acoes == ["INSERT", "UPDATE"]


def test_auditoria_nao_contem_dado_do_hospede(banco_limpo):
    """O log é legível por quem tem acesso de leitura ao banco."""
    identificador = hospede_repo.criar(MARIA)

    with conexao.obter_cursor() as cursor:
        cursor.execute("SELECT * FROM log_auditoria")
        tudo = " ".join(str(valor) for linha in cursor.fetchall() for valor in linha)

    for fragmento in ("Maria", "Souza", "123.456", "maria.souza", "99888"):
        assert fragmento not in tudo


def test_auditoria_registra_o_autor(banco_limpo):
    """O usuário informado chega às triggers pela variável de sessão."""
    with conexao.obter_cursor() as cursor:
        cursor.execute(
            "INSERT INTO usuario (login, nome, senha_hash, perfil) "
            "VALUES ('operador', 'Operador', '$argon2id$x', 'recepcionista')"
        )
        cursor.execute("SELECT id FROM usuario WHERE login = 'operador'")
        operador = cursor.fetchone()[0]

    identificador = hospede_repo.criar(MARIA, usuario_id=operador)

    with conexao.obter_cursor(dicionario=True) as cursor:
        cursor.execute(
            "SELECT usuario_id FROM log_auditoria "
            "WHERE tabela_afetada = 'hospede' AND registro_id = %s LIMIT 1",
            (identificador,),
        )
        assert cursor.fetchone()["usuario_id"] == operador


# ---------------------------------------------------------------------------
# Listagem e contagem
# ---------------------------------------------------------------------------

def test_listar_devolve_tudo_decifrado(banco_limpo):
    hospede_repo.criar(MARIA)
    hospede_repo.criar(JOAO)

    nomes = {hospede["nome"] for hospede in hospede_repo.listar()}
    assert nomes == {MARIA["nome"], JOAO["nome"]}


def test_listar_respeita_o_limite(banco_limpo):
    for numero in range(5):
        hospede_repo.criar(
            {"nome": f"Hóspede {numero}", "documento": f"{numero}11.111.111-11"}
        )
    assert len(hospede_repo.listar(limite=3)) == 3


def test_contar(banco_limpo):
    assert hospede_repo.contar() == 0
    hospede_repo.criar(MARIA)
    assert hospede_repo.contar() == 1
