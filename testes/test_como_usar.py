"""A página "Como usar" — Etapa 5, Bloco 18b.

O roteiro de uso da equipe, dentro do sistema. O que estes testes guardam:

1. **Só a equipe entra**, e cada perfil vê o que pode fazer: a parte do
   administrador não aparece para a recepção.
2. **Os números saem do regulamento.** Se um prazo mudar, o texto muda
   junto — um roteiro com o prazo velho ensinaria a coisa errada.
3. **O link está na barra** dos dois perfis da equipe.
"""

import pytest

from app.servicos import regulamento

from test_pedido_do_hospede import (  # noqa: F401
    DADOS,
    SENHA,
    _entrar,
    aplicacao,
    conta,
)

pytestmark = pytest.mark.banco

ADMIN = ("admin", "Villa@Admin2026")
RECEPCAO = ("recepcao", "Villa@Recep2026")


def _pagina(aplicacao, credenciais):
    return _entrar(aplicacao, *credenciais).get("/como-usar").data.decode()


@pytest.mark.parametrize("credenciais", [ADMIN, RECEPCAO])
def test_a_equipe_ve_as_tarefas_do_dia(aplicacao, credenciais):
    html = _pagina(aplicacao, credenciais)
    for tarefa in ("Um hóspede ligou para reservar", "Chegou um Pix",
                   "Cancelar uma reserva", "Tem mensagem nova",
                   "Ver a ocupação do mês", "O que o sistema faz sozinho"):
        assert tarefa in html, tarefa


def test_a_parte_do_administrador_e_so_dele(aplicacao):
    assert "Só do administrador" in _pagina(aplicacao, ADMIN)
    assert "Só do administrador" not in _pagina(aplicacao, RECEPCAO)


def test_hospede_e_visitante_nao_entram(aplicacao, conta):
    assert _entrar(aplicacao, DADOS["email"], SENHA).get("/como-usar").status_code == 403
    assert aplicacao.test_client().get("/como-usar").status_code in (302, 401)


@pytest.mark.parametrize("credenciais", [ADMIN, RECEPCAO])
def test_o_link_esta_na_barra_da_equipe(aplicacao, credenciais):
    painel = _entrar(aplicacao, *credenciais).get("/painel").data.decode()
    barra = painel[painel.index("<nav>"):painel.index("</nav>")]
    assert 'href="/como-usar"' in barra


def test_os_numeros_saem_do_regulamento(aplicacao, monkeypatch):
    """Muda TODAS as constantes, e nenhum valor antigo pode sobrar.

    A primeira versão só procurava o valor novo — e um "24 horas" escrito
    à mão numa seção passava, porque o "37 horas" aparecia em outra. A
    sabotagem achou: conferir a presença do novo não prova a ausência do
    velho.
    """
    novos = {
        "HORAS_PARA_O_PIX_PELA_RECEPCAO": 37,
        "DIAS_ANTES_DA_ENTRADA_PARA_DEVOLUCAO": 11,
        "ANTECEDENCIA_PARA_SINAL_DIAS": 9,
        "MINUTOS_PARA_O_PIX": 45,
        "PERCENTUAL_DO_SINAL": 40,
        "DIAS_ANTES_DA_ENTRADA_PARA_O_RESTANTE": 3,
    }
    for nome, valor in novos.items():
        monkeypatch.setattr(regulamento, nome, valor)
    corpo = _pagina(aplicacao, RECEPCAO).split("<main", 1)[1]
    for novo in ("37 horas", "11 dias ou mais", "9 dias ou mais", "45 minutos",
                 "40%", "72 horas"):
        assert novo in corpo, novo
    for velho in ("24 horas", "7 dias", "90 minutos", "50%", "48 horas"):
        assert velho not in corpo, f"valor fixo no texto: {velho}"


def test_a_regra_de_cancelamento_pela_pousada_esta_explicada(aplicacao):
    html = _pagina(aplicacao, RECEPCAO)
    assert "Cancelar pela pousada" in html
    assert "Cancelar a pedido do hóspede" in html
