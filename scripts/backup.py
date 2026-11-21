"""Backup do banco da VillaJu — Etapa 5, Bloco 18c (RNF06).

Roda sozinho todo dia, pelo Agendador de Tarefas do Windows, e pode rodar à
mão (antes de uma migração, por exemplo):

    venv\\Scripts\\python scripts\\backup.py

**Quem entra no banco** é o usuário `villaju_backup`, que só lê. A senha
dele fica em `instance/backup.cnf` (fora do Git, só o dono do arquivo lê),
criado por `scripts/configurar_backup.py`. Ela **nunca** vai para a linha de
comando — que qualquer programa pode ler na lista de processos — nem para o
log.

**A cópia só existe quando está completa.** O `mysqldump` grava num arquivo
temporário; só depois de conferida a linha final ("Dump completed") ele
ganha o nome definitivo. Um backup interrompido no meio nunca se parece com
um backup.

**Ficam as 14 mais novas**; as mais velhas são apagadas. Cada execução
deixa uma linha em `backup.log`, na mesma pasta, sem segredo nenhum.

**O backup não leva a chave de cifragem.** Os dados pessoais saem cifrados,
como estão no banco. A chave fica no `.env` e precisa ser guardada à parte:
backup e chave juntos expõem tudo; separados, cada um sozinho não basta.
"""

import datetime as dt
import os
import pathlib
import shutil
import subprocess  # nosec B404 - chama o mysqldump com lista fixa de argumentos
import sys

RAIZ = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))

# O nome das cópias e a pasta vêm do mesmo módulo que o Painel usa para o
# aviso de "último backup": os dois nunca divergem. Só arquivos com esse
# nome são tocados na pasta.
from app.servicos.estado_do_backup import (  # noqa: E402
    copias,
    nome_do_arquivo,
    pasta_dos_backups,
)

OPCOES = RAIZ / "instance" / "backup.cnf"
COPIAS_MANTIDAS = 14
FIM_DO_DUMP = "-- Dump completed"


class FalhaNoBackup(Exception):
    """O backup não foi feito; a frase diz por quê, sem segredo."""


def a_apagar(pasta: pathlib.Path, manter: int = COPIAS_MANTIDAS) -> list:
    return copias(pasta)[manter:]


def comando(mysqldump: str, opcoes: pathlib.Path, banco: str,
            destino: pathlib.Path) -> list:
    """A linha do mysqldump. A senha está no arquivo de opções, e não aqui.

    - `--defaults-extra-file` vem primeiro: o mysqldump só o aceita assim;
    - `--single-transaction`: cópia coerente do InnoDB sem travar o uso;
    - `--routines --triggers --events`: os 24 gatilhos vêm junto;
    - `--no-tablespaces` e `--set-gtid-purged=OFF`: dispensam privilégios
      que o usuário de backup não tem e não precisa ter.
    """
    return [
        mysqldump,
        f"--defaults-extra-file={opcoes}",
        "--single-transaction",
        "--routines",
        "--triggers",
        "--events",
        "--no-tablespaces",
        "--set-gtid-purged=OFF",
        "--default-character-set=utf8mb4",
        f"--result-file={destino}",
        banco,
    ]


def completo(arquivo: pathlib.Path) -> bool:
    """O mysqldump termina o arquivo com "-- Dump completed on ..."."""
    try:
        with arquivo.open("rb") as f:
            f.seek(0, os.SEEK_END)
            f.seek(max(0, f.tell() - 200))
            return FIM_DO_DUMP.encode() in f.read()
    except OSError:
        return False


def _registrar(pasta: pathlib.Path, linha: str) -> None:
    pasta.mkdir(parents=True, exist_ok=True)
    with (pasta / "backup.log").open("a", encoding="utf-8") as log:
        log.write(f"{dt.datetime.now():%Y-%m-%d %H:%M:%S} {linha}\n")


def fazer_backup(agora: dt.datetime = None, executar=subprocess.run) -> pathlib.Path:
    agora = agora or dt.datetime.now()
    pasta = pasta_dos_backups()
    banco = os.environ.get("VILLAJU_DB_NOME") or "villaju"
    mysqldump = os.environ.get("VILLAJU_MYSQLDUMP") or shutil.which("mysqldump")
    if not mysqldump:
        raise FalhaNoBackup("mysqldump não encontrado no PATH nem em VILLAJU_MYSQLDUMP.")
    if not OPCOES.is_file():
        raise FalhaNoBackup(
            "instance/backup.cnf não existe. Rode scripts/configurar_backup.py."
        )

    pasta.mkdir(parents=True, exist_ok=True)
    final = pasta / nome_do_arquivo(agora)
    temporario = final.with_suffix(".sql.parcial")
    resultado = executar(  # nosec B603 - argumentos em lista, sem shell
        comando(mysqldump, OPCOES, banco, temporario),
        capture_output=True, text=True,
    )
    if resultado.returncode != 0 or not completo(temporario):
        temporario.unlink(missing_ok=True)
        # O stderr do mysqldump não leva senha; leva, por exemplo, "Access
        # denied for user 'villaju_backup'". Vai inteiro para quem roda.
        raise FalhaNoBackup(
            f"mysqldump falhou (código {resultado.returncode}): "
            f"{(resultado.stderr or '').strip()[:300]}"
        )
    temporario.replace(final)

    for velha in a_apagar(pasta):
        velha.unlink()
    return final


def main() -> int:
    from dotenv import load_dotenv

    load_dotenv(RAIZ / ".env")
    pasta = pasta_dos_backups()
    try:
        arquivo = fazer_backup()
    except FalhaNoBackup as erro:
        _registrar(pasta, f"FALHA {erro}")
        print(f"Backup NÃO feito: {erro}", file=sys.stderr)
        return 1
    tamanho = arquivo.stat().st_size // 1024
    _registrar(pasta, f"ok {arquivo.name} {tamanho} KB")
    print(f"Backup feito: {arquivo} ({tamanho} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
