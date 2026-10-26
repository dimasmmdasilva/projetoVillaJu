"""Massa de dados fictícios.

A carga de `banco/04_dados_ficticios.sql` é, em si, um teste: ela passa
por todas as triggers de validação. Se qualquer reserva violasse a regra
de não-sobreposição, de capacidade ou de coerência de datas, o script
falharia e nenhum teste deste arquivo chegaria a rodar.

O que se verifica aqui, além disso, é que a massa tem o conteúdo que a
demonstração e as etapas seguintes esperam - e que ela não introduz
nenhum dado pessoal legível no banco.
"""

import datetime as dt

import pytest

pytestmark = pytest.mark.banco

CHALES = ["Embaré", "Camburi", "Asturias", "Boracéia", "Dunas"]
CAPACIDADE = 4


def _uma(cursor, sql, parametros=()):
    cursor.execute(sql, parametros)
    linha = cursor.fetchone()
    return linha[0] if linha else None


# ---------------------------------------------------------------------------
# Chalés e tarifas
# ---------------------------------------------------------------------------

def test_os_cinco_chales_existem(banco_com_dados):
    banco_com_dados.execute("SELECT nome FROM chale ORDER BY id")
    nomes = [n[0] for n in banco_com_dados.fetchall()]
    assert nomes == CHALES


def test_todos_os_chales_comportam_quatro_pessoas(banco_com_dados):
    """Capacidade confirmada com a responsável pela hospedaria."""
    banco_com_dados.execute("SELECT nome, capacidade FROM chale")
    for nome, capacidade in banco_com_dados.fetchall():
        assert capacidade == CAPACIDADE, f"{nome} comporta {capacidade}"


def test_cada_chale_tem_alta_e_baixa_temporada(banco_com_dados):
    """A Etapa 3 precisa das duas para exercitar a virada de temporada."""
    banco_com_dados.execute(
        "SELECT c.nome, COUNT(*) FROM chale c "
        "JOIN tarifa t ON t.chale_id = c.id GROUP BY c.id, c.nome"
    )
    for nome, quantas in banco_com_dados.fetchall():
        assert quantas == 2, f"{nome} tem {quantas} tarifas"


def test_alta_temporada_custa_mais_que_a_baixa(banco_com_dados):
    banco_com_dados.execute(
        "SELECT c.nome, "
        "  MAX(CASE WHEN t.descricao LIKE 'Alta%' THEN t.valor_diaria END), "
        "  MAX(CASE WHEN t.descricao LIKE 'Baixa%' THEN t.valor_diaria END) "
        "FROM chale c JOIN tarifa t ON t.chale_id = c.id "
        "GROUP BY c.id, c.nome"
    )
    for nome, alta, baixa in banco_com_dados.fetchall():
        assert alta > baixa, f"{nome}: alta {alta} não supera baixa {baixa}"


def test_periodos_de_tarifa_nao_se_sobrepoem(banco_com_dados):
    """Duas tarifas válidas na mesma data tornariam o cálculo ambíguo."""
    banco_com_dados.execute(
        "SELECT COUNT(*) FROM tarifa a JOIN tarifa b "
        "  ON a.chale_id = b.chale_id AND a.id < b.id "
        "WHERE a.data_inicio <= b.data_fim AND a.data_fim >= b.data_inicio"
    )
    assert banco_com_dados.fetchone()[0] == 0


# ---------------------------------------------------------------------------
# Usuários
# ---------------------------------------------------------------------------

def test_os_tres_perfis_estao_representados(banco_com_dados):
    """Sem os três, não há como demonstrar o controle de acesso."""
    banco_com_dados.execute("SELECT DISTINCT perfil FROM usuario")
    perfis = {p[0] for p in banco_com_dados.fetchall()}
    assert perfis == {"administrador", "recepcionista", "hospede"}


def test_senhas_usam_argon2id(banco_com_dados):
    """Argon2i e Argon2d também existem; o projeto exige a variante id."""
    banco_com_dados.execute("SELECT login, senha_hash FROM usuario")
    for login, hash_senha in banco_com_dados.fetchall():
        assert hash_senha.startswith("$argon2id$"), (
            f"{login} não usa Argon2id: {hash_senha[:20]}"
        )


