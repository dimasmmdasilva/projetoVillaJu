"""A lista de quem vai, editada pelo administrador — Etapa 5, Bloco 15b.

Decidido pelo Dimas em 13/11: só o administrador edita — acrescentar,
remover e corrigir. Os dados do titular não se editam por aqui.

O que estes testes guardam:

1. **Só o administrador.** Recepção e hóspede recebem 403, na tela e nos
   envios.
2. **`num_pessoas` acompanha a lista**, na mesma transação, e a
   capacidade do chalé continua valendo — pelo gatilho.
3. **As regras do pedido valem na edição**: documento de adulto, sem
   repetição, diferente do titular.
4. **A correção recifra com a mesma etiqueta**, e o gatilho não deixa
   mudar reserva nem ordem de um acompanhante.
5. **O log diz quem mudou qual campo, nunca o valor.**
"""

import re

import mysql.connector
import pytest

from app.dados import acompanhante_repo, conexao
from app.servicos import pedido, quem_vai

from test_pedido_do_hospede import (  # noqa: F401
    DADOS,
    _entrar,
    _escolha,
    _reserva,
    _token,
    aplicacao,
    chale,
    conta,
    hospede,
)

pytestmark = pytest.mark.banco

ADMIN = ("admin", "Villa@Admin2026")
RECEPCAO = ("recepcao", "Villa@Recep2026")
ERRO_DE_SIGNAL = 1644


def _usuario(aplicacao, login):
    with aplicacao.app_context():
        with conexao.obter_cursor(dicionario=True) as cursor:
            cursor.execute("SELECT * FROM usuario WHERE login = %s", (login,))
            return cursor.fetchone()


@pytest.fixture
def admin(aplicacao):
    return _usuario(aplicacao, ADMIN[0])


@pytest.fixture
def reserva(aplicacao, conta, chale):
    """3 pessoas: o titular e dois acompanhantes adultos."""
    with aplicacao.app_context():
        return pedido.pedir(conta, _escolha(chale, pessoas=3))


def _lista(aplicacao, reserva_id):
    with aplicacao.app_context():
        return [(p["ordem"], p["nome"], p["documento"], p["menor_de_idade"])
                for p in acompanhante_repo.listar_da_reserva(reserva_id)]


def _pessoas(aplicacao, reserva_id):
    return _reserva(aplicacao, reserva_id)["num_pessoas"]


def _nova(nome="Pedro Novo", documento="NV1234567", menor=""):
    return {"nome": nome, "documento": documento, "menor": menor}


# ===========================================================================
# 1. As três operações
# ===========================================================================

def test_acrescentar_no_fim_sobe_as_pessoas(aplicacao, reserva, admin):
    with aplicacao.app_context():
        assert quem_vai.acrescentar(reserva, _nova(), admin) == 3
    assert _lista(aplicacao, reserva)[-1] == (3, "Pedro Novo", "NV1234567", False)
    assert _pessoas(aplicacao, reserva) == 4


def test_acrescentar_com_vaga_nao_sobe_as_pessoas(aplicacao, reserva, admin,
                                                  conexao_banco):
    """Reserva de 4 pessoas com 2 acompanhantes: há uma vaga."""
    cursor = conexao_banco.cursor()
    cursor.execute("UPDATE reserva SET num_pessoas = 4 WHERE id = %s", (reserva,))
    cursor.close()
    with aplicacao.app_context():
        quem_vai.acrescentar(reserva, _nova(), admin)
    assert _pessoas(aplicacao, reserva) == 4


def test_acrescentar_alem_da_capacidade_e_recusado(aplicacao, reserva, admin,
                                                   chale):
    """O chalé recebe 4: o titular e três. A quarta pessoa não entra, e
    nada muda — o gatilho da reserva recusa, e a transação desfaz."""
    with aplicacao.app_context():
        quem_vai.acrescentar(reserva, _nova(), admin)
        with pytest.raises(quem_vai.ErroDeLista, match="capacidade"):
            quem_vai.acrescentar(reserva, _nova("Quinta Pessoa", "QP1234567"),
                                 admin)
    assert _pessoas(aplicacao, reserva) == chale["capacidade"]
    assert len(_lista(aplicacao, reserva)) == chale["capacidade"] - 1


