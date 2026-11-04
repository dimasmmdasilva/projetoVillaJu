"""Configuração da aplicação, carregada do ambiente.

Nenhum segredo vive no código. Chaves, senha de banco e caminhos de
certificado vêm de variável de ambiente, carregadas de um arquivo .env que
nunca é versionado.

A classe falha ruidosamente quando falta configuração obrigatória. A
alternativa — recorrer a um valor padrão embutido — é uma das falhas mais
difíceis de detectar em aplicação web: o sistema funciona, os testes
passam, e só muito depois se descobre que toda sessão de produção foi
assinada com uma chave que está no repositório público.
"""

import os
import pathlib


class ConfiguracaoAusente(Exception):
    """Falta variável de ambiente obrigatória, ou ela tem valor inválido."""


RAIZ = pathlib.Path(__file__).resolve().parents[1]

OBRIGATORIAS = [
    "VILLAJU_SECRET_KEY",
    "VILLAJU_CHAVE_CIFRAGEM",
    "VILLAJU_CHAVE_INDICE",
    "VILLAJU_DB_HOST",
    "VILLAJU_DB_PORTA",
    "VILLAJU_DB_NOME",
    "VILLAJU_DB_USUARIO",
    "VILLAJU_DB_SENHA",
]


def _exigir(variavel: str) -> str:
    valor = os.environ.get(variavel)
    if not valor:
        raise ConfiguracaoAusente(
            f"{variavel} não está definida no ambiente.\n"
            f"  Crie o .env a partir do .env.example e rode:\n"
            f"    python scripts/gerar_chaves.py"
        )
    return valor


def _opcional(variavel: str, padrao: str) -> str:
    return os.environ.get(variavel) or padrao


def _inteiro(variavel: str, padrao: int) -> int:
    bruto = os.environ.get(variavel)
    if not bruto:
        return padrao
    try:
        return int(bruto)
    except ValueError as erro:
        raise ConfiguracaoAusente(
            f"{variavel} precisa ser um número inteiro, não {bruto!r}"
        ) from erro


def _booleano(variavel: str, padrao: bool = False) -> bool:
    bruto = os.environ.get(variavel)
    if bruto is None:
        return padrao
    return bruto.strip().lower() in {"1", "true", "sim", "on"}