def test_parametros_do_hash_seguem_o_plano(banco_com_dados):
    """Seção 6.1: 64 MiB de memória, 3 iterações, paralelismo 4."""
    banco_com_dados.execute("SELECT login, senha_hash FROM usuario")
    for login, hash_senha in banco_com_dados.fetchall():
        assert "m=65536,t=3,p=4" in hash_senha, f"{login}: {hash_senha[:50]}"


def test_nenhuma_senha_aparece_em_claro(banco_com_dados):
    """O hash não pode conter a senha em nenhuma forma legível."""
    senhas = ["Villa@Admin2026", "Villa@Recep2026", "Villa@Hospede2026"]
    banco_com_dados.execute("SELECT senha_hash FROM usuario")
    for (hash_senha,) in banco_com_dados.fetchall():
        for senha in senhas:
            assert senha not in hash_senha


def test_hash_armazenado_verifica_a_senha_documentada(banco_com_dados):
    """As senhas do cabeçalho do script precisam funcionar de fato.

    Uma senha documentada que não confere é pior que nenhuma: a equipe
    perde tempo achando que errou de digitação.
    """
    from argon2 import PasswordHasher

    ph = PasswordHasher()
    esperadas = {
        "admin": "Villa@Admin2026",
        "recepcao": "Villa@Recep2026",
        "joao": "Villa@Hospede2026",
    }
    banco_com_dados.execute("SELECT login, senha_hash FROM usuario")
    for login, hash_senha in banco_com_dados.fetchall():
        assert ph.verify(hash_senha, esperadas[login]), login


def test_somente_o_perfil_hospede_tem_vinculo(banco_com_dados):
    """A trigger de integridade de perfil impõe isso; aqui se confirma."""
    banco_com_dados.execute("SELECT perfil, hospede_id FROM usuario")
    for perfil, hospede_id in banco_com_dados.fetchall():
        if perfil == "hospede":
            assert hospede_id is not None
        else:
            assert hospede_id is None


# ---------------------------------------------------------------------------
# Hóspedes
# ---------------------------------------------------------------------------

def test_existem_hospedes_suficientes_para_testar(banco_com_dados):
    assert _uma(banco_com_dados, "SELECT COUNT(*) FROM hospede") >= 5


def test_campos_pessoais_nao_contem_texto_legivel(banco_com_dados):
    """Nenhum nome, CPF ou e-mail em claro nas colunas protegidas.

    Mesmo sendo dados fictícios e bytes aleatórios, a verificação importa:
    ela falharia se alguém, por comodidade, gravasse 'João da Silva' numa
    coluna _cif para facilitar a depuração.
    """
    banco_com_dados.execute(
        "SELECT id, nome_cif, documento_cif, email_cif FROM hospede"
    )
    for linha in banco_com_dados.fetchall():
        identificador = linha[0]
        for valor in linha[1:]:
            if valor is None:
                continue
            legivel = sum(
                1 for b in valor if 32 <= b < 127
            )
            proporcao = legivel / len(valor)
            assert proporcao < 0.85, (
                f"hóspede {identificador}: {proporcao:.0%} do campo é texto "
                f"imprimível, o que sugere dado em claro"
            )


def test_nonce_tem_o_tamanho_do_aes_gcm(banco_com_dados):
    banco_com_dados.execute(
        "SELECT LENGTH(nome_nonce), LENGTH(documento_nonce) FROM hospede"
    )
    for tamanhos in banco_com_dados.fetchall():
        for tamanho in tamanhos:
            assert tamanho == 12


def test_tag_tem_o_tamanho_do_aes_gcm(banco_com_dados):
    banco_com_dados.execute(
        "SELECT LENGTH(nome_tag), LENGTH(documento_tag) FROM hospede"
    )
    for tamanhos in banco_com_dados.fetchall():
        for tamanho in tamanhos:
            assert tamanho == 16


def test_campos_de_busca_tem_o_tamanho_do_hmac(banco_com_dados):
    banco_com_dados.execute(
        "SELECT LENGTH(documento_busca) FROM hospede "
        "WHERE documento_busca IS NOT NULL"
    )
    for (tamanho,) in banco_com_dados.fetchall():
        assert tamanho == 32


