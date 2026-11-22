"""Backup e restauração — Etapa 5, Bloco 18c (RNF06).

O que estes testes guardam, sem precisar de root nem de mysqldump de
verdade (os dois são trocados por um executor que registra o que receberia):

1. **A senha nunca vai para a linha de comando**, nem no backup nem na
   restauração; vai por arquivo de opções.
2. **Só a cópia completa ganha o nome de backup**; a interrompida some.
3. **Ficam as 14 mais novas**, e nada mais da pasta é tocado.
4. **Restaurar por cima do banco em uso exige o nome digitado**; sem pedir,
   a cópia vai para um banco separado.
5. **O usuário de backup só lê.**
6. **O Painel do administrador avisa** quando o backup atrasa.

O teste de restauração de verdade, em banco novo e com a decifragem
conferida, é feito uma vez com o root do Dimas e registrado como evidência
(RNF06) — `scripts/restaurar.py`.
"""

import datetime as dt
import os
import importlib.util
import pathlib
import types

import pytest

from app.servicos import estado_do_backup as estado

RAIZ = pathlib.Path(__file__).resolve().parents[1]


def _script(nome):
    especificacao = importlib.util.spec_from_file_location(
        f"script_{nome}", RAIZ / "scripts" / f"{nome}.py")
    modulo = importlib.util.module_from_spec(especificacao)
    especificacao.loader.exec_module(modulo)
    return modulo


backup = _script("backup")
restaurar = _script("restaurar")
configurar = _script("configurar_backup")

AGORA = dt.datetime(2026, 11, 21, 3, 0)


def _copia(pasta, quando):
    arquivo = pasta / estado.nome_do_arquivo(quando)
    arquivo.write_text("-- Dump completed on x\n", encoding="utf-8")
    return arquivo


# ===========================================================================
# 1. O comando
# ===========================================================================

def test_a_senha_nao_vai_para_a_linha_de_comando(tmp_path):
    linha = backup.comando("mysqldump", tmp_path / "backup.cnf", "villaju",
                           tmp_path / "x.sql")
    assert linha[1] == f"--defaults-extra-file={tmp_path / 'backup.cnf'}", \
        "o mysqldump só aceita o arquivo de opções como primeiro argumento"
    texto = " ".join(linha)
    assert "password" not in texto and " -p" not in texto
    for opcao in ("--single-transaction", "--routines", "--triggers",
                  "--default-character-set=utf8mb4"):
        assert opcao in linha


# ===========================================================================
# 2. Só a cópia completa vira backup
# ===========================================================================

class _Executor:
    def __init__(self, codigo=0, conteudo="-- Dump completed on 2026-11-21\n"):
        self.codigo, self.conteudo, self.recebido = codigo, conteudo, None

    def __call__(self, linha, **_):
        self.recebido = linha
        destino = pathlib.Path(next(a for a in linha if a.startswith("--result-file="))
                               .split("=", 1)[1])
        if self.conteudo is not None:
            destino.write_text(self.conteudo, encoding="utf-8")
        return types.SimpleNamespace(returncode=self.codigo, stderr="erro simulado")


@pytest.fixture
def ambiente(tmp_path, monkeypatch):
    pasta = tmp_path / "backups"
    opcoes = tmp_path / "backup.cnf"
    opcoes.write_text("[client]\n", encoding="utf-8")
    monkeypatch.setenv("VILLAJU_PASTA_BACKUP", str(pasta))
    monkeypatch.setenv("VILLAJU_MYSQLDUMP", "mysqldump")
    monkeypatch.setattr(backup, "OPCOES", opcoes)
    return pasta


def test_backup_completo_ganha_o_nome(ambiente):
    arquivo = backup.fazer_backup(AGORA, executar=_Executor())
    assert arquivo.name == "villaju_2026-11-21_0300.sql"
    assert [a.name for a in ambiente.iterdir()] == [arquivo.name]


