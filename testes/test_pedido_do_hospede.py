"""O pedido de reserva feito pelo próprio hóspede — Etapa 5, Bloco 7.

As regras são do negócio, decididas em 13/11/2026:

- **7 dias ou mais de antecedência:** sinal de 50% por Pix em 90 minutos;
  o restante até 48 horas antes da entrada.
- **Menos de 7 dias:** Pix integral em 90 minutos.
- **Sem o "Fiz o Pix" em 90 minutos:** o pedido é cancelado sozinho e o
  chalé volta a ficar livre.
- **Um pedido em aberto por conta.**

O que estes testes guardam, além das regras:

1. **A ordem da varredura.** O gatilho trata pedido vencido como
   ocupação; a varredura precisa rodar ANTES da gravação, e antes da
   página que mostra disponibilidade.
2. **O dono vai no WHERE.** Trocar o número da reserva no endereço leva à
   mesma resposta de uma reserva inexistente.
3. **Nenhum valor vem do formulário.**
4. **O regulamento que o hóspede lê sai das constantes que o sistema
   aplica.**
"""

import datetime as dt
import pathlib
import re
from decimal import Decimal

import pytest

from app import create_app
from app.config import Configuracao
from app.dados import (
    auditoria_repo,
    conexao,
    hospede_repo,
    reserva_repo,
    usuario_repo,
)
from app.seguranca import senhas
from app.servicos import pedido, regulamento

pytestmark = pytest.mark.banco

SENHA = "jureia-praia-2026"
CHAVE_PIX = "pix-de-teste@exemplo.com"
DIARIA = Decimal("300.00")

DADOS = {
    "nome": "Mariana Prado",
    "documento": "529.982.247-25",
    "email": "mariana.prado@exemplo.com",
    "telefone": "(12) 99888-7766",
}
OUTRA = {
    "nome": "Pessoa Alheia",
    "documento": "111.444.777-35",
    "email": "pessoa.alheia@exemplo.com",
    "telefone": "(11) 90000-0000",
}

HOJE = dt.date.today()


def _dia(dias: int) -> str:
    return (HOJE + dt.timedelta(days=dias)).isoformat()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

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

    # Tarifa própria, em volta de hoje: a massa fictícia cobre só até
    # fevereiro de 2027, e um teste que dependesse dela quebraria com o
    # calendário. Diária redonda para a conta ser legível no teste.
    # Sem as reservas da massa fictícia: elas ocupam datas fixas (o Natal,
    # por exemplo), e um teste que pedisse "daqui a 40 dias" esbarraria
    # nelas conforme o dia em que rodasse.
    cursor.execute("DELETE FROM reserva")
    cursor.execute("DELETE FROM tarifa")
    cursor.execute("SELECT id FROM chale ORDER BY id")
    for (chale_id,) in cursor.fetchall():
        cursor.execute(
            "INSERT INTO tarifa (chale_id, data_inicio, data_fim, "
            "valor_diaria, descricao) VALUES (%s, %s, %s, %s, 'teste')",
            (chale_id, _dia(-5), _dia(400), DIARIA),
        )
    cursor.close()

    configuracao = Configuracao(modo_teste=True)
    configuracao.PIX_CHAVE = CHAVE_PIX
    conexao.iniciar_pool(configuracao)
    app = create_app(configuracao)
    app.config["SESSION_COOKIE_SECURE"] = False
    app.villaju = configuracao

    yield app
    conexao.encerrar_pool()


def _criar_conta(aplicacao, dados):
    with aplicacao.app_context():
        hospede_id = hospede_repo.criar(dict(dados))
        usuario_id = usuario_repo.criar(
            login=usuario_repo.login_de_email(dados["email"]),
            nome=dados["nome"],
            senha_hash=senhas.gerar_hash(SENHA),
            perfil="hospede",
            hospede_id=hospede_id,
            ativo=True,
        )
        usuario = usuario_repo.buscar_por_id(usuario_id)
    return usuario


@pytest.fixture
def conta(aplicacao):
    return _criar_conta(aplicacao, DADOS)


@pytest.fixture
def outra(aplicacao):
    return _criar_conta(aplicacao, OUTRA)


@pytest.fixture
def chale(aplicacao):
    """O primeiro chalé ativo, com a capacidade."""
    with aplicacao.app_context():
        with conexao.obter_cursor(dicionario=True) as cursor:
            cursor.execute(
                "SELECT id, nome, capacidade FROM chale "
                "WHERE ativo = TRUE ORDER BY id LIMIT 1"
            )
            return cursor.fetchone()


def _entrar(aplicacao, login, senha=SENHA):
    cliente = aplicacao.test_client()
    pagina = cliente.get("/login").data.decode()
    dados = {"login": login, "senha": senha}
    achado = re.search(r'name="csrf_token" value="([^"]+)"', pagina)
    if achado:
        dados["csrf_token"] = achado.group(1)
    cliente.post("/login", data=dados)
    return cliente


def _token(cliente, rota):
    pagina = cliente.get(rota).data.decode()
    achado = re.search(r'name="csrf_token" value="([^"]+)"', pagina)
    return achado.group(1) if achado else ""


