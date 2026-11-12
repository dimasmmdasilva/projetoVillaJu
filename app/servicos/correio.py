"""Envio de mensagens, por SMTP ou para arquivo.

O sistema precisa provar que um endereço de e-mail pertence a quem se
cadastrou. Avisar não basta: se o link de confirmação aparecesse na tela,
bastaria digitar o endereço de outra pessoa para criar conta no nome dela.
**O e-mail não serve para informar — serve para provar posse.**

**Por que o módulo se chama `correio` e não `email`.** Um módulo chamado
`email` dentro do pacote sombrearia a biblioteca padrão de mesmo nome, de
onde vem `EmailMessage`. Com importação absoluta o Python resolveria
corretamente, mas o leitor não: duas coisas com o mesmo nome no mesmo
projeto é confusão esperando acontecer.

**Nenhuma dependência nova.** `smtplib`, `ssl` e `email.message` são
biblioteca padrão. O `requirements.txt` continua com dez pacotes, e a
superfície que o pip-audit examina não cresce.

**Os dois modos.**

`arquivo` grava a mensagem em `instance/emails/` e é o padrão. Existe por
uma razão concreta: a demonstração do projeto roda sem rede. Não é
improviso — é o que MailHog, Mailtrap e o backend de arquivo do Django
fazem, pelo mesmo motivo.

`smtp` envia de verdade, com STARTTLS, lendo as credenciais da
configuração.

O padrão ser `arquivo` também evita um modo de falha ruim: um padrão que
tentasse enviar falharia em toda instalação nova, e a falha apareceria no
meio de um cadastro — não na partida, onde seria fácil de ver.

**Texto puro, nunca HTML.** Sem imagem, sem folha de estilo, sem pixel de
rastreamento. Mensagem de confirmação não precisa de nada disso, e cada
recurso remoto numa mensagem informa a um terceiro que ela foi aberta,
por quem e quando.
"""

import datetime as dt
import logging
import re
import smtplib
import ssl
import unicodedata
from email import message_from_string
from email.message import EmailMessage
from email.policy import default as politica_padrao
from pathlib import Path

_registrador = logging.getLogger(__name__)

MODOS = ("arquivo", "smtp")

# Teto de tempo para a conversa com o servidor de e-mail. Sem ele, um
# servidor que aceita a conexão e não responde deixaria a requisição do
# cadastro pendurada até o navegador desistir — e a pessoa reenviaria o
# formulário achando que não funcionou.
TEMPO_LIMITE = 15


class ErroDeCorreio(Exception):
    """Não foi possível entregar a mensagem."""


def _limpar_cabecalho(valor: str) -> str:
    """Remove quebras de linha de um valor que vai para cabeçalho.

    É a defesa contra injeção de cabeçalho. Um endereço contendo uma
    quebra de linha seguida de `Bcc:` acrescentaria destinatários à
    mensagem — e quem digita o endereço é um desconhecido no formulário
    público.

    A biblioteca padrão recusa alguns desses casos por conta própria, mas
    a verificação aqui é barata e não depende de qual versão está
    instalada.
    """
    return re.sub(r"[\r\n]+", " ", str(valor or "")).strip()


def _nome_de_arquivo(destinatario: str, quando: dt.datetime) -> str:
    """Nome legível e seguro para o arquivo da mensagem.

    O endereço entra no nome para facilitar achar a mensagem na
    demonstração, mas normalizado: um endereço é texto vindo de fora, e
    `../` num nome de arquivo escreveria fora da pasta pretendida.
    """
    cru = unicodedata.normalize("NFKD", destinatario)
    cru = cru.encode("ascii", "ignore").decode("ascii")
    seguro = re.sub(r"[^A-Za-z0-9._-]+", "_", cru)

    # Sequências de pontos viram um `_`, e pontos iniciais somem. As barras
    # já foram substituídas acima, então não havia travessia — mas um nome
    # contendo `..` é mau sinal, e ferramentas de linha de comando e
    # arquivadores tratam esses nomes de formas que não valem descobrir.
    seguro = re.sub(r"\.{2,}", "_", seguro).lstrip("._-")

    seguro = seguro[:60] or "sem-destinatario"

    # Microssegundos no nome, e não apenas segundos.
    #
    # **Duas mensagens para o mesmo endereço no mesmo segundo recebiam o
    # mesmo nome, e a segunda sobrescrevia a primeira — em silêncio.** O
    # caso não é hipotético: é o que acontece quando alguém se cadastra e
    # tenta de novo em seguida, e era justamente o convite que se perdia,
    # substituído pelo aviso. Na demonstração, a caixa de saída mostrava
    # uma mensagem onde havia duas, e o link de ativação desaparecia.
    #
    # Quem encontrou foi um teste que esperava duas mensagens e viu uma.
    # Nenhuma das 32 sabotagens da Etapa 4 nem das 9 do Bloco 2 teria
    # encontrado isto: não havia proteção a remover, havia uma colisão
    # que ninguém tinha provocado.
    return f"{quando:%Y%m%d-%H%M%S-%f}-{seguro}.txt"