@pytest.mark.parametrize("executor", [
    _Executor(codigo=2),
    _Executor(conteudo="-- cortado no meio\nINSERT INTO"),
])
def test_backup_que_falha_ou_corta_nao_deixa_copia(ambiente, executor):
    with pytest.raises(backup.FalhaNoBackup):
        backup.fazer_backup(AGORA, executar=executor)
    assert list(ambiente.iterdir()) == [], "nem a cópia, nem o .parcial"


def test_sem_o_arquivo_de_opcoes_nao_roda(ambiente, monkeypatch, tmp_path):
    monkeypatch.setattr(backup, "OPCOES", tmp_path / "nao_existe.cnf")
    with pytest.raises(backup.FalhaNoBackup) as erro:
        backup.fazer_backup(AGORA, executar=_Executor())
    assert "configurar_backup.py" in str(erro.value)


# ===========================================================================
# 3. As 14 mais novas
# ===========================================================================

def test_ficam_as_14_mais_novas_e_nada_mais_e_tocado(ambiente):
    ambiente.mkdir()
    for dias in range(1, 21):
        _copia(ambiente, AGORA - dt.timedelta(days=dias))
    outro = ambiente / "anotacoes.txt"
    outro.write_text("da Vania", encoding="utf-8")
    parcial = ambiente / "villaju_2026-01-01_0300.sql.parcial"
    parcial.write_text("", encoding="utf-8")

    backup.fazer_backup(AGORA, executar=_Executor())

    restantes = estado.copias(ambiente)
    assert len(restantes) == backup.COPIAS_MANTIDAS == 14
    assert restantes[0].name == "villaju_2026-11-21_0300.sql"
    assert outro.exists() and parcial.exists()


# ===========================================================================
# 4. Restaurar
# ===========================================================================

def test_sem_pedir_a_copia_vai_para_um_banco_separado():
    assert restaurar.destino(False) == "villaju_restaurado"


@pytest.mark.parametrize("digitado", [None, "", "sim", "VILLAJU", "villaju_restaurado"])
def test_banco_em_uso_so_com_o_nome_digitado(digitado):
    with pytest.raises(restaurar.RestauracaoRecusada):
        restaurar.destino(True, digitado)
    assert restaurar.destino(True, "villaju") == "villaju"


def test_a_restauracao_nao_poe_a_senha_na_linha_de_comando(tmp_path, monkeypatch):
    copia = _copia(tmp_path, AGORA)
    monkeypatch.setenv("VILLAJU_MYSQL", "mysql")
    recebido = {}

    def executor(linha, stdin, **_):
        recebido["linha"] = linha
        recebido["entrada"] = stdin.read()
        recebido["opcoes"] = pathlib.Path(linha[1].split("=", 1)[1])
        recebido["opcoes_existia"] = recebido["opcoes"].exists()
        return types.SimpleNamespace(returncode=0, stderr=b"")

    restaurar.carregar(copia, "villaju_restaurado", "segredo-do-root", executar=executor)
    assert "segredo-do-root" not in " ".join(recebido["linha"])
    assert recebido["linha"][-1] == "villaju_restaurado"
    assert recebido["entrada"] == copia.read_bytes(), "o arquivo entra em bytes"
    assert recebido["opcoes_existia"] and not recebido["opcoes"].exists(), \
        "o arquivo temporário com a senha é apagado no fim"


def test_a_conferencia_conta_todas_as_tabelas():
    from conftest import TABELAS

    assert set(restaurar.CONTAGENS) == set(TABELAS)


# ===========================================================================
# 5. O usuário de backup só lê
# ===========================================================================