@pytest.fixture
def hospede(aplicacao, conta):
    return _entrar(aplicacao, DADOS["email"])


def _escolha(chale, entrada=10, noites=2, pessoas=2, aceite=True):
    """A escolha, com os N - 1 acompanhantes que o Bloco 15 exige.

    Acompanhantes adultos, com documentos distintos entre si e do
    titular: o caso comum, que todo teste do Bloco 7 passa a exercitar.
    """
    dados = {
        "chale": str(chale["id"]),
        "entrada": _dia(entrada),
        "saida": _dia(entrada + noites),
        "pessoas": str(pessoas),
    }
    for numero in range(1, pessoas):
        dados[f"acompanhante_{numero}_nome"] = f"Acompanhante Teste {numero}"
        dados[f"acompanhante_{numero}_documento"] = f"RG{numero:07d}"
    if aceite:
        dados["aceite"] = "sim"
    return dados


def _pedir(aplicacao, usuario, chale, **kwargs):
    with aplicacao.app_context():
        return pedido.pedir(usuario, _escolha(chale, **kwargs))


def _reserva(aplicacao, identificador):
    with aplicacao.app_context():
        with conexao.obter_cursor(dicionario=True) as cursor:
            cursor.execute("SELECT * FROM reserva WHERE id = %s", (identificador,))
            return cursor.fetchone()


def _sql(aplicacao, comando, parametros=()):
    with aplicacao.app_context():
        with conexao.obter_cursor() as cursor:
            cursor.execute(comando, parametros)


def _vencer(aplicacao, identificador):
    """Leva o prazo do Pix para um minuto atrás."""
    _sql(
        aplicacao,
        "UPDATE reserva SET prazo_pagamento = NOW() - INTERVAL 1 MINUTE "
        "WHERE id = %s",
        (identificador,),
    )


def _confirmar(aplicacao, identificador):
    """O que a equipe fará no Bloco 8, aqui direto no banco."""
    _sql(
        aplicacao,
        "UPDATE reserva SET situacao = 'confirmada' WHERE id = %s",
        (identificador,),
    )


# ===========================================================================
# 1. O regulamento — as regras como funções puras
# ===========================================================================

@pytest.mark.parametrize(
    "dias,esperada",
    [(30, "sinal"), (7, "sinal"), (6, "integral"), (1, "integral"), (0, "integral")],
)
def test_a_modalidade_depende_da_antecedencia(dias, esperada):
    """7 dias é a fronteira, e ela é inclusiva do lado do sinal."""
    entrada = HOJE + dt.timedelta(days=dias)
    assert regulamento.modalidade_para(entrada, HOJE) == esperada


def test_o_restante_vence_as_23h59_de_dois_dias_antes():
    entrada = dt.date(2026, 12, 20)
    assert regulamento.prazo_do_restante(entrada) == dt.datetime(
        2026, 12, 18, 23, 59, 59
    )


@pytest.mark.parametrize(
    "dias_antes,devolve",
    [(30, True), (7, True), (6, False), (0, False)],
)
def test_a_devolucao_vale_ate_sete_dias_antes(dias_antes, devolve):
    entrada = dt.date(2026, 12, 20)
    hoje = entrada - dt.timedelta(days=dias_antes)
    assert regulamento.tem_devolucao(entrada, hoje) is devolve


def test_o_texto_do_regulamento_sai_das_constantes(monkeypatch):
    """A lição do Bloco 14: texto escrito à mão diverge da regra.

    Muda a constante e confere que o texto acompanha. Se o regulamento
    tivesse "90 minutos" escrito à mão, ele continuaria prometendo 90
    minutos enquanto o sistema cancelasse em 45.
    """
    monkeypatch.setattr(regulamento, "MINUTOS_PARA_O_PIX", 45)
    monkeypatch.setattr(regulamento, "ANTECEDENCIA_PARA_SINAL_DIAS", 10)

    texto = " ".join(
        regra["titulo"] + " " + regra["texto"] for regra in regulamento.regras()
    )

    assert "45 minutos" in texto
    assert "90 minutos" not in texto
    assert "10 dias" in texto


def test_o_regulamento_nao_fala_em_reais():
    """A página inicial não mostra preço, e o regulamento está nela."""
    for regra in regulamento.regras():
        assert "R$" not in regra["titulo"] + regra["texto"]


# ===========================================================================
# 2. Os valores
# ===========================================================================

def test_o_sinal_e_o_restante_somam_o_total_no_centavo():
    """Com centavo ímpar, o restante é a diferença, e não outros 50%."""
    primeiro, restante = pedido.dividir(Decimal("1301.01"), "sinal")

    assert primeiro == Decimal("650.51")
    assert restante == Decimal("650.50")
    assert primeiro + restante == Decimal("1301.01")


def test_o_integral_e_o_total_de_uma_vez():
    assert pedido.dividir(Decimal("900"), "integral") == (
        Decimal("900.00"), Decimal("0.00")
    )


