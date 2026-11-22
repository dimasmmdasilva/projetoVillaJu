"""Restaura um backup e confere — Etapa 5, Bloco 18c (RNF06).

    venv\\Scripts\\python scripts\\restaurar.py <arquivo.sql>
    venv\\Scripts\\python scripts\\restaurar.py <arquivo.sql> --banco-em-uso

**Restaurar é sempre manual e deliberado**, porque sobrescreve dados. Sem
opção, a cópia vai para um banco **novo e separado**, `villaju_restaurado`
— é o teste que o RNF06 exige, e não toca o banco em uso. Para restaurar
por cima do `villaju` (depois de uma perda real), é preciso pedir
`--banco-em-uso` **e** digitar o nome do banco para confirmar.

Pede a senha de root, sem eco. O `mysql` recebe a senha por um arquivo de
opções temporário, só do dono, apagado no fim — nunca pela linha de
comando. O arquivo de backup entra pela entrada padrão, em bytes: nada de
`Get-Content | mysql`, que estraga os acentos no PowerShell 5.1.

**Depois de restaurar, confere** contra o banco em uso:

- as mesmas tabelas, com as mesmas contagens de linhas;
- os mesmos gatilhos;
- e que os dados pessoais restaurados **se decifram** com a chave do
  `.env`. Um backup que devolve bytes que ninguém consegue ler não é
  backup.

Os nomes dos bancos e das tabelas estão escritos por extenso: nenhum texto
montado chega a um `execute`. O banco de cada consulta é escolhido pela
conexão, não pelo SQL.
"""

import argparse
import getpass
import os
import pathlib
import shutil
import subprocess  # nosec B404  # chama o mysql com lista fixa de argumentos
import sys
import tempfile

RAIZ = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))

BANCO_EM_USO = "villaju"
BANCO_DE_TESTE_DA_RESTAURACAO = "villaju_restaurado"

_SQL_CRIAR_RESTAURADO = (
    "CREATE DATABASE IF NOT EXISTS villaju_restaurado "
    "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
)

# Uma contagem por tabela, por extenso.
#
# Montado a partir de pares, e não como `{...}`: no dicionário literal, o
# Bandit lia a CHAVE "token_acesso" como nome de senha (B105, falso
# positivo), e a única saída era uma exceção do Bandit (nosec) valendo
# para o bloco inteiro. Sem chave literal, não há o que suprimir (Relatório 1,
# achado R1-02).
CONTAGENS = dict((
    ("chale", "SELECT COUNT(*) FROM chale"),
    ("tarifa", "SELECT COUNT(*) FROM tarifa"),
    ("hospede", "SELECT COUNT(*) FROM hospede"),
    ("reserva", "SELECT COUNT(*) FROM reserva"),
    ("usuario", "SELECT COUNT(*) FROM usuario"),
    ("log_auditoria", "SELECT COUNT(*) FROM log_auditoria"),
    ("contato", "SELECT COUNT(*) FROM contato"),
    ("token_acesso", "SELECT COUNT(*) FROM token_acesso"),
    ("pagamento", "SELECT COUNT(*) FROM pagamento"),
    ("acompanhante", "SELECT COUNT(*) FROM acompanhante"),
    ("mensagem", "SELECT COUNT(*) FROM mensagem"),
))
_SQL_GATILHOS = (
    "SELECT trigger_name, MD5(action_statement) FROM information_schema.triggers "
    "WHERE trigger_schema = DATABASE() ORDER BY trigger_name"
)
_SQL_HOSPEDES = "SELECT id, nome_cif, nome_nonce, nome_tag FROM hospede"
_SQL_ACOMPANHANTES = (
    "SELECT reserva_id, ordem, nome_cif, nome_nonce, nome_tag FROM acompanhante"
)
_SQL_MENSAGENS = (
    "SELECT reserva_id, ordem, autor_id, texto_cif, texto_nonce, texto_tag "
    "FROM mensagem"
)


class RestauracaoRecusada(Exception):
    """O pedido de restauração não passa; a frase diz por quê."""


def destino(banco_em_uso: bool, confirmacao: str = None) -> str:
    """Para onde a cópia vai. O banco em uso só com o nome digitado."""
    if not banco_em_uso:
        return BANCO_DE_TESTE_DA_RESTAURACAO
    if confirmacao != BANCO_EM_USO:
        raise RestauracaoRecusada(
            "Restauração por cima do banco em uso cancelada: o nome digitado "
            "não confere."
        )
    return BANCO_EM_USO


def _arquivo_de_opcoes(senha: str) -> pathlib.Path:
    descritor, caminho = tempfile.mkstemp(suffix=".cnf")
    with os.fdopen(descritor, "w", encoding="utf-8") as f:
        f.write(f'[client]\nuser=root\npassword="{senha}"\nhost=localhost\n')
    return pathlib.Path(caminho)