@pytest.mark.skipif(os.name != "nt", reason="permissões NTFS: só no Windows")
def test_o_icacls_e_a_conta_vem_do_windows_e_nao_do_ambiente(monkeypatch, tmp_path):
    """Relatório 1, achado R1-01 e o que a correção revelou.

    Chamado pelo nome, o `icacls` era procurado no PATH (Bandit B607). A
    primeira correção usou a variável `SystemRoot`, e o Semgrep acusou:
    ambiente é tão controlável quanto o PATH. A conta que recebe a
    permissão também vinha do ambiente (`USERNAME`) — alterá-la daria a
    leitura do `backup.cnf` a outra conta. As duas agora vêm da API do
    Windows; o teste põe valores falsos no ambiente e exige que nenhum
    chegue ao comando.
    """
    chamadas = []
    monkeypatch.setenv("SystemRoot", r"D:\Falso")
    monkeypatch.setenv("USERNAME", "Todos")
    monkeypatch.setenv("PATH", r"D:\Falso")
    monkeypatch.setattr(configurar.subprocess, "run",
                        lambda args, **kw: chamadas.append(args))
    configurar.restringir(tmp_path / "backup.cnf")

    programa, *argumentos = chamadas[0]
    assert os.path.isabs(programa)
    assert programa.lower().endswith(r"\system32\icacls.exe")
    assert "Falso" not in programa
    concedido = argumentos[argumentos.index("/grant:r") + 1]
    assert concedido == f"{configurar.usuario_do_windows()}:(R,W)"
    assert not concedido.startswith("Todos")


def test_o_usuario_de_backup_so_le():
    privilegios = configurar._SQL_PRIVILEGIOS
    for proibido in ("INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE",
                     "GRANT OPTION", "*.*"):
        assert proibido not in privilegios.replace("GRANT SELECT", "SELECT"), proibido
    assert "ON villaju.* TO 'villaju_backup'@'localhost'" in privilegios


def test_a_senha_vai_como_parametro_e_nao_montada():
    assert configurar._SQL_CRIAR.endswith("IDENTIFIED BY %s")
    assert configurar._SQL_TROCAR_SENHA.endswith("IDENTIFIED BY %s")


def test_o_arquivo_de_opcoes_tem_o_usuario_de_backup():
    texto = configurar.conteudo_das_opcoes("abc")
    assert "user=villaju_backup" in texto and 'password="abc"' in texto
    assert "[client]" in texto


# ===========================================================================
# 6. O aviso do Painel
# ===========================================================================

def test_sem_backup_nenhum_e_alerta(tmp_path):
    assert estado.situacao(tmp_path / "vazia", AGORA)["atrasado"] is True


def test_backup_recente_nao_e_alerta(tmp_path):
    _copia(tmp_path, AGORA - dt.timedelta(hours=5))
    situacao = estado.situacao(tmp_path, AGORA)
    assert situacao["atrasado"] is False
    assert "há 5 horas" in situacao["texto"]


def test_backup_de_mais_de_dois_dias_e_alerta(tmp_path):
    _copia(tmp_path, AGORA - dt.timedelta(hours=49))
    situacao = estado.situacao(tmp_path, AGORA)
    assert situacao["atrasado"] is True
    assert "Atrasado" in situacao["texto"]


def test_o_parcial_nao_conta_como_backup(tmp_path):
    (tmp_path / "villaju_2026-11-21_0200.sql.parcial").write_text("", encoding="utf-8")
    assert estado.situacao(tmp_path, AGORA)["ultimo"] is None


# ===========================================================================
# 7. O aviso na tela
# ===========================================================================

from test_pedido_do_hospede import _entrar, aplicacao  # noqa: E402,F401


@pytest.mark.banco
def test_o_administrador_ve_o_ultimo_backup_e_a_recepcao_nao(aplicacao, tmp_path,
                                                            monkeypatch):
    monkeypatch.setenv("VILLAJU_PASTA_BACKUP", str(tmp_path))
    _copia(tmp_path, dt.datetime.now() - dt.timedelta(hours=3))
    admin = _entrar(aplicacao, "admin", "Villa@Admin2026").get("/painel").data.decode()
    assert "Último backup:" in admin and "aviso-backup" in admin
    recepcao = _entrar(aplicacao, "recepcao", "Villa@Recep2026").get("/painel").data.decode()
    assert "Último backup" not in recepcao


@pytest.mark.banco
def test_backup_atrasado_vira_alerta_no_painel(aplicacao, tmp_path, monkeypatch):
    monkeypatch.setenv("VILLAJU_PASTA_BACKUP", str(tmp_path / "vazia"))
    admin = _entrar(aplicacao, "admin", "Villa@Admin2026").get("/painel").data.decode()
    assert '<p class="aviso aviso-erro">Nenhum backup encontrado' in admin