@pytest.mark.parametrize(
    "valor,texto",
    [
        (Decimal("1300.5"), "R$ 1.300,50"),
        (Decimal("650"), "R$ 650,00"),
        (Decimal("12345678.9"), "R$ 12.345.678,90"),
        (None, "—"),
    ],
)
def test_o_valor_e_escrito_em_reais(valor, texto):
    assert pedido.em_reais(valor) == texto


@pytest.mark.parametrize(
    "segundos,texto",
    [
        (40, "1 min"),
        (60 * 72, "1 h 12 min"),
        (60 * 60 * 24 * 3 + 60 * 60 * 4, "3 dias e 4 h"),
        (0, "prazo encerrado"),
        (-5, "prazo encerrado"),
    ],
)
def test_o_tempo_restante_arredonda_para_cima(segundos, texto):
    """40 segundos mostram "1 min", e não "0 min": um prazo que ainda vale
    não pode parecer encerrado."""
    agora = dt.datetime(2026, 11, 13, 12, 0, 0)
    prazo = agora + dt.timedelta(seconds=segundos)
    assert pedido.tempo_restante(prazo, agora) == texto


# ===========================================================================
# 3. O pedido gravado
# ===========================================================================

def test_o_pedido_com_antecedencia_nasce_com_sinal(aplicacao, conta, chale):
    identificador = _pedir(aplicacao, conta, chale, entrada=10, noites=2)
    reserva = _reserva(aplicacao, identificador)

    assert reserva["situacao"] == "pendente"
    assert reserva["modalidade"] == "sinal"
    assert reserva["valor_total"] == DIARIA * 2
    assert reserva["regulamento_aceito_em"] is not None
    assert reserva["prazo_restante"] == regulamento.prazo_do_restante(
        HOJE + dt.timedelta(days=10)
    )
    assert reserva["hospede_id"] == conta["hospede_id"]
    assert reserva["criado_por"] == conta["id"]


def test_o_pedido_em_cima_da_hora_e_integral(aplicacao, conta, chale):
    identificador = _pedir(aplicacao, conta, chale, entrada=3)
    reserva = _reserva(aplicacao, identificador)

    assert reserva["modalidade"] == "integral"
    assert reserva["prazo_restante"] is None


def test_o_prazo_do_pix_e_de_90_minutos_pelo_relogio_do_banco(
    aplicacao, conta, chale
):
    """O prazo é calculado pelo banco, que é quem o confere depois."""
    identificador = _pedir(aplicacao, conta, chale)

    with aplicacao.app_context():
        with conexao.obter_cursor() as cursor:
            cursor.execute(
                "SELECT TIMESTAMPDIFF(MINUTE, criado_em, prazo_pagamento) "
                "FROM reserva WHERE id = %s",
                (identificador,),
            )
            minutos = cursor.fetchone()[0]

    assert minutos == regulamento.MINUTOS_PARA_O_PIX


def test_sem_aceite_nao_ha_pedido(aplicacao, conta, chale):
    with pytest.raises(pedido.ErroDePedido, match="regulamento"):
        _pedir(aplicacao, conta, chale, aceite=False)

    with aplicacao.app_context():
        assert reserva_repo.listar_por_hospede(conta["hospede_id"]) == []


def test_o_valor_do_formulario_e_ignorado(aplicacao, hospede, conta, chale):
    """Um campo de valor enviado à mão não chega à reserva.

    Está na lista de testes de abuso do Relatório 2: manipular campo
    oculto para pagar menos.
    """
    dados = _escolha(chale, entrada=10, noites=2)
    dados.update(
        valor_total="1.00", total="1.00", primeiro="1.00",
        csrf_token=_token(hospede, "/reservas/pedir"),
    )

    hospede.post("/reservas/pedir", data=dados)

    with aplicacao.app_context():
        reservas = reserva_repo.listar_por_hospede(conta["hospede_id"])

    assert len(reservas) == 1
    assert reservas[0]["valor_total"] == DIARIA * 2


def test_data_passada_e_recusada(aplicacao, conta, chale):
    with pytest.raises(pedido.ErroDePedido, match="já passou"):
        _pedir(aplicacao, conta, chale, entrada=-1)


def test_acima_da_capacidade_e_recusado(aplicacao, conta, chale):
    with pytest.raises(pedido.ErroDePedido, match="recebe até"):
        _pedir(aplicacao, conta, chale, pessoas=chale["capacidade"] + 1)


def test_periodo_sem_tarifa_e_recusado(aplicacao, conta, chale):
    """Sem tarifa não há valor, e sem valor não há Pix a pedir. A equipe
    pode reservar sem tarifa; o hóspede, não."""
    with pytest.raises(pedido.ErroDePedido, match="valor cadastrado"):
        _pedir(aplicacao, conta, chale, entrada=500)


def test_chale_ocupado_e_recusado(aplicacao, conta, outra, chale):
    _pedir(aplicacao, outra, chale, entrada=10, noites=3)

    with pytest.raises(pedido.ErroDePedido, match="já está reservado"):
        _pedir(aplicacao, conta, chale, entrada=11, noites=1)