def test_remover_baixa_as_pessoas(aplicacao, reserva, admin):
    with aplicacao.app_context():
        assert quem_vai.remover(reserva, 1, admin)
    assert [p[0] for p in _lista(aplicacao, reserva)] == [2]
    assert _pessoas(aplicacao, reserva) == 2


def test_remover_quem_nao_esta_e_recusado(aplicacao, reserva, admin):
    with aplicacao.app_context():
        assert not quem_vai.remover(reserva, 9, admin)
    assert _pessoas(aplicacao, reserva) == 3


def test_acrescentar_depois_de_remover_nao_reusa_a_ordem(aplicacao, reserva,
                                                         admin):
    """A ordem é parte da etiqueta da cifra: nunca se reusa no meio."""
    with aplicacao.app_context():
        quem_vai.remover(reserva, 1, admin)
        assert quem_vai.acrescentar(reserva, _nova(), admin) == 3
    assert [p[0] for p in _lista(aplicacao, reserva)] == [2, 3]
    assert _pessoas(aplicacao, reserva) == 3


def test_corrigir_o_nome(aplicacao, reserva, admin):
    with aplicacao.app_context():
        assert quem_vai.corrigir(
            reserva, 1, _nova("Acompanhante Corrigido", "RG0000001"), admin)
    assert _lista(aplicacao, reserva)[0] == (
        1, "Acompanhante Corrigido", "RG0000001", False)


def test_corrigir_para_menor_sem_documento(aplicacao, reserva, admin):
    with aplicacao.app_context():
        assert quem_vai.corrigir(
            reserva, 2, _nova("Criança Teste", "", "sim"), admin)
    assert _lista(aplicacao, reserva)[1] == (2, "Criança Teste", None, True)


def test_corrigir_recifra_com_nonce_novo(aplicacao, reserva, admin,
                                         conexao_banco):
    def nonce():
        cursor = conexao_banco.cursor()
        cursor.execute("SELECT nome_nonce FROM acompanhante WHERE reserva_id = %s "
                       "AND ordem = 1", (reserva,))
        valor = cursor.fetchone()[0]
        cursor.close()
        return valor

    antes = nonce()
    with aplicacao.app_context():
        quem_vai.corrigir(reserva, 1, _nova("Outro Nome", "RG0000001"), admin)
    assert nonce() != antes
    # E continua legível: a etiqueta é a mesma.
    assert _lista(aplicacao, reserva)[0][1] == "Outro Nome"


def test_corrigir_sem_mudanca_nao_escreve_no_log(aplicacao, reserva, admin,
                                                 conexao_banco):
    with aplicacao.app_context():
        assert quem_vai.corrigir(
            reserva, 1, _nova("Acompanhante Teste 1", "RG0000001"), admin)
    cursor = conexao_banco.cursor()
    cursor.execute("SELECT COUNT(*) FROM log_auditoria "
                   "WHERE acao = 'LISTA_DE_HOSPEDES_ALTERADA'")
    assert cursor.fetchone()[0] == 0
    cursor.close()


def test_reserva_cancelada_nao_se_edita(aplicacao, reserva, admin, conta):
    with aplicacao.app_context():
        assert pedido.desistir(conta, reserva)
        with pytest.raises(quem_vai.ErroDeLista, match="não aceita"):
            quem_vai.acrescentar(reserva, _nova(), admin)
        with pytest.raises(quem_vai.ErroDeLista):
            quem_vai.remover(reserva, 1, admin)
        with pytest.raises(quem_vai.ErroDeLista):
            quem_vai.corrigir(reserva, 1, _nova(), admin)


def test_a_trava_vale_mesmo_contornando_o_servico(aplicacao, reserva, admin,
                                                  conta):
    """O repositório recusa reserva encerrada por conta própria."""
    with aplicacao.app_context():
        pedido.desistir(conta, reserva)
        with pytest.raises(acompanhante_repo.ListaNaoEditavel):
            acompanhante_repo.remover(reserva, 1, usuario_id=admin["id"])
    assert len(_lista(aplicacao, reserva)) == 2