def montar(destinatario: str, assunto: str, corpo: str, remetente: str):
    """Monta a mensagem, sem enviá-la.

    Separado do envio para que o conteúdo possa ser verificado em teste
    sem tocar em rede nem em disco.
    """
    destinatario = _limpar_cabecalho(destinatario)
    if not destinatario:
        raise ErroDeCorreio("A mensagem precisa de um destinatário.")

    mensagem = EmailMessage()
    mensagem["To"] = destinatario
    mensagem["From"] = _limpar_cabecalho(remetente)
    mensagem["Subject"] = _limpar_cabecalho(assunto)
    mensagem["Date"] = dt.datetime.now().astimezone().strftime(
        "%a, %d %b %Y %H:%M:%S %z"
    )
    mensagem.set_content(corpo)
    return mensagem


def enviar(destinatario: str, assunto: str, corpo: str, configuracao) -> str:
    """Entrega a mensagem pelo modo configurado.

    Devolve uma descrição de onde a mensagem foi parar — o caminho do
    arquivo, ou o servidor usado. Serve ao log e à tela da caixa de
    saída, e nunca contém o corpo da mensagem.
    """
    modo = getattr(configuracao, "EMAIL_MODO", "arquivo")
    if modo not in MODOS:
        raise ErroDeCorreio(f"Modo de correio desconhecido: {modo}")

    mensagem = montar(
        destinatario, assunto, corpo, configuracao.EMAIL_REMETENTE
    )

    if modo == "arquivo":
        return _gravar_em_arquivo(mensagem, configuracao)
    return _enviar_por_smtp(mensagem, configuracao)


def _gravar_em_arquivo(mensagem, configuracao) -> str:
    pasta = Path(configuracao.EMAIL_PASTA)
    pasta.mkdir(parents=True, exist_ok=True)

    caminho = pasta / _nome_de_arquivo(mensagem["To"], dt.datetime.now())

    # Cinto e suspensório: os microssegundos resolvem o caso real, mas a
    # resolução do relógio é da plataforma e não do programa — no Windows
    # ela já foi de 15 milissegundos. Se o nome existir, procura o
    # próximo livre em vez de sobrescrever.
    #
    # A perda silenciosa de uma mensagem é o pior resultado possível
    # aqui: o sistema diz que enviou, o log diz que enviou, e o que a
    # pessoa precisava ler não está em lugar nenhum.
    # O contador entra ANTES da extensão, por recorte de texto e não com
    # `with_suffix`. A primeira versão usava `with_suffix(".txt")` e tinha
    # um defeito próprio: o nome contém o endereço, o endereço contém
    # pontos, e `with_suffix` troca o último deles — `...exemplo.com-2`
    # virava `...exemplo.txt`, perdendo o contador e podendo voltar a
    # colidir.
    # O contador é separado por **sublinhado**, e não por hífen. O motivo é
    # ordenação, e custou um defeito para aparecer.
    #
    # `listar_enviadas` desempata por nome quando o `mtime` é igual — e ele
    # é igual sempre que duas mensagens caem no mesmo tique de 15 ms do
    # relógio de arquivos do Windows, que é exatamente o caso em que o
    # contador existe.
    #
    # Comparando texto, `-` vale 45 e `.` vale 46. Com hífen,
    # `...pessoa-2.txt` fica MENOR que `...pessoa.txt`, e a ordem
    # decrescente põe a mensagem mais antiga no topo. Com sublinhado, que
    # vale 95, o contador fica maior que o ponto e a ordem do nome
    # coincide com a cronológica.
    if caminho.exists():
        raiz = caminho.name[: -len(".txt")]
        sufixo = 2
        while caminho.exists():
            caminho = pasta / f"{raiz}_{sufixo}.txt"
            sufixo += 1

    caminho.write_text(str(mensagem), encoding="utf-8")

    # O destinatário vai para o log; o corpo não. A mensagem de
    # confirmação carrega o token, e token em log é token vazado.
    _registrador.info("mensagem gravada para %s", mensagem["To"])
    return str(caminho)