def test_a_recusa_nao_diz_as_datas_da_reserva_alheia(
    aplicacao, conta, outra, chale
):
    """A mensagem do repositório foi escrita para a equipe e diz "já está
    reservado de 15/12 a 20/12". Para um hóspede, isso é saber quando
    outra pessoa vai estar na pousada.

    A recusa conta só o que a pessoa pediu: aquele chalé, naquele período,
    não está livre.
    """
    _pedir(aplicacao, outra, chale, entrada=10, noites=3)
    entrada_alheia = (HOJE + dt.timedelta(days=10)).strftime("%d/%m/%Y")
    saida_alheia = (HOJE + dt.timedelta(days=13)).strftime("%d/%m/%Y")

    with pytest.raises(pedido.ErroDePedido) as erro:
        _pedir(aplicacao, conta, chale, entrada=11, noites=1)

    assert entrada_alheia not in str(erro.value)
    assert saida_alheia not in str(erro.value)


# ===========================================================================
# 4. Um pedido em aberto por conta
# ===========================================================================

def test_um_segundo_pedido_em_aberto_e_recusado(aplicacao, conta, chale):
    _pedir(aplicacao, conta, chale, entrada=10)

    with pytest.raises(pedido.ErroDePedido, match="já tem um pedido"):
        _pedir(aplicacao, conta, chale, entrada=40)


def test_o_pedido_com_pix_informado_ainda_conta_como_aberto(
    aplicacao, conta, chale
):
    """Declarar o Pix não libera a conta para outro pedido: quem declarou
    sem pagar seguraria dois chalés até a equipe conferir."""
    identificador = _pedir(aplicacao, conta, chale, entrada=10)
    with aplicacao.app_context():
        assert pedido.informar_pix(conta, identificador, pedido.PRIMEIRO)

    with pytest.raises(pedido.ErroDePedido, match="já tem um pedido"):
        _pedir(aplicacao, conta, chale, entrada=40)


def test_desistir_libera_a_conta_para_outro_pedido(aplicacao, conta, chale):
    identificador = _pedir(aplicacao, conta, chale, entrada=10)
    with aplicacao.app_context():
        assert pedido.desistir(conta, identificador)

    assert _pedir(aplicacao, conta, chale, entrada=40)


def test_o_limite_e_por_conta(aplicacao, conta, outra, chale):
    """O pedido de uma pessoa não impede o de outra."""
    _pedir(aplicacao, conta, chale, entrada=10)
    assert _pedir(aplicacao, outra, chale, entrada=40)


# ===========================================================================
# 5. A varredura dos vencidos, e a ORDEM em que ela roda
# ===========================================================================

def test_o_pedido_vencido_sem_pix_e_cancelado(aplicacao, conta, chale):
    identificador = _pedir(aplicacao, conta, chale)
    _vencer(aplicacao, identificador)

    with aplicacao.app_context():
        assert reserva_repo.cancelar_pedidos_vencidos() == 1

    reserva = _reserva(aplicacao, identificador)
    assert reserva["situacao"] == "cancelada"
    assert reserva["motivo_cancelamento"] == "prazo_do_pix"
    assert reserva["cancelado_em"] is not None


def test_o_pedido_dentro_do_prazo_nao_e_cancelado(aplicacao, conta, chale):
    identificador = _pedir(aplicacao, conta, chale)

    with aplicacao.app_context():
        assert reserva_repo.cancelar_pedidos_vencidos() == 0

    assert _reserva(aplicacao, identificador)["situacao"] == "pendente"


def test_o_pedido_com_pix_informado_nao_vence(aplicacao, conta, chale):
    """O clique para a contagem: daí em diante, a vez é da equipe."""
    identificador = _pedir(aplicacao, conta, chale)
    with aplicacao.app_context():
        assert pedido.informar_pix(conta, identificador, pedido.PRIMEIRO)
    _vencer(aplicacao, identificador)

    with aplicacao.app_context():
        assert reserva_repo.cancelar_pedidos_vencidos() == 0

    assert _reserva(aplicacao, identificador)["situacao"] == "pendente"


def test_a_reserva_da_equipe_nunca_vence(aplicacao, conta, chale):
    """Sem modalidade, a reserva segue o fluxo manual de antes."""
    with aplicacao.app_context():
        identificador = reserva_repo.criar({
            "chale_id": chale["id"],
            "hospede_id": conta["hospede_id"],
            "data_entrada": _dia(10),
            "data_saida": _dia(12),
            "num_pessoas": 2,
            "situacao": "pendente",
        })
        assert reserva_repo.cancelar_pedidos_vencidos() == 0

    assert _reserva(aplicacao, identificador)["situacao"] == "pendente"


def test_o_pedido_novo_varre_os_vencidos_antes_de_gravar(
    aplicacao, conta, outra, chale
):
    """**A regra de ordem, verificada pelo efeito.**

    Um pedido vencido ainda ocupa o chalé para o gatilho. Se a varredura
    rodasse depois da gravação — ou não rodasse —, o pedido novo para as
    mesmas datas seria recusado por causa de um pedido que já devia estar
    cancelado.

    Chamado direto no serviço, sem requisição: assim o gancho da
    aplicação não roda, e o que se verifica é a varredura DENTRO da
    transação do pedido.
    """
    antigo = _pedir(aplicacao, outra, chale, entrada=10)
    _vencer(aplicacao, antigo)

    novo = _pedir(aplicacao, conta, chale, entrada=10)

    assert _reserva(aplicacao, antigo)["situacao"] == "cancelada"
    assert _reserva(aplicacao, novo)["situacao"] == "pendente"