class Configuracao:
    """Configuração efetiva da aplicação.

    Os atributos em maiúsculas são lidos pelo Flask; os demais são de uso
    interno do projeto.
    """

    def __init__(self, modo_teste: bool = False):
        self.MODO_TESTE = modo_teste

        # --- Sessão --------------------------------------------------------
        self.SECRET_KEY = _exigir("VILLAJU_SECRET_KEY")

        # Fora do alcance de JavaScript: mesmo com XSS, o cookie não é
        # legível pelo script injetado.
        self.SESSION_COOKIE_HTTPONLY = True

        # Só trafega sobre HTTPS. Em modo de teste precisa ficar desligado,
        # porque o cliente de teste do Flask não usa TLS e o navegador
        # simulado descartaria o cookie.
        self.SESSION_COOKIE_SECURE = not modo_teste

        # Reduz exposição a CSRF: o cookie não acompanha requisição vinda
        # de outro site, exceto navegação de nível superior.
        self.SESSION_COOKIE_SAMESITE = "Lax"

        # Nome próprio em vez do padrão 'session', para não anunciar o
        # framework em uso a quem inspeciona o tráfego.
        self.SESSION_COOKIE_NAME = "villaju_sessao"

        # Sessão expira em 8 horas, cobrindo um turno de trabalho. Sem
        # prazo, um navegador esquecido aberto na recepção continuaria
        # autenticado indefinidamente.
        self.PERMANENT_SESSION_LIFETIME = _inteiro(
            "VILLAJU_SESSAO_SEGUNDOS", 8 * 60 * 60
        )

        # --- Proteção contra CSRF ------------------------------------------
        self.WTF_CSRF_ENABLED = not modo_teste
        self.WTF_CSRF_TIME_LIMIT = _inteiro("VILLAJU_CSRF_SEGUNDOS", 3600)

        # --- Limites de requisição -----------------------------------------
        # Teto do corpo da requisição. Sem ele, um envio de centenas de
        # megabytes consome memória do servidor antes de qualquer validação.
        # 2 MiB é folgado para formulário e aperta contra abuso.
        self.MAX_CONTENT_LENGTH = _inteiro("VILLAJU_MAX_CORPO_BYTES", 2 * 1024 * 1024)

        # --- Modo de execução ----------------------------------------------
        self.DEBUG = _booleano("VILLAJU_DEBUG") and not modo_teste
        self.TESTING = modo_teste

        # --- Banco de dados ------------------------------------------------
        nome_do_banco = (
            _opcional("VILLAJU_DB_NOME_TESTE", "villaju_teste")
            if modo_teste
            else _exigir("VILLAJU_DB_NOME")
        )
        usuario = (
            _opcional("VILLAJU_DB_USUARIO_TESTE", "villaju_teste")
            if modo_teste
            else _exigir("VILLAJU_DB_USUARIO")
        )
        senha = (
            _opcional("VILLAJU_DB_SENHA_TESTE", "")
            if modo_teste
            else _exigir("VILLAJU_DB_SENHA")
        )

        # Em minúsculas DE PROPÓSITO. `como_dicionario` só exporta os
        # atributos em maiúsculas, e tudo o que entra no app.config do
        # Flask aparece nas páginas de erro do modo de depuração e em
        # qualquer extensão que despeje a configuração. A senha do banco
        # não pode estar lá.
        self.banco = {
            "host": _exigir("VILLAJU_DB_HOST"),
            "port": _inteiro("VILLAJU_DB_PORTA", 3306),
            "database": nome_do_banco,
            "user": usuario,
            "password": senha,
        }

        self.banco_tamanho_do_pool = _inteiro("VILLAJU_DB_POOL", 5)

        # --- TLS -------------------------------------------------------------
        self.TLS_CERT = RAIZ / _opcional("VILLAJU_TLS_CERT", "certs/villaju.crt")
        self.TLS_CHAVE = RAIZ / _opcional("VILLAJU_TLS_CHAVE", "certs/villaju.key")

        # --- Servidor --------------------------------------------------------
        self.HOST = _opcional("VILLAJU_HOST", "127.0.0.1")
        self.PORTA = _inteiro("VILLAJU_PORTA", 5000)

        # --- Limitação de taxa -----------------------------------------------
        # Valores aplicados no login e no formulário público. O armazenamento
        # é em memória do processo: suficiente para um servidor único, que é
        # o cenário do projeto.
        self.LIMITE_LOGIN = _opcional("VILLAJU_LIMITE_LOGIN", "5 per minute")
        self.LIMITE_PUBLICO = _opcional("VILLAJU_LIMITE_PUBLICO", "3 per minute")
        self.LIMITE_PADRAO = _opcional("VILLAJU_LIMITE_PADRAO", "200 per hour")

    def como_dicionario(self) -> dict:
        """Apenas os atributos em maiúsculas, que o Flask consome."""
        return {
            nome: valor
            for nome, valor in vars(self).items()
            if nome.isupper()
        }

    def resumo_seguro(self) -> dict:
        """Descrição da configuração sem revelar segredo algum.

        Serve para registro em log e para a documentação técnica. Nunca
        inclui a chave de sessão, as chaves criptográficas nem a senha do
        banco — exatamente os valores que alguém poderia querer registrar
        "só para depurar".
        """
        return {
            "modo_teste": self.MODO_TESTE,
            "debug": self.DEBUG,
            "banco": f"{self.banco['user']}@{self.banco['host']}:"
                     f"{self.banco['port']}/{self.banco['database']}",
            "cookie_httponly": self.SESSION_COOKIE_HTTPONLY,
            "cookie_secure": self.SESSION_COOKIE_SECURE,
            "cookie_samesite": self.SESSION_COOKIE_SAMESITE,
            "sessao_segundos": self.PERMANENT_SESSION_LIFETIME,
            "csrf_ativo": self.WTF_CSRF_ENABLED,
            "max_corpo_bytes": self.MAX_CONTENT_LENGTH,
            "limite_login": self.LIMITE_LOGIN,
        }


def conferir_ambiente() -> list:
    """Devolve a lista de variáveis obrigatórias que estão faltando.

    Usada pelo ponto de entrada para dar uma mensagem completa, em vez de
    reclamar de uma variável por vez e obrigar a corrigir em rodadas.
    """
    return [nome for nome in OBRIGATORIAS if not os.environ.get(nome)]
