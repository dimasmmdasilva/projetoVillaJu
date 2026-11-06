"""O critério de conclusão da Etapa 3, executado do começo ao fim.

O plano define o critério em uma frase:

> Um recepcionista fictício consegue, do login até a confirmação, criar
> uma reserva válida e ser impedido de criar uma conflitante, com mensagem
> clara em ambos os casos.

Os outros arquivos de teste verificam peças: a trigger, o repositório, a
rota, o cálculo. Este verifica o percurso — a mesma sequência de cliques
que será demonstrada ao vivo na banca, feita pelo cliente de teste do
Flask contra a aplicação inteira, com o banco real atrás.

A diferença importa. Cada peça pode estar correta e o percurso quebrar:
basta um `redirect` apontando para a rota errada, um campo de formulário
com nome diferente do que a rota lê, ou um botão que não existe na tela.
Nada disso aparece em teste de unidade, e tudo isso aparece na
demonstração.

**Por que um teste só, longo.** Porque é um percurso. Quebrá-lo em dez
testes independentes exigiria recriar o estado anterior em cada um, e o
que se quer verificar é justamente que o estado atravessa os passos.
"""

import re
from decimal import Decimal

import pytest

from app import create_app
from app.config import Configuracao
from app.dados import conexao, hospede_repo, reserva_repo

pytestmark = pytest.mark.banco

RECEPCAO = ("recepcao", "Villa@Recep2026")

# O Dunas tem uma única reserva na massa, concluída em julho de 2026.
# Julho de 2027 está livre em todos os cinco chalés, o que mantém o
# percurso previsível, e cai inteiro na baixa temporada de 2027/2028.
DUNAS = 5
NOME_DO_CHALE = "Dunas"
ENTRADA = "2027-07-10"
SAIDA = "2027-07-15"

# Cinco noites pela diária de baixa temporada do Dunas: 5 x 280.
NOITES = 5
DIARIA = Decimal("280.00")
VALOR_ESPERADO = DIARIA * NOITES


@pytest.fixture
def aplicacao(conexao_banco):
    from conftest import (
        SCRIPT_DADOS,
        SCRIPT_SCHEMA,
        SCRIPT_TRIGGERS,
        aplicar_script,
        limpar_banco,
    )

    cursor = conexao_banco.cursor()
    limpar_banco(cursor)
    aplicar_script(cursor, SCRIPT_SCHEMA)
    aplicar_script(cursor, SCRIPT_TRIGGERS)
    aplicar_script(cursor, SCRIPT_DADOS)
    cursor.close()

    conexao.iniciar_pool(Configuracao(modo_teste=True))
    yield create_app(Configuracao(modo_teste=True))
    conexao.encerrar_pool()


def _avisos(resposta) -> list:
    return [
        texto.strip()
        for texto in re.findall(
            r'<p class="aviso aviso-\w+">(.*?)</p>', resposta.data.decode(), re.S
        )
    ]