def test_a_reserva_da_equipe_tambem_varre_antes_de_gravar(
    aplicacao, conta, outra, chale
):
    """A recepção não pode ser barrada por um pedido que já venceu."""
    antigo = _pedir(aplicacao, outra, chale, entrada=10)
    _vencer(aplicacao, antigo)

    with aplicacao.app_context():
        nova = reserva_repo.criar({
            "chale_id": chale["id"],
            "hospede_id": conta["hospede_id"],
            "data_entrada": _dia(10),
            "data_saida": _dia(12),
            "num_pessoas": 2,
            "situacao": "confirmada",
        })

    assert _reserva(aplicacao, nova)["situacao"] == "confirmada"


def test_a_consulta_publica_ve_o_chale_liberado(aplicacao, outra, chale):
    """O gancho roda ANTES da requisição, e por isso a página já sai certa.

    Se rodasse depois — como a varredura de cadastros —, a consulta
    seria montada com o pedido vencido ainda ocupando o chalé, e só a
    próxima mostraria a verdade.
    """
    antigo = _pedir(aplicacao, outra, chale, entrada=10)
    _vencer(aplicacao, antigo)
    aplicacao.varredura_de_pedidos["ultima"] = 0.0

    html = aplicacao.test_client().get(
        f"/disponibilidade?chale={chale['id']}"
        f"&entrada={_dia(10)}&saida={_dia(12)}"
    ).data.decode()

    assert "Disponível para esta data" in html


def test_o_gancho_respeita_a_trava_de_um_minuto(aplicacao, outra, chale):
    """A trava existe: sem ela, cada imagem e folha de estilo faria uma
    consulta de atualização."""
    import time

    antigo = _pedir(aplicacao, outra, chale, entrada=10)
    _vencer(aplicacao, antigo)
    aplicacao.varredura_de_pedidos["ultima"] = time.monotonic()

    aplicacao.test_client().get("/")

    assert _reserva(aplicacao, antigo)["situacao"] == "pendente"


# ===========================================================================
# 6. "Fiz o Pix"
# ===========================================================================

def test_informar_o_pix_dentro_do_prazo(aplicacao, conta, chale):
    identificador = _pedir(aplicacao, conta, chale)

    with aplicacao.app_context():
        assert pedido.informar_pix(conta, identificador, pedido.PRIMEIRO)

    assert _reserva(aplicacao, identificador)["pix_informado_em"] is not None


def test_informar_o_pix_fica_no_log(aplicacao, conta, chale):
    identificador = _pedir(aplicacao, conta, chale)
    with aplicacao.app_context():
        pedido.informar_pix(conta, identificador, pedido.PRIMEIRO)
        linhas = [
            linha for linha in auditoria_repo.listar(limite=50)
            if linha["acao"] == auditoria_repo.PIX_INFORMADO
        ]

    assert len(linhas) == 1
    assert linhas[0]["usuario_id"] == conta["id"]
    assert linhas[0]["registro_id"] == identificador


def test_o_pedido_e_o_aceite_ficam_no_log(aplicacao, conta, chale):
    identificador = _pedir(aplicacao, conta, chale)
    with aplicacao.app_context():
        linhas = [
            linha for linha in auditoria_repo.listar(limite=50)
            if linha["acao"] == auditoria_repo.PEDIDO_DE_RESERVA
        ]

    assert len(linhas) == 1
    assert linhas[0]["registro_id"] == identificador
    assert "regulamento aceito" in linhas[0]["detalhe"]


def test_informar_o_pix_depois_do_prazo_e_recusado(aplicacao, conta, chale):
    """A condição está no UPDATE: mesmo que a varredura ainda não tenha
    passado, o clique fora do prazo não vale."""
    identificador = _pedir(aplicacao, conta, chale)
    _vencer(aplicacao, identificador)

    with aplicacao.app_context():
        assert not pedido.informar_pix(conta, identificador, pedido.PRIMEIRO)

    assert _reserva(aplicacao, identificador)["pix_informado_em"] is None


def test_informar_o_pix_duas_vezes_nao_reescreve_o_momento(
    aplicacao, conta, chale
):
    identificador = _pedir(aplicacao, conta, chale)
    with aplicacao.app_context():
        assert pedido.informar_pix(conta, identificador, pedido.PRIMEIRO)

    # O primeiro clique é empurrado um minuto para trás. Sem isso, os dois
    # cliques caem no mesmo segundo, o banco grava o mesmo valor, informa
    # zero linhas alteradas — e a segunda declaração parece recusada por
    # acaso, mesmo com a condição `pix_informado_em IS NULL` removida. A
    # sabotagem provou: passou sem ser vista.
    _sql(
        aplicacao,
        "UPDATE reserva SET pix_informado_em = NOW() - INTERVAL 1 MINUTE "
        "WHERE id = %s",
        (identificador,),
    )
    primeiro = _reserva(aplicacao, identificador)["pix_informado_em"]

    with aplicacao.app_context():
        assert not pedido.informar_pix(conta, identificador, pedido.PRIMEIRO)

    assert _reserva(aplicacao, identificador)["pix_informado_em"] == primeiro


