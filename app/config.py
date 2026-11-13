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

        # --- Correio ---------------------------------------------------------
        # Dois modos. `arquivo` grava a mensagem em disco e é o padrão: o
        # sistema funciona inteiro sem nenhuma configuração de SMTP, e a
        # demonstração roda sem rede. `smtp` envia de verdade.
        #
        # O padrão ser `arquivo` não é descuido. Um padrão que tentasse
        # enviar falharia em toda instalação nova, e a falha apareceria no
        # meio de um cadastro — não na partida, onde seria fácil de ver.
        self.EMAIL_MODO = _opcional("VILLAJU_EMAIL_MODO", "arquivo")

        # Conferido aqui, e não na hora de enviar.
        #
        # Um modo escrito errado — `smpt` em vez de `smtp`, que é o erro de
        # digitação natural — passava por toda a partida sem reclamação. O
        # sistema subia, as telas abriam, e a falha só aparecia **no meio de
        # um cadastro**, quando alguém já havia preenchido o formulário.
        #
        # Isso contraria o que o cabeçalho deste módulo promete: falhar
        # ruidosamente diante de configuração inválida. A promessa estava
        # cumprida para variável AUSENTE e descumprida para variável com
        # valor errado, que é o caso mais comum dos dois.
        #
        # A lista de modos vive em `app.servicos.correio`, mas é repetida
        # aqui em vez de importada: `config` é carregado antes de tudo, e
        # importar um módulo de serviço a partir dele criaria uma
        # dependência de ida e volta. São dois valores que não mudam, e há
        # teste conferindo que as duas listas são iguais.
        MODOS_DE_CORREIO = ("arquivo", "smtp")

        if self.EMAIL_MODO not in MODOS_DE_CORREIO:
            raise ConfiguracaoAusente(
                f"VILLAJU_EMAIL_MODO tem o valor {self.EMAIL_MODO!r}, que "
                f"não é um modo de correio.\n"
                f"  Os modos válidos são: {', '.join(MODOS_DE_CORREIO)}\n"
                f"  `arquivo` grava a mensagem em disco; `smtp` envia de "
                f"verdade."
            )
        self.EMAIL_REMETENTE = _opcional(
            "VILLAJU_EMAIL_REMETENTE", "VillaJu <nao-responda@villaju.local>"
        )
        self.EMAIL_PASTA = RAIZ / _opcional(
            "VILLAJU_EMAIL_PASTA", "instance/emails"
        )

        # Endereço público do sistema, usado para montar o link de
        # confirmação. Vem da configuração porque o servidor não sabe por
        # qual endereço foi alcançado, e adivinhar pelo cabeçalho `Host`
        # permitiria a um atacante fazer o link apontar para o sítio dele.
        #
        # O padrão é `localhost`, e NÃO o valor de `VILLAJU_HOST`. São
        # coisas diferentes: `HOST` é o endereço em que o servidor escuta —
        # `127.0.0.1`, ou `0.0.0.0`, que nem sequer é navegável. O endereço
        # pelo qual se navega é outro.
        #
        # A primeira versão derivava do `HOST` e produzia links para
        # `https://127.0.0.1:5000` enquanto a pessoa navegava em
        # `https://localhost:5000`. Para o navegador são **origens
        # diferentes**: a exceção de certificado aceita numa não vale na
        # outra, e o cookie de sessão de uma não acompanha a outra. Clicar
        # no link do e-mail levava a um aviso de certificado e a uma origem
        # sem sessão.
        #
        # O certificado local cobre `localhost`, `villaju.local` e
        # `127.0.0.1`, então qualquer um funciona — o que não funciona é
        # misturá-los.
        self.EMAIL_BASE_URL = _opcional(
            "VILLAJU_EMAIL_BASE_URL", f"https://localhost:{self.PORTA}"
        )

        # --- Janela do cadastro não confirmado -------------------------------
        # Quantos minutos uma conta recém-criada tem para ser confirmada.
        # Passado o prazo, o link deixa de valer E os dados são descartados,
        # liberando o e-mail e o documento.
        #
        # **Por que em minutos, e não em horas.** Decisão de 12/11/2026. Um
        # cadastro não confirmado prende o endereço de e-mail e o documento
        # de alguém. Se a pessoa digitou o endereço errado, a segunda
        # tentativa com o documento certo seria recusada — e recusada em
        # silêncio, pela regra de resposta uniforme, sem que nem ela nem a
        # recepção conseguissem ver o motivo. Guardar por dias o que nunca
        # foi confirmado é guardar dado pessoal sem ter prova de que a
        # pessoa quis dá-lo.
        #
        # **O risco de 5 minutos é a entrega do e-mail.** O Gmail costuma
        # levar segundos, mas filtro de spam e aplicativo de celular que
        # sincroniza por agendamento podem consumir vários minutos. Por
        # isso o valor é configurável: trocar para 15 é uma linha no .env,
        # e nenhuma alteração de código.
        #
        # Se a janela expirar, nada se perde de forma definitiva — a pessoa
        # se cadastra de novo e funciona, porque o descarte libera o e-mail
        # e o documento. O custo é um incômodo, não um impasse.
        self.CADASTRO_MINUTOS = _inteiro("VILLAJU_CADASTRO_MINUTOS", 5)

        # --- A chave Pix da pousada (Etapa 5, Bloco 7) ----------------------
        # Exibida ao hóspede, como texto, no card da reserva. Sem QR Code:
        # o sistema não gera cobrança, só informa para onde mandar.
        #
        # **Sem valor padrão, de propósito.** Uma chave fictícia escrita no
        # código poderia chegar à produção, e o hóspede mandaria dinheiro
        # para quem fosse dono daquele número. Sem a variável, o card diz
        # para falar com a recepção.
        self.PIX_CHAVE = _opcional("VILLAJU_PIX_CHAVE", "")

        self.SMTP_HOST = _opcional("VILLAJU_SMTP_HOST", "")
        self.SMTP_PORTA = _inteiro("VILLAJU_SMTP_PORTA", 587)
        self.SMTP_USUARIO = _opcional("VILLAJU_SMTP_USUARIO", "")
        self.SMTP_SENHA = _opcional("VILLAJU_SMTP_SENHA", "")

        # No modo `smtp`, as três precisam estar lá.
        #
        # Mesma razão do modo: meio configurado é pior do que não
        # configurado. Sem este bloco, um `.env` em que a senha de app
        # ficou de fora sobe normalmente e falha no primeiro cadastro —
        # com a pessoa achando que o sistema está quebrado, e não que
        # falta uma linha de configuração.
        #
        # A senha nunca é citada na mensagem, apenas o nome da variável.
        if self.EMAIL_MODO == "smtp":
            faltando = [
                nome
                for nome, valor in (
                    ("VILLAJU_SMTP_HOST", self.SMTP_HOST),
                    ("VILLAJU_SMTP_USUARIO", self.SMTP_USUARIO),
                    ("VILLAJU_SMTP_SENHA", self.SMTP_SENHA),
                )
                if not valor
            ]
            if faltando:
                raise ConfiguracaoAusente(
                    "VILLAJU_EMAIL_MODO está em `smtp`, mas falta "
                    "configuração do servidor:\n"
                    + "".join(f"  - {nome}\n" for nome in faltando)
                    + "  Veja a seção Correio do .env.example.\n"
                    + "  Para não depender de SMTP agora, use "
                    "VILLAJU_EMAIL_MODO=arquivo."
                )

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
            # O modo e o remetente, nunca a senha de app do SMTP: ela é
            # credencial, como a senha do banco e as chaves de cifragem.
            "email_modo": self.EMAIL_MODO,
            "email_remetente": self.EMAIL_REMETENTE,
            "smtp_configurado": bool(self.SMTP_HOST and self.SMTP_USUARIO),
            "cadastro_minutos": self.CADASTRO_MINUTOS,
        }


def conferir_ambiente() -> list:
    """Devolve a lista de variáveis obrigatórias que estão faltando.

    Usada pelo ponto de entrada para dar uma mensagem completa, em vez de
    reclamar de uma variável por vez e obrigar a corrigir em rodadas.
    """
    return [nome for nome in OBRIGATORIAS if not os.environ.get(nome)]