def carregar(arquivo: pathlib.Path, banco: str, senha: str,
             executar=subprocess.run) -> None:
    mysql = os.environ.get("VILLAJU_MYSQL") or shutil.which("mysql")
    if not mysql:
        raise RestauracaoRecusada("cliente mysql não encontrado no PATH.")
    opcoes = _arquivo_de_opcoes(senha)
    try:
        with arquivo.open("rb") as entrada:
            resultado = executar(  # lista fixa, sem shell
                [mysql, f"--defaults-extra-file={opcoes}",
                 "--default-character-set=utf8mb4", banco],
                stdin=entrada, capture_output=True,
            )
    finally:
        opcoes.unlink(missing_ok=True)
    if resultado.returncode != 0:
        raise RestauracaoRecusada(
            "o mysql recusou o arquivo: "
            + resultado.stderr.decode("utf-8", "replace").strip()[:300]
        )


def _retrato(conexao) -> dict:
    cursor = conexao.cursor()
    contagens = {}
    for tabela, sql in CONTAGENS.items():
        cursor.execute(sql)
        contagens[tabela] = cursor.fetchone()[0]
    cursor.execute(_SQL_GATILHOS)
    gatilhos = dict(cursor.fetchall())
    cursor.close()
    return {"contagens": contagens, "gatilhos": gatilhos}


def _decifra(conexao) -> dict:
    """Quantos blocos cifrados se abrem com a chave do `.env`."""
    from app.seguranca import cripto

    cursor = conexao.cursor()
    resultado = {}
    for nome, sql, aad, colunas in (
        ("hospede", _SQL_HOSPEDES,
         lambda l: cripto.aad_hospede(l[0]), (1, 2, 3)),
        ("acompanhante", _SQL_ACOMPANHANTES,
         lambda l: cripto.aad_acompanhante(l[0], l[1], "nome"), (2, 3, 4)),
        ("mensagem", _SQL_MENSAGENS,
         lambda l: cripto.aad_mensagem(l[0], l[1], l[2]), (3, 4, 5)),
    ):
        cursor.execute(sql)
        linhas = cursor.fetchall()
        abertas = 0
        for linha in linhas:
            try:
                cripto.decifrar(*(bytes(linha[i]) for i in colunas), aad(linha))
            except cripto.ErroDeCriptografia:  # inclui FalhaDeAutenticacao
                # O que não abre fica fora da conta, e a conferência acusa.
                continue
            abertas += 1
        resultado[nome] = (abertas, len(linhas))
    cursor.close()
    return resultado


def conferir(senha: str, banco: str) -> bool:
    import mysql.connector

    def conectar(nome):
        return mysql.connector.connect(host="localhost", user="root",
                                       password=senha, database=nome)

    original, restaurado = conectar(BANCO_EM_USO), conectar(banco)
    try:
        antes, depois = _retrato(original), _retrato(restaurado)
        decifrados = _decifra(restaurado)
    finally:
        original.close()
        restaurado.close()

    tudo_certo = True
    print(f"\nConferência: {BANCO_EM_USO} x {banco}")
    for tabela, quantas in antes["contagens"].items():
        igual = depois["contagens"].get(tabela) == quantas
        tudo_certo &= igual
        print(f"  {'ok ' if igual else 'DIFERENTE'} {tabela}: "
              f"{quantas} x {depois['contagens'].get(tabela)}")
    gatilhos_iguais = antes["gatilhos"] == depois["gatilhos"]
    tudo_certo &= gatilhos_iguais
    print(f"  {'ok ' if gatilhos_iguais else 'DIFERENTE'} gatilhos: "
          f"{len(antes['gatilhos'])} x {len(depois['gatilhos'])}")
    for nome, (abertas, total) in decifrados.items():
        certo = abertas == total
        tudo_certo &= certo
        print(f"  {'ok ' if certo else 'FALHOU'} {nome}: {abertas} de {total} "
              f"decifrados com a chave do .env")
    print("\nRESTAURAÇÃO CONFERIDA." if tudo_certo else "\nHÁ DIFERENÇAS — ver acima.")
    return tudo_certo


def main(argv=None) -> int:
    from dotenv import load_dotenv

    load_dotenv(RAIZ / ".env")
    leitor = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    leitor.add_argument("arquivo", type=pathlib.Path)
    leitor.add_argument("--banco-em-uso", action="store_true",
                        help="restaurar por cima do banco villaju (pede confirmação)")
    argumentos = leitor.parse_args(argv)

    if not argumentos.arquivo.is_file():
        print(f"Arquivo não encontrado: {argumentos.arquivo}", file=sys.stderr)
        return 1
    try:
        confirmacao = None
        if argumentos.banco_em_uso:
            confirmacao = input(
                "Isto SOBRESCREVE o banco em uso. Digite o nome dele para "
                "confirmar: "
            ).strip()
        banco = destino(argumentos.banco_em_uso, confirmacao)
        senha = getpass.getpass("Senha de root do MySQL: ")
        if banco == BANCO_DE_TESTE_DA_RESTAURACAO:
            import mysql.connector

            conexao = mysql.connector.connect(host="localhost", user="root",
                                              password=senha)
            cursor = conexao.cursor()
            cursor.execute(_SQL_CRIAR_RESTAURADO)
            cursor.close()
            conexao.close()
        carregar(argumentos.arquivo, banco, senha)
    except RestauracaoRecusada as erro:
        print(f"Restauração NÃO feita: {erro}", file=sys.stderr)
        return 1
    print(f"Restaurado em {banco}.")
    if banco == BANCO_DE_TESTE_DA_RESTAURACAO:
        return 0 if conferir(senha, banco) else 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
