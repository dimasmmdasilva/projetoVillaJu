"""A análise estática continua limpa — Relatório 1 (22/11).

O Relatório 1 registrou o Bandit em zero, com cada exceção (`# nosec`)
verificada. Zero achados vale enquanto ninguém mexe no código; estes
testes mantêm o resultado verdadeiro depois do relatório entregue:

1. **O Bandit não acusa nada**, nem avisa de exceção malformada.
2. **Toda exceção suprime um alerta de fato.** Duas não suprimiam nada
   (achado R1-02): esconderiam em silêncio um problema futuro naquela
   linha. Conferido rodando o Bandit ignorando as exceções.
3. **Toda exceção diz por quê**, no formato que o Bandit entende:
   `# nosec B404  # justificativa`. Com hífen no lugar do segundo `#`, o
   Bandit lia cada palavra da justificativa como nome de teste (R1-03).

O Bandit é dependência fixada do projeto (`requirements.txt`).
"""

import json
import pathlib
import re
import subprocess  # nosec B404  # roda o Bandit, lista fixa de argumentos
import sys

import pytest

RAIZ = pathlib.Path(__file__).resolve().parents[1]
ALVOS = ["app", "scripts", "run.py"]
FORMATO = re.compile(r"#\s*nosec(?P<testes>(?:\s+B\d{3})+)\s+#\s*(?P<porque>\S.*)$")


def _bandit(*extra):
    resultado = subprocess.run(  # nosec B603  # sys.executable e argumentos fixos
        [sys.executable, "-m", "bandit", "-r", *ALVOS, "-f", "json", "-q", *extra],
        cwd=RAIZ, capture_output=True, text=True, check=False,
    )
    return json.loads(resultado.stdout), resultado.stderr


@pytest.fixture(scope="module")
def normal():
    return _bandit()


@pytest.fixture(scope="module")
def sem_excecoes():
    return _bandit("--ignore-nosec")[0]


def _excecoes():
    """(arquivo, linha, comentário) de cada `# nosec` do código analisado."""
    achadas = []
    for alvo in ALVOS:
        caminho = RAIZ / alvo
        arquivos = [caminho] if caminho.is_file() else sorted(caminho.rglob("*.py"))
        for arquivo in arquivos:
            for n, linha in enumerate(arquivo.read_text(encoding="utf-8").splitlines(), 1):
                if re.search(r"#\s*nosec\b", linha):
                    nome = arquivo.relative_to(RAIZ).as_posix()
                    achadas.append((nome, n, linha[linha.index("#"):]))
    return achadas


def test_o_bandit_nao_acusa_nada(normal):
    dados, erros = normal
    assert dados["results"] == [], [
        (r["test_id"], r["filename"], r["line_number"]) for r in dados["results"]
    ]
    assert "WARNING" not in erros, erros


def test_toda_excecao_suprime_um_alerta_de_fato(sem_excecoes):
    suprimidos = {
        (pathlib.Path(r["filename"]).as_posix(), r["line_number"], r["test_id"])
        for r in sem_excecoes["results"]
    }
    for arquivo, linha, comentario in _excecoes():
        testes = re.findall(r"B\d{3}", comentario.split("#")[1])
        for teste in testes:
            assert (arquivo, linha, teste) in suprimidos, (
                f"{arquivo}:{linha} declara {teste}, mas o Bandit não acusa nada "
                f"ali: a exceção está sobrando e esconderia um problema futuro"
            )


def test_toda_excecao_diz_por_que():
    excecoes = _excecoes()
    assert excecoes, "o código tem exceções declaradas; a busca deveria achá-las"
    for arquivo, linha, comentario in excecoes:
        achado = FORMATO.search(comentario)
        assert achado, (
            f"{arquivo}:{linha}: use '# nosec BXXX  # justificativa' — {comentario}"
        )
        assert len(achado.group("porque")) >= 10, f"{arquivo}:{linha}: justificativa curta"


# ===========================================================================
# 4. O ponto cego do Bandit em português (achado R1-05)
#
# A regra B105 do Bandit ("senha escrita no código") só reconhece nomes em
# inglês. Comprovado em 22/11: com o mesmo valor, `PASSWORD = "..."` e
# `SECRET_KEY = "..."` são acusados; `SENHA_PADRAO = "..."` e
# `CHAVE_SECRETA = "..."` não. O gitleaks também não pega, porque procura
# segredo com cara de chave aleatória. Num projeto em português, a regra
# ficava desligada sem ninguém perceber — a sabotagem que escreveu uma
# senha no código passou pelo Bandit em silêncio.
#
# Esta verificação cobre o buraco: nenhum texto atribuído a um nome que
# fale de senha, chave, segredo ou token, em português ou inglês, salvo as
# exceções nomeadas abaixo, cada uma com o motivo.
# ===========================================================================

import ast  # noqa: E402

NOME_DE_SEGREDO = re.compile(r"senha|chave|segredo|token|password|passwd|secret|pwd", re.I)

EXCECOES_DE_SEGREDO = {
    ("app/dados/auditoria_repo.py", "SENHA_ALTERADA"):
        "nome do evento de auditoria, não uma senha",
    ("app/seguranca/autorizacao.py", "_CHAVE_NO_CONTEXTO"):
        "nome da chave de um dicionário interno (contexto da requisição)",
    ("scripts/configurar_backup.py", "_SQL_TROCAR_SENHA"):
        "consulta SQL fixa; a senha entra como parâmetro (%s)",
}


def _textos_em_nomes_de_segredo():
    achados = []
    for alvo in ALVOS:
        caminho = RAIZ / alvo
        arquivos = [caminho] if caminho.is_file() else sorted(caminho.rglob("*.py"))
        for arquivo in arquivos:
            nome_do_arquivo = arquivo.relative_to(RAIZ).as_posix()
            for no in ast.walk(ast.parse(arquivo.read_text(encoding="utf-8"))):
                pares = []
                if isinstance(no, (ast.Assign, ast.AnnAssign)):
                    alvos = no.targets if isinstance(no, ast.Assign) else [no.target]
                    for t in alvos:
                        nome = getattr(t, "id", None) or getattr(t, "attr", None)
                        if nome:
                            pares.append((nome, no.value))
                elif isinstance(no, ast.keyword) and no.arg:
                    pares.append((no.arg, no.value))
                elif isinstance(no, ast.Dict):
                    for chave, valor in zip(no.keys, no.values):
                        if isinstance(chave, ast.Constant) and isinstance(chave.value, str):
                            pares.append((chave.value, valor))
                for nome, valor in pares:
                    if (NOME_DE_SEGREDO.search(nome)
                            and isinstance(valor, ast.Constant)
                            and isinstance(valor.value, str) and valor.value):
                        achados.append((nome_do_arquivo, nome, valor.lineno))
    return achados


def test_nenhum_segredo_escrito_no_codigo_nem_em_portugues():
    novos = [
        f"{arquivo}:{linha} {nome}"
        for arquivo, nome, linha in _textos_em_nomes_de_segredo()
        if (arquivo, nome) not in EXCECOES_DE_SEGREDO
    ]
    assert not novos, f"texto atribuído a nome de segredo: {novos}"


def test_as_excecoes_de_segredo_ainda_existem():
    """Exceção de algo que saiu do código é lista que envelhece."""
    presentes = {(a, n) for a, n, _ in _textos_em_nomes_de_segredo()}
    assert set(EXCECOES_DE_SEGREDO) <= presentes, set(EXCECOES_DE_SEGREDO) - presentes
