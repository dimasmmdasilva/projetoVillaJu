"""O guia de instalação e o roteiro da demonstração não mentem — Bloco 18d.

Documento de instalação envelhece em silêncio: o script muda de nome, a
variável nova não entra na tabela, o prazo muda e o texto não. Quem
descobre é quem instala, numa máquina nova, sem ninguém para perguntar —
ou o apresentador, na frente da banca.

Estes testes tratam os dois documentos como código:

1. **Todo arquivo citado existe.**
2. **Toda variável que o código lê está explicada**, e nenhuma explicada
   é inventada.
3. **Os padrões e números escritos são os do código** (14 cópias, 48 h,
   90 minutos, 4 s por foto, os padrões das variáveis opcionais).
4. **Nenhum comando usa `Get-Content | mysql`**, que estraga acentos.
5. **O roteiro funciona:** as rotas existem, as contas existem, a
   recepção recebe 403 em `/auditoria`, e o SQL do Workbench roda — o
   `INSERT` conflitante é recusado pelo gatilho com a frase do roteiro.
"""

import ast
import pathlib
import re

import pytest

from app.servicos import regulamento
from app.servicos.estado_do_backup import LIMITE_DE_HORAS
from app.rotas.publico import FOTOS_DA_POUSADA

RAIZ = pathlib.Path(__file__).resolve().parents[1]
GUIA = (RAIZ / "docs" / "instalacao.md").read_text(encoding="utf-8")
ROTEIRO = (RAIZ / "docs" / "roteiro_demonstracao.md").read_text(encoding="utf-8")
README = (RAIZ / "README.md").read_text(encoding="utf-8")
MODELO = (RAIZ / ".env.example").read_text(encoding="utf-8")
CSS = (RAIZ / "app" / "static" / "css" / "villaju.css").read_text(encoding="utf-8")
MENSAGEM_DO_GATILHO = "Já existe reserva ativa para este chalé no período informado."

DOCUMENTOS = {"instalacao": GUIA, "roteiro": ROTEIRO, "README": README}


def _blocos_de_codigo(texto):
    return re.findall(r"```[a-z]*\n(.*?)```", texto, re.S)


def _variaveis_no_codigo():
    achadas = set()
    for pasta in ("app", "scripts"):
        for arquivo in (RAIZ / pasta).rglob("*.py"):
            achadas |= set(re.findall(r"VILLAJU_[A-Z_]+", arquivo.read_text(encoding="utf-8")))
    achadas |= set(re.findall(r"VILLAJU_[A-Z_]+", (RAIZ / "run.py").read_text(encoding="utf-8")))
    return achadas


def _avaliar(expressao):
    """Só aritmética de constantes — o padrão escrito em `config.py`."""
    def valor(no):
        if isinstance(no, ast.Constant):
            return no.value
        if isinstance(no, ast.BinOp) and isinstance(no.op, ast.Mult):
            return valor(no.left) * valor(no.right)
        raise ValueError(ast.dump(no))
    return valor(ast.parse(expressao, mode="eval").body)


# ===========================================================================
# 1. Arquivos
# ===========================================================================

@pytest.mark.parametrize("nome", ["instalacao", "README"])
def test_todo_arquivo_citado_existe(nome):
    citados = set(re.findall(
        r"\b((?:scripts|banco|docs|testes|app)[\\/][\w./\\-]+\.(?:py|sql|md|html|css))",
        DOCUMENTOS[nome],
    ))
    assert citados, "o documento deveria citar arquivos do projeto"
    faltando = sorted(c for c in citados if not (RAIZ / c.replace("\\", "/")).exists())
    assert not faltando, f"{nome} cita arquivos que não existem: {faltando}"


def test_o_readme_leva_ao_guia_e_ao_roteiro():
    assert "](docs/instalacao.md)" in README
    assert "](docs/roteiro_demonstracao.md)" in README
    assert "](instalacao.md)" in ROTEIRO


