"""Prepara o backup automático — Etapa 5, Bloco 18c. Roda UMA vez, com root.

    venv\\Scripts\\python scripts\\configurar_backup.py

1. Pede a senha de root (sem eco na tela) e cria — ou, se já existe,
   troca a senha de — `villaju_backup`, um usuário que **só lê** o banco
   `villaju`: SELECT, SHOW VIEW, TRIGGER, LOCK TABLES, EVENT. Nada de
   INSERT, UPDATE, DELETE, nem acesso a outro banco.
2. Gera a senha dele sozinho (`secrets`), e ninguém precisa vê-la.
3. Grava `instance/backup.cnf` (fora do Git) e restringe o arquivo ao
   usuário do Windows que rodou o script.

Nenhum texto montado chega a um `execute`: o nome do banco e do usuário
estão escritos por extenso nas consultas, e a senha vai como parâmetro.
"""

import getpass
import os
import pathlib
import secrets
import subprocess  # nosec B404 - icacls com lista fixa de argumentos
import sys

RAIZ = pathlib.Path(__file__).resolve().parents[1]
OPCOES = RAIZ / "instance" / "backup.cnf"

_SQL_CRIAR = (
    "CREATE USER IF NOT EXISTS 'villaju_backup'@'localhost' IDENTIFIED BY %s"
)
_SQL_TROCAR_SENHA = "ALTER USER 'villaju_backup'@'localhost' IDENTIFIED BY %s"
_SQL_PRIVILEGIOS = (
    "GRANT SELECT, SHOW VIEW, TRIGGER, LOCK TABLES, EVENT "
    "ON villaju.* TO 'villaju_backup'@'localhost'"
)


def conteudo_das_opcoes(senha: str) -> str:
    return (
        "# Gerado por scripts/configurar_backup.py. Fora do Git.\n"
        "# Usuário só de leitura; quem lê este arquivo lê o banco.\n"
        "[client]\n"
        "user=villaju_backup\n"
        f'password="{senha}"\n'
        "host=localhost\n"
    )


def restringir(arquivo: pathlib.Path) -> None:
    """Só o dono lê e escreve. No Windows, pelas permissões NTFS."""
    if os.name == "nt":
        usuario = os.environ.get("USERNAME", "")
        subprocess.run(  # nosec B603 B607 - icacls do sistema, lista fixa
            ["icacls", str(arquivo), "/inheritance:r",
             "/grant:r", f"{usuario}:(R,W)"],
            check=True, capture_output=True,
        )
    else:
        arquivo.chmod(0o600)


def main() -> int:
    import mysql.connector
    from dotenv import load_dotenv

    load_dotenv(RAIZ / ".env")
    if (os.environ.get("VILLAJU_DB_NOME") or "villaju") != "villaju":
        print("Este script prepara o backup do banco 'villaju'.", file=sys.stderr)
        return 1

    senha_root = getpass.getpass("Senha de root do MySQL: ")
    senha = secrets.token_urlsafe(32)
    conexao = mysql.connector.connect(
        host="localhost", user="root", password=senha_root
    )
    try:
        cursor = conexao.cursor()
        cursor.execute(_SQL_CRIAR, (senha,))
        cursor.execute(_SQL_TROCAR_SENHA, (senha,))
        cursor.execute(_SQL_PRIVILEGIOS)
        cursor.execute("SHOW GRANTS FOR 'villaju_backup'@'localhost'")
        concedidos = [linha[0] for linha in cursor.fetchall()]
        conexao.commit()
        cursor.close()
    finally:
        conexao.close()

    OPCOES.parent.mkdir(parents=True, exist_ok=True)
    OPCOES.write_text(conteudo_das_opcoes(senha), encoding="utf-8")
    restringir(OPCOES)

    print("Usuário villaju_backup pronto, com:")
    for linha in concedidos:
        print("  ", linha)
    print(f"Opções gravadas em {OPCOES} (só o seu usuário lê).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
