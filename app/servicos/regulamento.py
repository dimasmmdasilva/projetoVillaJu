"""O regulamento da pousada: as regras de reserva e pagamento, num lugar só.

Etapa 5, Bloco 7. As regras são do negócio, decididas pelo Dimas em
13/11/2026:

- **Reserva com 7 dias ou mais de antecedência:** sinal de 50% por Pix em
  até 90 minutos; o restante até 48 horas antes da entrada.
- **Reserva com menos de 7 dias:** pagamento integral por Pix em até 90
  minutos.
- **Sem o Pix informado em 90 minutos:** o pedido é cancelado sozinho e o
  chalé volta a ficar disponível.
- **Cancelamento com 7 dias ou mais de antecedência:** devolução de tudo o
  que foi pago. **Com menos de 7 dias:** nada é devolvido — "assim dá
  tempo de outra pessoa ocupar o chalé".

---

## Por que este módulo não importa nada do projeto

A página inicial mostra o regulamento, e a face pública tem uma regra de
isolamento verificada por teste: ela não alcança repositório nenhum, nem
por caminho indireto. Um módulo de constantes e funções de data, sem
nenhuma importação de `app.dados`, pode ser lido por ela sem abrir
caminho a dado de ninguém.

## Por que o número e o texto moram juntos

No Bloco 14, o e-mail de recuperação de senha tinha "48 horas" escrito à
mão, e a sabotagem que mudou o prazo do token mostrou que a mensagem
continuaria prometendo 48 horas. Aqui o regulamento que o hóspede lê e
a regra que o sistema aplica saem **das mesmas constantes**: mudar o
prazo muda as duas coisas, ou nenhuma.
"""

import datetime as dt

# ---------------------------------------------------------------------------
# As regras
# ---------------------------------------------------------------------------

# A partir de quantos dias de antecedência a reserva pode ser paga em duas
# vezes. Abaixo disso, o pagamento é integral — e a reserva já nasce sem
# direito a devolução, porque o mesmo limite separa as duas coisas.
ANTECEDENCIA_PARA_SINAL_DIAS = 7

# Quanto tempo o hóspede tem, a partir do pedido, para fazer o Pix e
# informá-lo. Curto de propósito: enquanto o prazo corre o chalé fica
# indisponível para os outros, e um prazo longo faria do pedido sem
# pagamento uma forma barata de bloquear datas.
MINUTOS_PARA_O_PIX = 90

# O restante vence "48 horas antes da entrada", contado em DIAS DE
# CALENDÁRIO: às 23h59 de dois dias antes. O sistema não guarda horário
# de entrada, e um prazo que dependesse dele obrigaria a pessoa a
# adivinhar.
DIAS_ANTES_DA_ENTRADA_PARA_O_RESTANTE = 2

# Até quantos dias antes da entrada um cancelamento dá direito a
# devolução. Mesmo limite da modalidade, e não por coincidência.
DIAS_ANTES_DA_ENTRADA_PARA_DEVOLUCAO = 7

PERCENTUAL_DO_SINAL = 50

# Quantos pedidos sem confirmação da equipe uma conta pode ter ao mesmo
# tempo. Um pedido em aberto bloqueia o chalé; sem limite, uma conta
# sozinha poderia bloquear os cinco.
LIMITE_DE_PEDIDOS_EM_ABERTO = 1

SINAL = "sinal"
INTEGRAL = "integral"


# ---------------------------------------------------------------------------
# As datas, calculadas
# ---------------------------------------------------------------------------

def modalidade_para(entrada: dt.date, hoje: dt.date) -> str:
    """`sinal` com 7 dias ou mais de antecedência; `integral` com menos."""
    if (entrada - hoje).days >= ANTECEDENCIA_PARA_SINAL_DIAS:
        return SINAL
    return INTEGRAL


def prazo_do_restante(entrada: dt.date) -> dt.datetime:
    """Às 23h59min59s de dois dias antes da entrada."""
    dia = entrada - dt.timedelta(days=DIAS_ANTES_DA_ENTRADA_PARA_O_RESTANTE)
    return dt.datetime.combine(dia, dt.time(23, 59, 59))