def test_os_cinco_scripts_do_banco_em_ordem():
    comandos = re.findall(r'SOURCE (banco/\d\d_\w+\.sql)"', GUIA)
    assert sorted(set(comandos)) == sorted(
        f"banco/{a.name}" for a in (RAIZ / "banco").glob("0*.sql")
    )
    ordem = [int(re.search(r"/(\d\d)_", c).group(1)) for c in comandos[:5]]
    assert ordem == [0, 1, 2, 3, 4]


# ===========================================================================
# 2. Variáveis
# ===========================================================================

def _linhas_das_tabelas():
    """As variáveis com linha própria nas tabelas das seções 3.2 e 3.3.

    Não basta a variável aparecer em algum lugar do guia: a sabotagem que
    tirou a linha da chave Pix passou, porque ela continuava citada no
    checklist da seção 11. Explicada é ter a linha, com o "para quê".
    """
    secao = GUIA[GUIA.index("### 3.2."):GUIA.index("## 4.")]
    return set(re.findall(r"^\| `(VILLAJU_[A-Z_]+)` \|", secao, re.M))


def test_toda_variavel_do_modelo_esta_explicada():
    do_modelo = set(re.findall(r"^(VILLAJU_[A-Z_]+)=", MODELO, re.M))
    faltando = sorted(do_modelo - _linhas_das_tabelas())
    assert not faltando, f"variáveis do .env.example fora das tabelas: {faltando}"


def test_toda_variavel_lida_pelo_codigo_esta_no_guia():
    faltando = sorted(_variaveis_no_codigo() - _linhas_das_tabelas())
    assert not faltando, f"o código lê variáveis que o guia não explica: {faltando}"


def test_o_guia_nao_inventa_variavel():
    citadas = set(re.findall(r"`(VILLAJU_[A-Z_]+)`", GUIA))
    conhecidas = _variaveis_no_codigo() | set(re.findall(r"^(VILLAJU_[A-Z_]+)=", MODELO, re.M))
    assert not citadas - conhecidas, sorted(citadas - conhecidas)


def test_os_padroes_das_opcionais_sao_os_do_codigo():
    config = (RAIZ / "app" / "config.py").read_text(encoding="utf-8")
    opcionais = GUIA[GUIA.index("### 3.3."):GUIA.index("## 4.")]
    for variavel, padrao in re.findall(
        r'_(?:inteiro|opcional)\(\s*"(VILLAJU_[A-Z_]+)",\s*([^)\n]+?)\s*\)', config
    ):
        linha = re.search(rf"^\| `{variavel}` \| ([^|]+) \|", opcionais, re.M)
        if not linha:
            continue  # variável do .env.example, explicada na outra tabela
        escrito = linha.group(1).strip()
        esperado = _avaliar(padrao)
        if isinstance(esperado, int):
            assert escrito.split()[0] == str(esperado), (variavel, escrito, esperado)
        else:
            assert escrito == f"`{esperado}`", (variavel, escrito, esperado)


# ===========================================================================
# 3. Números
# ===========================================================================

def test_os_numeros_do_backup_sao_os_do_codigo():
    script = (RAIZ / "scripts" / "backup.py").read_text(encoding="utf-8")
    COPIAS_MANTIDAS = int(re.search(r"^COPIAS_MANTIDAS = (\d+)$", script, re.M).group(1))

    assert f"Ficam as **{COPIAS_MANTIDAS}** mais novas" in GUIA
    assert f"mais de **{LIMITE_DE_HORAS}**" in GUIA


def test_os_numeros_do_roteiro_sao_os_do_codigo():
    assert f"prazo de {regulamento.MINUTOS_PARA_O_PIX} minutos" in ROTEIRO
    ciclo = int(re.search(r"animation: troca-de-foto (\d+)s", CSS).group(1))
    assert f"a cada {ciclo // len(FOTOS_DA_POUSADA)} s" in ROTEIRO


# ===========================================================================
# 4. Comandos
# ===========================================================================

@pytest.mark.parametrize("nome", DOCUMENTOS)
def test_nenhum_comando_passa_sql_pelo_pipe_do_powershell(nome):
    for bloco in _blocos_de_codigo(DOCUMENTOS[nome]):
        assert not re.search(r"Get-Content[^\n]*\|\s*mysql", bloco), bloco