def test_ha_hospede_sem_telefone_e_email(banco_com_dados):
    """Os campos opcionais precisam ser exercitados por algum registro."""
    assert _uma(
        banco_com_dados,
        "SELECT COUNT(*) FROM hospede WHERE telefone_cif IS NULL "
        "AND email_cif IS NULL",
    ) >= 1


# ---------------------------------------------------------------------------
# Reservas
# ---------------------------------------------------------------------------

def test_as_quatro_situacoes_estao_representadas(banco_com_dados):
    """A demonstração precisa mostrar cada estado do ciclo de vida."""
    banco_com_dados.execute("SELECT DISTINCT situacao FROM reserva")
    situacoes = {s[0] for s in banco_com_dados.fetchall()}
    assert situacoes == {"pendente", "confirmada", "cancelada", "concluida"}


def test_nenhuma_reserva_ativa_se_sobrepoe(banco_com_dados):
    """A prova de que a massa respeita a regra central do sistema."""
    banco_com_dados.execute(
        "SELECT COUNT(*) FROM reserva a JOIN reserva b "
        "  ON a.chale_id = b.chale_id AND a.id < b.id "
        "WHERE a.situacao IN ('pendente','confirmada') "
        "  AND b.situacao IN ('pendente','confirmada') "
        "  AND a.data_entrada < b.data_saida "
        "  AND a.data_saida > b.data_entrada"
    )
    assert banco_com_dados.fetchone()[0] == 0


def test_existem_reservas_encostadas(banco_com_dados):
    """Saída de uma no mesmo dia da entrada de outra, no mesmo chalé.

    Este par existe de propósito na massa: é a demonstração viva do
    intervalo semiaberto. Se a regra fosse fechada, a carga teria falhado.
    """
    assert _uma(
        banco_com_dados,
        "SELECT COUNT(*) FROM reserva a JOIN reserva b "
        "  ON a.chale_id = b.chale_id AND a.id <> b.id "
        "WHERE a.data_saida = b.data_entrada "
        "  AND a.situacao IN ('pendente','confirmada') "
        "  AND b.situacao IN ('pendente','confirmada')",
    ) >= 1


def test_existe_reserva_cruzando_a_virada_de_temporada(banco_com_dados):
    """A Etapa 3 precisa dela para o cálculo de valor total."""
    assert _uma(
        banco_com_dados,
        "SELECT COUNT(*) FROM reserva r JOIN tarifa t "
        "  ON t.chale_id = r.chale_id "
        "WHERE r.data_entrada < t.data_inicio AND r.data_saida > t.data_inicio",
    ) >= 1


def test_nenhuma_reserva_excede_a_capacidade(banco_com_dados):
    banco_com_dados.execute(
        "SELECT r.id, r.num_pessoas, c.capacidade FROM reserva r "
        "JOIN chale c ON c.id = r.chale_id WHERE r.num_pessoas > c.capacidade"
    )
    assert banco_com_dados.fetchall() == []


def test_todas_as_datas_sao_coerentes(banco_com_dados):
    assert _uma(
        banco_com_dados,
        "SELECT COUNT(*) FROM reserva WHERE data_saida <= data_entrada",
    ) == 0


def test_observacoes_nao_contem_dado_sensivel(banco_com_dados):
    """A coluna não é cifrada e aparece na interface sem proteção."""
    import re

    banco_com_dados.execute(
        "SELECT id, observacoes FROM reserva WHERE observacoes IS NOT NULL"
    )
    padroes = {
        "CPF": r"\d{3}\.?\d{3}\.?\d{3}-?\d{2}",
        "telefone": r"\(?\d{2}\)?\s?9?\d{4}-?\d{4}",
        "e-mail": r"[\w.+-]+@[\w-]+\.[\w.]+",
    }
    for identificador, texto in banco_com_dados.fetchall():
        for rotulo, padrao in padroes.items():
            assert not re.search(padrao, texto), (
                f"reserva {identificador} tem {rotulo} nas observações: {texto}"
            )


# ---------------------------------------------------------------------------
# Contatos
# ---------------------------------------------------------------------------