def test_percurso_da_recepcao_do_login_a_confirmacao(aplicacao):
    sessao = aplicacao.test_client()
    pagina = sessao.get("/login")

    assert pagina.status_code == 200
    assert b"csrf_token" in pagina.data

    # --- 1. Sem sessão, nada se alcança ---------------------------------
    # O percurso começa provando que ele é necessário.
    assert sessao.get("/reservas/").status_code in (302, 401)

    # --- 2. Login -------------------------------------------------------
    entrada = sessao.post(
        "/login",
        data={"login": RECEPCAO[0], "senha": RECEPCAO[1]},
        follow_redirects=True,
    )
    assert entrada.status_code == 200
    assert "recepcionista" in entrada.data.decode()

    # --- 3. Cadastrar o hóspede ----------------------------------------
    # Nome, documento, telefone e e-mail são cifrados antes do INSERT. O
    # que se digita aqui chega ilegível ao banco; a conferência vem no
    # passo 9.
    cadastro = sessao.post(
        "/hospedes/novo",
        data={
            "nome": "Helena Queirós",
            "documento": "321.654.987-00",
            "telefone": "12 97777-3333",
            "email": "helena@exemplo.test",
        },
        follow_redirects=True,
    )
    assert cadastro.status_code == 200

    hospede = hospede_repo.buscar_por_documento("321.654.987-00")
    assert hospede is not None, "o hóspede cadastrado pela tela precisa ser achável"
    assert hospede["nome"] == "Helena Queirós"

    # --- 4. Consultar a disponibilidade --------------------------------
    # A consulta de disponibilidade lê `entrada`/`saida`; o formulário de
    # reserva lê `data_entrada`/`data_saida`. Nomes diferentes para o mesmo
    # conceito em duas telas — registrado como pendência de padronização.
    disponibilidade = sessao.get(
        f"/reservas/disponibilidade?entrada={ENTRADA}&saida={SAIDA}"
    )
    assert disponibilidade.status_code == 200
    assert NOME_DO_CHALE in disponibilidade.data.decode()

    livres_antes = {
        chale["id"]
        for chale in reserva_repo.chales_disponiveis(ENTRADA, SAIDA)
    }
    assert DUNAS in livres_antes

    # --- 5. Criar a reserva válida -------------------------------------
    criacao = sessao.post(
        "/reservas/nova",
        data={
            "chale_id": str(DUNAS),
            "hospede_id": str(hospede["id"]),
            "data_entrada": ENTRADA,
            "data_saida": SAIDA,
            "num_pessoas": "3",
            "observacoes": "Chegada prevista para o fim da tarde.",
        },
        follow_redirects=True,
    )
    assert criacao.status_code == 200

    sucesso = _avisos(criacao)
    assert sucesso, "a criação precisa confirmar em texto, não em silêncio"

    criada = next(
        reserva
        for reserva in reserva_repo.listar()
        if reserva["hospede_id"] == hospede["id"]
    )
    assert criada["situacao"] == "pendente"

    # O valor saiu do cadastro de tarifas, noite a noite. Julho é baixa
    # temporada no Dunas: cinco noites pela mesma diária, sem virada.
    assert criada["valor_total"] == VALOR_ESPERADO, (
        f"esperado {VALOR_ESPERADO}, gravado {criada['valor_total']}"
    )

    # --- 6. A mesma consulta já não mostra o chalé ----------------------
    # O passo que liga disponibilidade e reserva: se a consulta não
    # refletisse a gravação, as duas telas contariam histórias diferentes.
    livres_depois = {
        chale["id"]
        for chale in reserva_repo.chales_disponiveis(ENTRADA, SAIDA)
    }
    assert DUNAS not in livres_depois
    assert livres_depois == livres_antes - {DUNAS}

    # --- 7. Ser impedido de criar a conflitante -------------------------
    # O núcleo do critério. A mesma data, o mesmo chalé.
    conflito = sessao.post(
        "/reservas/nova",
        data={
            "chale_id": str(DUNAS),
            "hospede_id": str(hospede["id"]),
            "data_entrada": ENTRADA,
            "data_saida": SAIDA,
            "num_pessoas": "2",
            "observacoes": "",
        },
    )
    assert conflito.status_code == 409

    recusa = _avisos(conflito)
    assert recusa, "a recusa precisa aparecer na tela"
    mensagem = recusa[0]

    # A mensagem precisa dizer o que aconteceu e o que fazer.
    assert NOME_DO_CHALE in mensagem
    assert "10/07/2027" in mensagem
    assert "15/07/2027" in mensagem

    # E não pode conter nada de SQL, de driver ou de Python. É a diferença
    # entre uma recusa e um vazamento de detalhe de implementação.
    proibidos = (
        "1644",
        "45000",
        "SIGNAL",
        "Traceback",
        "mysql",
        "SELECT",
        "INSERT",
        "trigger",
    )
    for termo in proibidos:
        assert termo.lower() not in mensagem.lower(), f"a mensagem vazou {termo!r}"

    # Nada foi gravado pela tentativa.
    assert len(reserva_repo.conflitos(DUNAS, ENTRADA, SAIDA)) == 1

    # --- 8. Confirmar a reserva ----------------------------------------
    confirmacao = sessao.post(
        f"/reservas/{criada['id']}/situacao",
        data={"situacao": "confirmada"},
        follow_redirects=True,
    )
    assert confirmacao.status_code == 200
    assert reserva_repo.buscar_por_id(criada["id"])["situacao"] == "confirmada"

    # A tela de detalhe mostra a conta aberta e o nome decifrado.
    detalhe = sessao.get(f"/reservas/{criada['id']}")
    assert detalhe.status_code == 200
    html = detalhe.data.decode()
    assert "Helena Queirós" in html
    assert "confirmada" in html
    assert "R$" in html

    # --- 9. O dado pessoal continua ilegível no banco -------------------
    # O percurso inteiro passou por nome, documento, telefone e e-mail. Se
    # algum deles tiver chegado ao disco em texto claro, tudo o que foi
    # verificado acima perde sentido.
    # A varredura é sobre a linha INTEIRA, não só sobre as colunas `_cif`.
    # A primeira versão deste passo olhava apenas os quatro campos
    # cifrados, e por isso não veria um documento vazando pelo índice de
    # busca — que é coluna própria, existe justamente para ser pesquisável
    # e guarda um HMAC. Se o HMAC der lugar ao valor, a tabela passa a
    # conter o documento em claro sem que nenhuma coluna `_cif` mude.
    with conexao.obter_conexao() as ligacao:
        cursor = ligacao.cursor()
        cursor.execute("SELECT * FROM hospede WHERE id = %s", (hospede["id"],))
        linha = cursor.fetchone()
        cursor.close()

    bytes_gravados = b"".join(
        valor if isinstance(valor, bytes) else str(valor).encode("utf-8")
        for valor in linha
        if valor is not None
    )
    for claro in (b"Helena", b"Queir", b"321.654", b"97777", b"helena@"):
        assert claro not in bytes_gravados, f"{claro!r} foi gravado em texto claro"

    # --- 10. Sair --------------------------------------------------------
    saida = sessao.post("/logout", follow_redirects=True)
    assert saida.status_code == 200
    assert sessao.get("/reservas/").status_code in (302, 401)