# ===========================================================================
# 2. As regras do pedido valem na edição
# ===========================================================================

def test_adulto_sem_documento_e_recusado(aplicacao, reserva, admin):
    with aplicacao.app_context():
        with pytest.raises(quem_vai.ErroDeLista, match="informe o documento"):
            quem_vai.acrescentar(reserva, _nova(documento=""), admin)


def test_documento_repetido_e_recusado(aplicacao, reserva, admin):
    with aplicacao.app_context():
        with pytest.raises(quem_vai.ErroDeLista, match="se repete"):
            quem_vai.acrescentar(reserva, _nova(documento="RG0000002"), admin)


def test_corrigir_mantendo_o_proprio_documento_nao_e_repeticao(aplicacao,
                                                               reserva, admin):
    with aplicacao.app_context():
        assert quem_vai.corrigir(reserva, 2, _nova("Nome Novo", "RG0000002"),
                                 admin)


def test_documento_do_titular_e_recusado(aplicacao, reserva, admin):
    with aplicacao.app_context():
        with pytest.raises(quem_vai.ErroDeLista, match="documento do titular"):
            quem_vai.acrescentar(reserva, _nova(documento=DADOS["documento"]),
                                 admin)


def test_documento_fora_do_formato_e_recusado(aplicacao, reserva, admin):
    with aplicacao.app_context():
        with pytest.raises(quem_vai.ErroDeLista, match="de 5 a 20"):
            quem_vai.corrigir(reserva, 1, _nova(documento="12<34"), admin)


# ===========================================================================
# 3. O banco
# ===========================================================================

def test_o_gatilho_nao_deixa_mudar_a_ordem(aplicacao, reserva, conexao_banco):
    cursor = conexao_banco.cursor()
    with pytest.raises(mysql.connector.Error) as erro:
        cursor.execute("UPDATE acompanhante SET ordem = 7 WHERE reserva_id = %s "
                       "AND ordem = 1", (reserva,))
    cursor.close()
    assert erro.value.errno == ERRO_DE_SIGNAL
    assert "etiqueta" in erro.value.msg


def test_o_gatilho_nao_deixa_mudar_a_reserva(aplicacao, reserva, conexao_banco):
    """Recusado pelo gatilho (1644), antes mesmo da chave estrangeira."""
    cursor = conexao_banco.cursor()
    with pytest.raises(mysql.connector.Error) as erro:
        cursor.execute("UPDATE acompanhante SET reserva_id = reserva_id + 1000 "
                       "WHERE reserva_id = %s AND ordem = 1", (reserva,))
    cursor.close()
    assert erro.value.errno == ERRO_DE_SIGNAL


def test_o_gatilho_deixa_corrigir_os_campos(aplicacao, reserva, conexao_banco):
    cursor = conexao_banco.cursor()
    cursor.execute("UPDATE acompanhante SET menor_de_idade = TRUE "
                   "WHERE reserva_id = %s AND ordem = 1", (reserva,))
    assert cursor.rowcount == 1
    cursor.close()


# ===========================================================================
# 4. O log
# ===========================================================================

def test_o_log_diz_qual_campo_e_nao_o_valor(aplicacao, reserva, admin,
                                           conexao_banco):
    with aplicacao.app_context():
        quem_vai.corrigir(reserva, 1, _nova("Nome Trocado", "DOC998877"), admin)
        quem_vai.acrescentar(reserva, _nova(), admin)
        quem_vai.remover(reserva, 2, admin)

    cursor = conexao_banco.cursor()
    cursor.execute("SELECT usuario_id, detalhe FROM log_auditoria "
                   "WHERE acao = 'LISTA_DE_HOSPEDES_ALTERADA' ORDER BY id")
    linhas = cursor.fetchall()
    cursor.close()
    assert linhas == [
        (admin["id"], "Administrador corrigiu o acompanhante 1: nome, documento"),
        (admin["id"], "Administrador acrescentou o acompanhante 3"),
        (admin["id"], "Administrador removeu o acompanhante 2"),
    ]
    for _, detalhe in linhas:
        for texto in ("Nome Trocado", "DOC998877", "Pedro Novo", "NV1234567"):
            assert texto not in detalhe