def test_o_restante_so_depois_da_confirmacao(aplicacao, conta, chale):
    identificador = _pedir(aplicacao, conta, chale, entrada=10)

    with aplicacao.app_context():
        assert not pedido.informar_pix(conta, identificador, pedido.RESTANTE)

    _confirmar(aplicacao, identificador)

    with aplicacao.app_context():
        assert pedido.informar_pix(conta, identificador, pedido.RESTANTE)

    assert _reserva(aplicacao, identificador)["restante_informado_em"]


def test_nao_ha_restante_no_pagamento_integral(aplicacao, conta, chale):
    identificador = _pedir(aplicacao, conta, chale, entrada=3)
    _confirmar(aplicacao, identificador)

    with aplicacao.app_context():
        assert not pedido.informar_pix(conta, identificador, pedido.RESTANTE)


def test_etapa_desconhecida_e_recusada(aplicacao, conta, chale):
    identificador = _pedir(aplicacao, conta, chale)
    with aplicacao.app_context():
        assert not pedido.informar_pix(conta, identificador, "tudo")


# ===========================================================================
# 7. O dono vai no WHERE — o número no endereço não decide nada
# ===========================================================================

def test_informar_o_pix_da_reserva_alheia_e_recusado(
    aplicacao, conta, outra, chale
):
    alheia = _pedir(aplicacao, outra, chale)

    with aplicacao.app_context():
        assert not pedido.informar_pix(conta, alheia, pedido.PRIMEIRO)

    assert _reserva(aplicacao, alheia)["pix_informado_em"] is None


def test_desistir_da_reserva_alheia_e_recusado(aplicacao, conta, outra, chale):
    alheia = _pedir(aplicacao, outra, chale)

    with aplicacao.app_context():
        assert not pedido.desistir(conta, alheia)

    assert _reserva(aplicacao, alheia)["situacao"] == "pendente"


def test_a_resposta_para_reserva_alheia_e_igual_a_da_inexistente(
    aplicacao, hospede, outra, chale
):
    """Pela rota, como quem troca o número no endereço.

    Se a resposta distinguisse, um hóspede descobriria quais números de
    reserva existem — e, com isso, quantas reservas a pousada tem.
    """
    alheia = _pedir(aplicacao, outra, chale)
    token = _token(hospede, "/reservas/minhas")

    respostas = []
    for numero in (alheia, 999999):
        resposta = hospede.post(
            f"/reservas/{numero}/pix",
            data={"etapa": "primeiro", "csrf_token": token},
            follow_redirects=True,
        )
        texto = resposta.data.decode()
        respostas.append((resposta.status_code, "não aceita mais" in texto))

    assert respostas[0] == respostas[1] == (200, True)
    assert _reserva(aplicacao, alheia)["pix_informado_em"] is None


# ===========================================================================
# 8. Desistir
# ===========================================================================

def test_desistir_cancela_e_libera_o_chale(aplicacao, conta, outra, chale):
    identificador = _pedir(aplicacao, conta, chale, entrada=10)

    with aplicacao.app_context():
        assert pedido.desistir(conta, identificador)

    reserva = _reserva(aplicacao, identificador)
    assert reserva["situacao"] == "cancelada"
    assert reserva["motivo_cancelamento"] == "pedido_do_hospede"

    # E as mesmas datas ficam livres para outra pessoa.
    assert _pedir(aplicacao, outra, chale, entrada=10)


def test_nao_se_desiste_depois_de_informar_o_pix(aplicacao, conta, chale):
    """Com Pix informado há dinheiro envolvido: o cancelamento com
    devolução é outra regra, e é o Bloco 9."""
    identificador = _pedir(aplicacao, conta, chale)
    with aplicacao.app_context():
        pedido.informar_pix(conta, identificador, pedido.PRIMEIRO)
        assert not pedido.desistir(conta, identificador)


def test_nao_se_desiste_da_reserva_da_equipe(aplicacao, conta, chale):
    with aplicacao.app_context():
        identificador = reserva_repo.criar({
            "chale_id": chale["id"],
            "hospede_id": conta["hospede_id"],
            "data_entrada": _dia(10),
            "data_saida": _dia(12),
            "num_pessoas": 2,
            "situacao": "pendente",
        })
        assert not pedido.desistir(conta, identificador)


# ===========================================================================
# 9. O card
# ===========================================================================

AGORA = dt.datetime(2026, 11, 13, 12, 0, 0)


def _linha(**campos):
    base = {
        "situacao": "pendente",
        "modalidade": "sinal",
        "valor_total": Decimal("1300.00"),
        "prazo_pagamento": AGORA + dt.timedelta(minutes=72),
        "prazo_restante": dt.datetime(2026, 11, 28, 23, 59, 59),
        "pix_informado_em": None,
        "restante_informado_em": None,
        "motivo_cancelamento": None,
    }
    base.update(campos)
    return base