def _enviar_por_smtp(mensagem, configuracao) -> str:
    if not configuracao.SMTP_HOST:
        raise ErroDeCorreio(
            "O modo de correio é `smtp`, mas nenhum servidor foi "
            "configurado. Defina VILLAJU_SMTP_HOST no ambiente."
        )

    contexto = ssl.create_default_context()

    try:
        with smtplib.SMTP(
            configuracao.SMTP_HOST,
            configuracao.SMTP_PORTA,
            timeout=TEMPO_LIMITE,
        ) as servidor:
            # STARTTLS antes de qualquer autenticação: sem isso, a senha
            # de app viajaria em texto claro até o servidor.
            servidor.starttls(context=contexto)

            if configuracao.SMTP_USUARIO:
                servidor.login(
                    configuracao.SMTP_USUARIO, configuracao.SMTP_SENHA
                )

            servidor.send_message(mensagem)

    except smtplib.SMTPAuthenticationError as erro:
        # A mensagem do servidor pode conter o usuário; a senha, não. Ainda
        # assim ela não vai para a tela — só para o log.
        _registrador.error("autenticação recusada pelo servidor de e-mail")
        raise ErroDeCorreio(
            "O servidor de e-mail recusou as credenciais."
        ) from erro
    except (smtplib.SMTPException, OSError) as erro:
        _registrador.error("falha ao enviar mensagem: %s", type(erro).__name__)
        raise ErroDeCorreio(
            "Não foi possível entregar a mensagem agora."
        ) from erro

    _registrador.info("mensagem enviada para %s", mensagem["To"])
    return f"smtp://{configuracao.SMTP_HOST}:{configuracao.SMTP_PORTA}"


# ---------------------------------------------------------------------------
# Caixa de saída
# ---------------------------------------------------------------------------

def listar_enviadas(configuracao, limite: int = 50) -> list:
    """As mensagens gravadas em modo arquivo, da mais recente para a mais
    antiga.

    Serve à tela de caixa de saída, que existe para que a demonstração
    funcione sem rede: a mensagem é produzida, aparece na lista, e o link
    de confirmação é clicável a partir dali.
    """
    pasta = Path(configuracao.EMAIL_PASTA)
    if not pasta.is_dir():
        return []

    mensagens = []
    # Ordenado por data de modificação E por nome, nessa ordem.
    #
    # **Por que o nome entra.** A granularidade de escrita de arquivo no
    # Windows é de cerca de 15 milissegundos: duas mensagens gravadas no
    # mesmo tique recebem `st_mtime` idêntico. E `sorted(reverse=True)`
    # **não inverte empates** — ele é estável, e empate preserva a ordem
    # de entrada, que vem do `glob` em ordem crescente.
    #
    # O efeito era a caixa de saída mostrar a mensagem **mais antiga
    # primeiro** justamente quando duas chegavam juntas — o contrário do
    # que a tela promete. É o caso real de quem se cadastra e tenta de
    # novo: recebe o convite e o aviso em sequência, e via o convite no
    # topo como se fosse o último.
    #
    # Quem encontrou foi um teste que falhava só na bateria completa e
    # passava isolado. A intermitência era a pista: sob carga, as duas
    # gravações caem no mesmo tique com mais frequência.
    #
    # O nome resolve porque ele carrega o carimbo de tempo com
    # microssegundos, que é mais preciso do que o `mtime` que o sistema
    # de arquivos guarda.
    for caminho in sorted(
        pasta.glob("*.txt"),
        key=lambda c: (c.stat().st_mtime, c.name),
        reverse=True,
    )[:limite]:
        # `UnicodeDecodeError` não é `OSError`: um arquivo com bytes que não
        # formam UTF-8 derrubava a listagem inteira, escondendo todas as
        # mensagens boas por causa de uma estragada.
        try:
            bruto = caminho.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            _registrador.warning("mensagem ilegível em %s", caminho.name)
            continue

        # A mensagem é analisada, não fatiada por texto. A primeira versão
        # separava cabeçalho e corpo no primeiro parágrafo em branco e lia
        # o corpo como texto puro — mas `set_content` codifica em base64
        # quando há acento, e é o caso de qualquer mensagem em português.
        # O resultado era uma caixa de saída que exibia base64 e não
        # encontrava link nenhum.
        try:
            analisada = message_from_string(bruto, policy=politica_padrao)
            corpo = analisada.get_content()
        except Exception:  # noqa: BLE001
            _registrador.warning("mensagem ilegível em %s", caminho.name)
            continue

        mensagens.append(
            {
                "arquivo": caminho.name,
                "para": str(analisada["To"] or ""),
                "assunto": str(analisada["Subject"] or ""),
                "quando": dt.datetime.fromtimestamp(caminho.stat().st_mtime),
                "corpo": corpo.strip(),
                "links": re.findall(r"https?://\S+", corpo),
            }
        )

    return mensagens
