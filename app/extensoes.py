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

    # **A configuração diz sempre True, e o desligamento vem depois.**
    #
    # O `init_app` do Flask-Limiter desiste logo no começo quando
    # encontra o limitador desligado — `_extension.py`, linhas 331 a 333:
    #
    #     self.enabled = config.setdefault(ConfigVars.ENABLED, self.enabled)
    #     if not self.enabled:
    #         return
    #
    # São duas armadilhas numa só linha, e o projeto caiu nas duas.
    #
    # A primeira: `setdefault` só usa `self.enabled` quando a chave não
    # está na configuração. `limiter` é objeto de módulo, único no
    # processo. A primeira versão deste código fazia
    # `limiter.enabled = False` depois do `init_app` para o modo de
    # teste, e a PRIMEIRA aplicação de teste criada no processo deixava o
    # objeto desligado para sempre: toda aplicação criada depois —
    # inclusive uma de produção — ficava **sem limitação de taxa e sem
    # erro algum**, porque o `init_app` dela desistia na linha 333.
    #
    # Na suíte isso passava por sorte de ordem alfabética:
    # `test_aplicacao.py` vem antes de `test_auth.py`, então a aplicação
    # que o teste examinava era a primeira do processo. Um arquivo de
    # teste novo com nome anterior faria a verificação começar a falhar.
    #
    # A segunda: escrever `False` na configuração ANTES do `init_app`
    # corrige o vazamento entre aplicações, mas faz o `init_app` desistir
    # de novo — agora para a aplicação de teste —, e o limitador deixa de
    # ser registrado nela. Os decoradores `@limiter.limit` ficam sem
    # extensão por trás, e `test_defesas_do_formulario` não conseguiria
    # religá-los.
    #
    # A saída é a ordem: a configuração afirma `True`, de modo que o
    # registro sempre acontece e é sempre completo, e o desligamento do
    # modo de teste é feito no objeto, depois.
    app.config["RATELIMIT_ENABLED"] = True

    limiter.init_app(app)

    # Em modo de teste a limitação fica desligada: um teste que exercita
    # o login dez vezes seria bloqueado no sexto e falharia por motivo
    # que não tem relação com o que ele verifica. Os testes específicos
    # de limitação a religam para si.
    #
    # Fica registrado como limitação conhecida que `limiter` ser objeto de
    # módulo faz este `enabled` ser do processo e não da aplicação: duas
    # aplicações vivas ao mesmo tempo compartilham o valor. Fora dos
    # testes existe uma aplicação por processo, e a alternativa seria
    # construir o limitador dentro da fábrica — o que tiraria
    # `@limiter.limit` do alcance dos módulos de rota, onde ele precisa
    # estar para decorar as funções no momento da importação.
    limiter.enabled = not app.config.get("TESTING", False)