def test_card_aguardando_o_sinal():
    estado = pedido.cartao(_linha(), agora=AGORA)

    assert estado["etapa"] == "aguardando_pix"
    assert estado["botao"]["etapa"] == pedido.PRIMEIRO
    assert estado["botao"]["rotulo"] == "Fiz o Pix 50% — R$ 650,00"
    assert estado["a_pagar"] == Decimal("650.00")
    assert estado["restam"] == "1 h 12 min"
    assert estado["prazo"] == "até 13h12 de hoje"
    assert estado["pode_desistir"] is True
    assert estado["atualizar"] is True


def test_card_aguardando_o_integral():
    estado = pedido.cartao(_linha(modalidade="integral", prazo_restante=None),
                           agora=AGORA)

    assert estado["botao"]["rotulo"] == "Fiz o Pix 100% — R$ 1.300,00"
    assert estado["a_pagar"] == Decimal("1300.00")


def test_card_com_pix_em_conferencia_nao_tem_botao():
    estado = pedido.cartao(
        _linha(pix_informado_em=AGORA - dt.timedelta(minutes=5)), agora=AGORA
    )

    assert estado["etapa"] == "pix_em_conferencia"
    assert estado["botao"] is None
    assert estado["pode_desistir"] is False
    assert estado["atualizar"] is False


def test_card_com_prazo_vencido_nao_tem_botao():
    estado = pedido.cartao(
        _linha(prazo_pagamento=AGORA - dt.timedelta(seconds=1)), agora=AGORA
    )

    assert estado["etapa"] == "prazo_vencido"
    assert estado["botao"] is None


def test_card_aguardando_o_restante():
    estado = pedido.cartao(_linha(situacao="confirmada"), agora=AGORA)

    assert estado["etapa"] == "aguardando_restante"
    assert estado["botao"]["etapa"] == pedido.RESTANTE
    assert estado["botao"]["rotulo"] == (
        "Fiz o Pix dos 50% restantes — R$ 650,00"
    )
    assert estado["prazo"] == "até 23h59 de 28/11/2026"
    assert estado["pode_desistir"] is False
    # Contagem de dias não precisa de atualização a cada minuto.
    assert estado["atualizar"] is False


def test_card_integral_confirmado():
    estado = pedido.cartao(
        _linha(situacao="confirmada", modalidade="integral",
               prazo_restante=None),
        agora=AGORA,
    )

    assert estado["etapa"] == "confirmada"
    assert "Esperamos você" in estado["texto"]
    assert estado["botao"] is None


def test_card_da_reserva_da_recepcao_nao_tem_botao():
    estado = pedido.cartao(
        _linha(modalidade=None, prazo_pagamento=None, prazo_restante=None),
        agora=AGORA,
    )

    assert estado["etapa"] == "pela_recepcao"
    assert estado["botao"] is None
    assert estado["pode_desistir"] is False


def test_card_cancelado_diz_o_motivo():
    estado = pedido.cartao(
        _linha(situacao="cancelada", motivo_cancelamento="prazo_do_pix"),
        agora=AGORA,
    )

    assert "não foi informado dentro do prazo" in estado["texto"]


# ===========================================================================
# 10. As telas
# ===========================================================================

def test_o_formulario_do_pedido_e_do_hospede(aplicacao, hospede):
    assert hospede.get("/reservas/pedir").status_code == 200


def test_a_equipe_nao_pede_reserva_pelo_formulario_do_hospede(aplicacao):
    recepcao = _entrar(aplicacao, "recepcao", "Villa@Recep2026")
    assert recepcao.get("/reservas/pedir").status_code == 403


def test_o_visitante_e_mandado_entrar(aplicacao):
    resposta = aplicacao.test_client().get("/reservas/pedir")
    assert resposta.status_code == 302
    assert "/login" in resposta.headers["Location"]


def _revisar(hospede, escolha):
    """O passo 4 (Bloco 15): o resumo vem do POST de revisão."""
    dados = dict(escolha)
    dados["csrf_token"] = _token(hospede, "/reservas/pedir")
    return hospede.post("/reservas/pedir/revisar", data=dados).data.decode()


def test_o_resumo_mostra_valor_prazos_e_regulamento(aplicacao, hospede, chale):
    escolha = _escolha(chale, entrada=10, noites=2, aceite=False)
    html = _revisar(hospede, escolha)

    assert "R$ 600,00" in html
    assert "R$ 300,00" in html  # o sinal
    assert "Regulamento da pousada" in html
    assert 'name="aceite"' in html
    assert "Confirmar pedido" in html


def test_o_formulario_nao_leva_valor(aplicacao, hospede, chale):
    """O POST leva chalé, datas, pessoas e quem vai — e nada que diga
    quanto custa."""
    escolha = _escolha(chale, entrada=10, aceite=False)
    html = _revisar(hospede, escolha)

    # Pelo destino, e não pelo primeiro `method="post"` da página: esse é
    # o botão "Sair" da barra superior, que também é um formulário POST.
    formulario = html[html.index('action="/reservas/pedir" class="formulario"'):]
    formulario = formulario[:formulario.index("</form>")]
    ocultos = re.findall(r'type="hidden" name="([^"]+)"', formulario)

    assert set(ocultos) == {
        "csrf_token", "chale", "entrada", "saida", "pessoas",
        "acompanhante_1_nome", "acompanhante_1_documento",
    }