def test_as_quatro_situacoes_de_contato_estao_representadas(banco_com_dados):
    banco_com_dados.execute("SELECT DISTINCT situacao FROM contato")
    situacoes = {s[0] for s in banco_com_dados.fetchall()}
    assert situacoes == {"novo", "em_atendimento", "respondido", "descartado"}


def test_ip_de_origem_e_binario_recuperavel(banco_com_dados):
    """INET6_ATON guarda 4 bytes para IPv4 e 16 para IPv6."""
    banco_com_dados.execute(
        "SELECT INET6_NTOA(ip_origem) FROM contato WHERE ip_origem IS NOT NULL"
    )
    enderecos = [e[0] for e in banco_com_dados.fetchall()]
    assert enderecos, "nenhum contato tem IP registrado"
    for endereco in enderecos:
        assert endereco.count(".") == 3 or ":" in endereco


# ---------------------------------------------------------------------------
# Auditoria gerada pela carga
# ---------------------------------------------------------------------------

def test_a_carga_gerou_auditoria(banco_com_dados):
    """Cada hóspede e cada reserva inserida deixou rastro."""
    assert _uma(banco_com_dados, "SELECT COUNT(*) FROM log_auditoria") >= 12


def test_auditoria_registra_o_autor_das_reservas(banco_com_dados):
    """A carga define @app_usuario_id antes de inserir as reservas."""
    banco_com_dados.execute(
        "SELECT usuario_id FROM log_auditoria WHERE tabela_afetada = 'reserva'"
    )
    autores = {a[0] for a in banco_com_dados.fetchall()}
    assert autores and None not in autores, (
        f"reservas sem autor na auditoria: {autores}"
    )


def test_auditoria_dos_hospedes_fica_sem_autor(banco_com_dados):
    """Os hóspedes são carregados antes de existir usuário algum."""
    banco_com_dados.execute(
        "SELECT usuario_id FROM log_auditoria WHERE tabela_afetada = 'hospede'"
    )
    autores = {a[0] for a in banco_com_dados.fetchall()}
    assert autores == {None}


def test_log_da_carga_nao_vazou_campo_cifrado(banco_com_dados):
    """Nenhum byte das colunas protegidas aparece no log."""
    banco_com_dados.execute("SELECT nome_cif, documento_cif FROM hospede")
    protegidos = [v for linha in banco_com_dados.fetchall() for v in linha if v]

    banco_com_dados.execute("SELECT detalhe FROM log_auditoria")
    detalhes = " ".join(d[0] or "" for d in banco_com_dados.fetchall())

    for valor in protegidos:
        amostra = valor[:8].hex()
        assert amostra not in detalhes.lower()


def test_carimbo_de_tempo_foi_preenchido(banco_com_dados):
    """As colunas são NOT NULL e a carga não as informa."""
    for tabela in ("chale", "tarifa", "hospede", "usuario", "reserva"):
        banco_com_dados.execute(
            f"SELECT criado_em, alterado_em FROM `{tabela}` LIMIT 1"
        )
        criado, alterado = banco_com_dados.fetchone()
        assert isinstance(criado, dt.datetime), tabela
        assert isinstance(alterado, dt.datetime), tabela


# ---------------------------------------------------------------------------
# O arquivo de carga
# ---------------------------------------------------------------------------

def test_script_nao_seleciona_banco():
    """Com USE, a carga poderia atingir o banco errado."""
    from conftest import SCRIPT_DADOS

    instrucoes = [
        linha.strip()
        for linha in SCRIPT_DADOS.read_text(encoding="utf-8").splitlines()
        if not linha.strip().startswith("--")
        and linha.strip().upper().startswith(("USE ", "CREATE DATABASE"))
    ]
    assert not instrucoes, f"a carga seleciona banco: {instrucoes}"


def test_script_avisa_que_os_dados_nao_sao_ciphertext_real():
    """O aviso evita que alguém tente decifrar e perca tempo.

    Também evita a conclusão errada de que a cifragem já está implementada
    quando ela só chega na Etapa 2.
    """
    from conftest import SCRIPT_DADOS

    texto = SCRIPT_DADOS.read_text(encoding="utf-8").lower()
    assert "não são ciphertext real" in texto or "nao sao ciphertext real" in texto
