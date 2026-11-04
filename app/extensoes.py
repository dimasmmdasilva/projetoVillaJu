"""Extensões do Flask, instanciadas sem aplicação.

O padrão é o de fábrica: as extensões nascem aqui, sem vínculo, e são
ligadas à aplicação dentro de `create_app` por `init_app`. Isso permite
criar várias aplicações no mesmo processo — uma por teste, por exemplo —
sem que compartilhem estado.
"""

from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_wtf.csrf import CSRFProtect

# Proteção contra falsificação de requisição entre sítios.
#
# Ativada globalmente: todo formulário precisa carregar o token, inclusive
# o formulário público de contato. Sem ele, um sítio hostil conseguiria
# fazer o navegador de um recepcionista autenticado enviar uma requisição
# ao VillaJu — cancelando uma reserva, por exemplo — aproveitando o cookie
# de sessão que o navegador anexa automaticamente.
#
# O atributo SameSite=Lax do cookie cobre boa parte desse cenário, mas é
# defesa do navegador: navegadores antigos não o respeitam, e o token é
# verificado pelo próprio servidor.
csrf = CSRFProtect()


# Limitação de taxa por origem.
#
# A chave é o endereço de origem da requisição. Serve a dois propósitos
# previstos no plano: conter força bruta no login (RNF02) e conter envio
# automatizado no formulário público (RNF05).
#
# O armazenamento é a memória do próprio processo. É adequado ao cenário
# do projeto, que roda um servidor único em máquina local. Num cenário com
# vários processos, cada um teria sua contagem e o limite efetivo seria
# multiplicado pelo número de processos — aí seria preciso um
# armazenamento compartilhado, como Redis.
#
# Declarar `storage_uri` explicitamente evita o aviso que a biblioteca
# emite quando o armazenamento fica implícito, e deixa a escolha visível
# para quem lê o código.
limiter = Limiter(
    key_func=get_remote_address,
    storage_uri="memory://",
    strategy="fixed-window",
)


def registrar(app) -> None:
    """Liga as extensões à aplicação e aplica os limites configurados."""
    csrf.init_app(app)

    limiter.default_limits = [app.config["LIMITE_PADRAO"]]
    limiter.init_app(app)

    # Em modo de teste a limitação fica desligada: um teste que exercita
    # o login dez vezes seria bloqueado no sexto e falharia por motivo
    # que não tem relação com o que ele verifica. Os testes específicos de
    # limitação a religam para si.
    if app.config.get("TESTING"):
        limiter.enabled = False