def test_o_agendamento_tem_um_nome_so_e_aponta_para_o_script():
    nomes = set(re.findall(r'-TaskName "([^"]+)"', GUIA))
    assert nomes == {"VillaJu - backup diario"}
    assert '-Argument "scripts\\backup.py"' in GUIA
    assert (RAIZ / "scripts" / "backup.py").exists()


# ===========================================================================
# 5. O roteiro funciona
# ===========================================================================

def test_as_contas_do_roteiro_existem_na_massa():
    massa = (RAIZ / "banco" / "04_dados_ficticios.sql").read_text(encoding="utf-8")
    for login, senha, perfil in re.findall(
        r"^\| `(\w+)` \| `([^`]+)` \| (\w+) \|", GUIA, re.M
    ):
        # O guia escreve "hóspede"; a massa, o identificador `hospede`.
        perfil = perfil.replace("ó", "o")
        assert re.search(rf"--\s+{login}\s+{re.escape(senha)}\s+{perfil}", massa), login
        assert f"`{login}`" in ROTEIRO


@pytest.fixture
def aplicacao(conexao_banco):
    from conftest import (
        SCRIPT_DADOS,
        SCRIPT_SCHEMA,
        SCRIPT_TRIGGERS,
        aplicar_script,
        limpar_banco,
    )

    from app import create_app
    from app.config import Configuracao
    from app.dados import conexao

    cursor = conexao_banco.cursor()
    limpar_banco(cursor)
    aplicar_script(cursor, SCRIPT_SCHEMA)
    aplicar_script(cursor, SCRIPT_TRIGGERS)
    aplicar_script(cursor, SCRIPT_DADOS)
    cursor.close()

    configuracao = Configuracao(modo_teste=True)
    conexao.iniciar_pool(configuracao)
    app = create_app(configuracao)
    app.config["SESSION_COOKIE_SECURE"] = False
    yield app
    conexao.encerrar_pool()


@pytest.mark.banco
def test_as_rotas_do_roteiro_existem(aplicacao):
    rotas = {r.rule for r in aplicacao.url_map.iter_rules()}
    citadas = set(re.findall(r"`(/[a-z/-]*)`", ROTEIRO))
    assert citadas
    assert not citadas - rotas, sorted(citadas - rotas)


@pytest.mark.banco
def test_a_recepcao_recebe_403_em_auditoria(aplicacao):
    cliente = aplicacao.test_client()
    cliente.post("/login", data={"login": "recepcao", "senha": "Villa@Recep2026"})
    assert cliente.get("/auditoria").status_code == 403


def _sql_marcado(marca):
    achado = re.search(rf"<!-- sql:{marca} -->\s*```sql\n(.*?)```", ROTEIRO, re.S)
    assert achado, marca
    comandos = [c.strip() for c in achado.group(1).split(";") if c.strip()]
    assert comandos[0] == "USE villaju", "o roteiro precisa escolher o banco"
    # O teste roda no banco de testes, que a conexão já escolheu.
    return comandos[1:]


@pytest.mark.banco
def test_o_insert_do_workbench_e_recusado_pelo_gatilho(aplicacao, conexao_banco):
    import mysql.connector

    cursor = conexao_banco.cursor()
    cursor.execute("SELECT COUNT(*) FROM reserva")
    antes = cursor.fetchone()[0]

    selecao, insercao = _sql_marcado("conflito")
    cursor.execute(selecao)
    with pytest.raises(mysql.connector.Error) as erro:
        cursor.execute(insercao)
    assert erro.value.errno == 1644
    assert MENSAGEM_DO_GATILHO in erro.value.msg
    assert MENSAGEM_DO_GATILHO in ROTEIRO

    cursor.execute("SELECT COUNT(*) FROM reserva")
    assert cursor.fetchone()[0] == antes
    cursor.close()


@pytest.mark.banco
def test_as_consultas_de_leitura_rodam(aplicacao, conexao_banco):
    cursor = conexao_banco.cursor()
    for comando in _sql_marcado("leitura"):
        assert comando.upper().startswith("SELECT"), comando
        cursor.execute(comando)
        cursor.fetchall()
    cursor.close()