def test_o_resumo_avisa_quando_nao_ha_devolucao(aplicacao, hospede, chale):
    escolha = _escolha(chale, entrada=3, aceite=False)
    html = _revisar(hospede, escolha)

    assert "Sem devolução" in html


def test_pedir_pela_tela_leva_ao_card(aplicacao, hospede, chale):
    dados = _escolha(chale, entrada=10)
    dados["csrf_token"] = _token(hospede, "/reservas/pedir")

    resposta = hospede.post("/reservas/pedir", data=dados)
    assert resposta.status_code == 302
    assert "/reservas/minhas#reserva-" in resposta.headers["Location"]

    html = hospede.get("/reservas/minhas").data.decode()
    assert CHAVE_PIX in html
    assert "Fiz o Pix 50% — R$ 300,00" in html
    assert "Desistir do pedido" in html
    assert 'http-equiv="refresh"' in html


def test_pedir_sem_aceite_pela_tela_volta_com_aviso(aplicacao, hospede, conta, chale):
    dados = _escolha(chale, aceite=False)
    dados["csrf_token"] = _token(hospede, "/reservas/pedir")

    resposta = hospede.post("/reservas/pedir", data=dados, follow_redirects=True)

    assert "aceitar o regulamento" in resposta.data.decode()
    with aplicacao.app_context():
        assert reserva_repo.listar_por_hospede(conta["hospede_id"]) == []


def test_pedir_sem_token_csrf_e_recusado(aplicacao, hospede, conta, chale):
    """A configuração de teste desliga a proteção, para que os demais
    testes não precisem buscar o token a cada envio. Aqui ela é religada:
    o Flask-WTF consulta a chave a cada requisição."""
    aplicacao.config["WTF_CSRF_ENABLED"] = True

    resposta = hospede.post("/reservas/pedir", data=_escolha(chale))

    assert resposta.status_code == 400
    with aplicacao.app_context():
        assert reserva_repo.listar_por_hospede(conta["hospede_id"]) == []


def test_o_botao_fiz_o_pix_pela_tela(aplicacao, hospede, conta, chale):
    identificador = _pedir(aplicacao, conta, chale)

    hospede.post(
        f"/reservas/{identificador}/pix",
        data={"etapa": "primeiro",
              "csrf_token": _token(hospede, "/reservas/minhas")},
    )

    html = hospede.get("/reservas/minhas").data.decode()
    assert "A equipe vai conferir" in html
    assert "Fiz o Pix 50%" not in html
    assert 'http-equiv="refresh"' not in html


def test_sem_chave_configurada_o_card_manda_falar_com_a_recepcao(
    aplicacao, hospede, conta, chale
):
    """Sem valor padrão no código: uma chave fictícia que chegasse à
    produção mandaria o dinheiro do hóspede para um desconhecido."""
    _pedir(aplicacao, conta, chale)
    aplicacao.villaju.PIX_CHAVE = ""

    html = hospede.get("/reservas/minhas").data.decode()

    assert "Fale com a recepção para receber a chave Pix" in html
    assert CHAVE_PIX not in html


def test_a_chave_pix_nao_tem_valor_padrao_no_codigo(monkeypatch):
    monkeypatch.delenv("VILLAJU_PIX_CHAVE", raising=False)
    assert Configuracao(modo_teste=True).PIX_CHAVE == ""


def test_a_pagina_inicial_mostra_o_regulamento(aplicacao):
    html = aplicacao.test_client().get("/").data.decode()

    assert "Regulamento da pousada" in html
    assert f"{regulamento.MINUTOS_PARA_O_PIX} minutos" in html
    assert 'href="/#regulamento"' in html


def test_a_consulta_oferece_reservar_a_quem_e_hospede(aplicacao, hospede, chale):
    endereco = (
        f"/disponibilidade?chale={chale['id']}"
        f"&entrada={_dia(10)}&saida={_dia(12)}"
    )

    html = hospede.get(endereco).data.decode()
    assert "Reservar este chalé" in html
    assert "/reservas/pedir?" in html

    visitante = aplicacao.test_client().get(endereco).data.decode()
    assert "Reservar este chalé" not in visitante
    assert "Criar conta" in visitante


def test_o_pedido_e_limitado_por_taxa():
    """Verificado na fonte, como os demais: em modo de teste o limitador
    fica desligado, e religá-lo mediria a configuração, não a rota."""
    fonte = (
        pathlib.Path(__file__).resolve().parents[1] / "app" / "rotas" / "reservas.py"
    ).read_text(encoding="utf-8")

    corte = fonte.index("def pedir(")
    decoradores = fonte[:corte].rsplit("@reservas.route", 1)[-1]
    assert "@limiter.limit" in decoradores