def ultimo_dia_com_devolucao(entrada: dt.date) -> dt.date:
    """O último dia em que um cancelamento ainda devolve o que foi pago."""
    return entrada - dt.timedelta(days=DIAS_ANTES_DA_ENTRADA_PARA_DEVOLUCAO)


def tem_devolucao(entrada: dt.date, hoje: dt.date) -> bool:
    """Se cancelar hoje devolve o que foi pago."""
    return hoje <= ultimo_dia_com_devolucao(entrada)


# ---------------------------------------------------------------------------
# O texto
# ---------------------------------------------------------------------------

def regras() -> list:
    """O regulamento como o hóspede o lê, montado das constantes acima.

    **Nenhum valor em reais**, e isso é verificado: a página inicial não
    mostra preço. O regulamento fala de proporção e de prazo; o valor
    aparece na tela de reserva, para quem já está identificado.
    """
    horas_do_restante = DIAS_ANTES_DA_ENTRADA_PARA_O_RESTANTE * 24

    return [
        {
            "titulo": (
                f"Reservas com {ANTECEDENCIA_PARA_SINAL_DIAS} dias ou mais "
                f"de antecedência"
            ),
            "texto": (
                f"Sinal de {PERCENTUAL_DO_SINAL}% por Pix, feito em até "
                f"{MINUTOS_PARA_O_PIX} minutos depois do pedido. O restante, "
                f"até {horas_do_restante} horas antes da entrada — às 23h59 "
                f"de {DIAS_ANTES_DA_ENTRADA_PARA_O_RESTANTE} dias antes."
            ),
        },
        {
            "titulo": (
                f"Reservas com menos de {ANTECEDENCIA_PARA_SINAL_DIAS} dias "
                f"de antecedência"
            ),
            "texto": (
                f"Pagamento integral por Pix, feito em até "
                f"{MINUTOS_PARA_O_PIX} minutos depois do pedido."
            ),
        },
        {
            "titulo": "Prazo do Pix",
            "texto": (
                f"Depois de fazer o Pix, informe pelo botão no seu painel. "
                f"Sem essa informação dentro dos {MINUTOS_PARA_O_PIX} "
                f"minutos, o pedido é cancelado automaticamente e o chalé "
                f"volta a ficar disponível."
            ),
        },
        {
            "titulo": "Confirmação",
            "texto": (
                "A reserva está confirmada quando a equipe da pousada "
                "confere o Pix no extrato. Até lá, o chalé fica guardado "
                "para você."
            ),
        },
        {
            "titulo": "Cancelamento",
            "texto": (
                f"Com {DIAS_ANTES_DA_ENTRADA_PARA_DEVOLUCAO} dias ou mais de "
                f"antecedência da entrada, todo o valor pago é devolvido. "
                f"Com menos de {DIAS_ANTES_DA_ENTRADA_PARA_DEVOLUCAO} dias, "
                f"não há devolução — inclusive para reservas feitas com "
                f"menos de {DIAS_ANTES_DA_ENTRADA_PARA_DEVOLUCAO} dias de "
                f"antecedência. Assim, outra pessoa ainda tem tempo de "
                f"aproveitar o chalé."
            ),
        },
        {
            "titulo": "Um pedido por vez",
            "texto": (
                "Enquanto um pedido aguarda pagamento ou a conferência da "
                "equipe, não é possível fazer outro pela mesma conta."
            ),
        },
    ]


__all__ = [
    "ANTECEDENCIA_PARA_SINAL_DIAS",
    "DIAS_ANTES_DA_ENTRADA_PARA_DEVOLUCAO",
    "DIAS_ANTES_DA_ENTRADA_PARA_O_RESTANTE",
    "INTEGRAL",
    "LIMITE_DE_PEDIDOS_EM_ABERTO",
    "MINUTOS_PARA_O_PIX",
    "PERCENTUAL_DO_SINAL",
    "SINAL",
    "modalidade_para",
    "prazo_do_restante",
    "regras",
    "tem_devolucao",
    "ultimo_dia_com_devolucao",
]