# ===========================================================================
# 5. A tela e os perfis
# ===========================================================================

def test_admin_abre_a_tela(aplicacao, reserva):
    html = _entrar(aplicacao, *ADMIN).get(
        f"/reservas/{reserva}/quem-vai").data.decode()
    assert "Acompanhante Teste 1" in html and DADOS["nome"] in html
    assert html.count("Remover da lista") == 2
    assert "Acrescentar" in html


def test_a_tela_nao_edita_o_titular(aplicacao, reserva):
    """O titular aparece só como texto: nenhum formulário leva os dados dele."""
    html = _entrar(aplicacao, *ADMIN).get(
        f"/reservas/{reserva}/quem-vai").data.decode()
    assert f'value="{DADOS["nome"]}"' not in html
    assert f'value="{DADOS["documento"]}"' not in html


@pytest.mark.parametrize("rota,metodo", [
    ("/reservas/{r}/quem-vai", "get"),
    ("/reservas/{r}/quem-vai/acrescentar", "post"),
    ("/reservas/{r}/quem-vai/1/corrigir", "post"),
    ("/reservas/{r}/quem-vai/1/remover", "post"),
])
def test_recepcao_e_hospede_nao_editam(aplicacao, reserva, hospede, rota,
                                       metodo):
    for cliente in (_entrar(aplicacao, *RECEPCAO), hospede):
        resposta = getattr(cliente, metodo)(rota.format(r=reserva),
                                            data=_nova())
        assert resposta.status_code == 403
    assert len(_lista(aplicacao, reserva)) == 2


def test_acrescentar_pela_tela(aplicacao, reserva):
    admin = _entrar(aplicacao, *ADMIN)
    dados = dict(_nova(), csrf_token=_token(admin, f"/reservas/{reserva}/quem-vai"))
    resposta = admin.post(f"/reservas/{reserva}/quem-vai/acrescentar", data=dados,
                          follow_redirects=True)
    assert "Pessoa acrescentada" in resposta.data.decode()
    assert len(_lista(aplicacao, reserva)) == 3


def test_recusa_pela_tela_volta_preenchida_sem_redirecionar(aplicacao, reserva):
    admin = _entrar(aplicacao, *ADMIN)
    dados = dict(_nova(documento=""),
                 csrf_token=_token(admin, f"/reservas/{reserva}/quem-vai"))
    resposta = admin.post(f"/reservas/{reserva}/quem-vai/acrescentar", data=dados)
    html = resposta.data.decode()
    assert resposta.status_code == 200
    assert "informe o documento" in html
    assert 'value="Pedro Novo"' in html


def test_reserva_inexistente_responde_404(aplicacao):
    admin = _entrar(aplicacao, *ADMIN)
    assert admin.get("/reservas/999999/quem-vai").status_code == 404


def test_o_card_da_equipe_leva_o_admin_a_edicao(aplicacao, reserva):
    html = _entrar(aplicacao, *ADMIN).get("/pagamentos").data.decode()
    assert f"/reservas/{reserva}/quem-vai" in html
    html = _entrar(aplicacao, *RECEPCAO).get("/pagamentos").data.decode()
    assert f"/reservas/{reserva}/quem-vai" not in html


def test_a_edicao_exige_csrf(aplicacao, reserva):
    admin = _entrar(aplicacao, *ADMIN)
    aplicacao.config["WTF_CSRF_ENABLED"] = True
    try:
        resposta = admin.post(f"/reservas/{reserva}/quem-vai/1/remover")
    finally:
        aplicacao.config["WTF_CSRF_ENABLED"] = False
    assert resposta.status_code == 400
    assert len(_lista(aplicacao, reserva)) == 2


def test_o_hospede_ve_a_correcao_no_card(aplicacao, reserva, hospede, admin):
    with aplicacao.app_context():
        quem_vai.corrigir(reserva, 1, _nova("Nome Corrigido", "RG0000001"), admin)
    html = hospede.get("/reservas/minhas").data.decode()
    card = html[html.index(f'id="reserva-{reserva}"'):]
    card = card[:card.index("</article>")]
    assert "Nome Corrigido" in card
    assert re.search(r"3\s*pessoas", card)
