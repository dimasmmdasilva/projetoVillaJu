"""A conversa da reserva — Etapa 5, Bloco 17.

O que estes testes guardam:

1. **Só o dono da reserva conversa**, e a reserva alheia responde como
   inexistente. A equipe responde em qualquer reserva; o visitante, em
   nenhuma.
2. **O texto fica cifrado**, amarrado à reserva, à posição e ao autor:
   copiado para outra reserva, não se decifra — e a conversa mostra
   "ilegível" em vez de cair.
3. **O que foi dito não muda.** Nem pela aplicação (privilégio por coluna,
   em test_privilegios) nem por quem tiver mais privilégio (o gatilho).
4. **A mensagem não lida leva a reserva a "Precisa de ação"**, e sai de lá
   quando a equipe responde ou marca como lida.
5. **O log diz que houve mensagem, nunca o texto.**
6. **O formulário público de contato e a tela "Contatos" saíram**, e
   "Fale com a pousada" mostra só os contatos, provisórios.
"""

import re

import mysql.connector
import pytest

from app.dados import conexao, mensagem_repo
from app.servicos import conversa

from test_pedido_do_hospede import (  # noqa: F401
    DADOS,
    OUTRA,
    SENHA,
    _entrar,
    _pedir,
    _token,
    aplicacao,
    chale,
    conta,
    outra,
)

pytestmark = pytest.mark.banco

ADMIN = ("admin", "Villa@Admin2026")
RECEPCAO = ("recepcao", "Villa@Recep2026")
SEGREDO = "Chegamos às 22h com um cachorro pequeno"


@pytest.fixture
def hospede(aplicacao, conta):
    return _entrar(aplicacao, DADOS["email"], SENHA)


@pytest.fixture
def reserva(aplicacao, conta, chale):
    return _pedir(aplicacao, conta, chale)


def _escrever(cliente, identificador, texto, rota=None):
    rota = rota or f"/reservas/{identificador}/conversa"
    pagina = (f"/reservas/{identificador}/conversa" if rota.endswith("/conversa")
              else f"/reservas/{identificador}")
    return cliente.post(rota, data={"texto": texto,
                                    "csrf_token": _token(cliente, pagina)})


def _responder(cliente, identificador, texto):
    return _escrever(cliente, identificador, texto,
                     f"/reservas/{identificador}/conversa/equipe")


def _linhas(aplicacao, comando, parametros=()):
    with aplicacao.app_context():
        with conexao.obter_cursor() as cursor:
            cursor.execute(comando, parametros)
            return cursor.fetchall()


def _sql(aplicacao, comando, parametros=()):
    with aplicacao.app_context():
        with conexao.obter_cursor() as cursor:
            cursor.execute(comando, parametros)


# ===========================================================================
# 1. Quem conversa
# ===========================================================================

def test_o_hospede_escreve_e_le_na_propria_reserva(aplicacao, hospede, reserva):
    resposta = _escrever(hospede, reserva, SEGREDO)
    assert resposta.status_code == 302
    pagina = hospede.get(f"/reservas/{reserva}/conversa").data.decode()
    assert SEGREDO in pagina
    assert "Você" in pagina


def test_a_reserva_alheia_responde_como_inexistente(aplicacao, reserva, outra):
    intrusa = _entrar(aplicacao, OUTRA["email"], SENHA)
    assert intrusa.get(f"/reservas/{reserva}/conversa").status_code == 404
    assert intrusa.get("/reservas/999999/conversa").status_code == 404
    assert _escrever(intrusa, reserva, "olá").status_code == 404
    assert _linhas(aplicacao, "SELECT id FROM mensagem") == []


def test_o_repositorio_leva_o_dono_no_where(aplicacao, conta, outra, reserva):
    with aplicacao.app_context():
        assert not mensagem_repo.enviar_do_hospede(
            reserva, outra["hospede_id"], outra["id"], "olá")
        assert mensagem_repo.conversa_do_hospede(
            reserva, outra["hospede_id"]) == []


@pytest.mark.parametrize("credenciais", [ADMIN, RECEPCAO])
def test_a_equipe_responde_e_o_hospede_ve_a_pousada(aplicacao, hospede, reserva,
                                                    credenciais):
    _escrever(hospede, reserva, SEGREDO)
    equipe = _entrar(aplicacao, *credenciais)
    assert _responder(equipe, reserva, "Pode vir, deixamos a luz acesa.").status_code == 302

    detalhe = equipe.get(f"/reservas/{reserva}").data.decode()
    assert SEGREDO in detalhe and "Pode vir" in detalhe
    assert 'id="conversa"' in detalhe

    pagina = hospede.get(f"/reservas/{reserva}/conversa").data.decode()
    assert "Pode vir" in pagina
    assert conversa.PELA_POUSADA in pagina
    assert credenciais[0] not in pagina.split("<main", 1)[1], (
        "o login de quem atendeu não vai à tela do hóspede"
    )


def test_quem_nao_e_do_perfil_nao_entra(aplicacao, hospede, reserva):
    equipe = _entrar(aplicacao, *RECEPCAO)
    assert equipe.get(f"/reservas/{reserva}/conversa").status_code == 403
    assert _responder(hospede, reserva, "eu sou a equipe").status_code == 403
    visitante = aplicacao.test_client()
    assert visitante.get(f"/reservas/{reserva}/conversa").status_code in (302, 401)
    assert _linhas(aplicacao, "SELECT id FROM mensagem") == []


def test_a_conversa_so_aceita_post_para_escrever(aplicacao, hospede, reserva):
    equipe = _entrar(aplicacao, *ADMIN)
    assert equipe.get(f"/reservas/{reserva}/conversa/equipe").status_code == 405
    assert equipe.get(f"/reservas/{reserva}/conversa/lidas").status_code == 405


# ===========================================================================
# 2. A cifra
# ===========================================================================

def test_o_texto_nao_chega_ao_banco_em_claro(aplicacao, hospede, reserva):
    _escrever(hospede, reserva, SEGREDO)
    linha = _linhas(aplicacao, "SELECT * FROM mensagem")[0]
    gravado = b"".join(v if isinstance(v, (bytes, bytearray)) else str(v).encode()
                       for v in linha if v is not None)
    assert SEGREDO.encode() not in gravado
    assert b"cachorro" not in gravado


def test_copiado_para_outra_reserva_nao_se_decifra(aplicacao, hospede, conta,
                                                   chale, reserva):
    """A etiqueta amarra o texto à reserva. Quem copiasse o bloco para a
    conversa de outra reserva não o faria aparecer lá."""
    _escrever(hospede, reserva, SEGREDO)
    _sql(aplicacao, "UPDATE reserva SET situacao = 'confirmada'")
    outra_reserva = _pedir(aplicacao, conta, chale, entrada=60)
    _sql(aplicacao,
         "INSERT INTO mensagem (reserva_id, ordem, autor_id, da_equipe, "
         " texto_cif, texto_nonce, texto_tag, lida_pela_equipe, "
         " lida_pelo_hospede) "
         "SELECT %s, ordem, autor_id, da_equipe, texto_cif, texto_nonce, "
         "       texto_tag, lida_pela_equipe, lida_pelo_hospede "
         "FROM mensagem WHERE reserva_id = %s",
         (outra_reserva, reserva))
    pagina = hospede.get(f"/reservas/{outra_reserva}/conversa")
    assert pagina.status_code == 200
    html = pagina.data.decode()
    assert SEGREDO not in html
    assert "Mensagem ilegível" in html


def test_atribuida_a_outro_autor_nao_se_decifra(aplicacao, hospede, reserva):
    _escrever(hospede, reserva, SEGREDO)
    admin_id = _linhas(aplicacao, "SELECT id FROM usuario WHERE login = 'admin'")[0][0]
    _sql(aplicacao, "DROP TRIGGER trg_mensagem_antes_alterar")
    try:
        _sql(aplicacao, "UPDATE mensagem SET autor_id = %s", (admin_id,))
    finally:
        from conftest import SCRIPT_TRIGGERS, aplicar_script
        with aplicacao.app_context():
            with conexao.obter_cursor() as cursor:
                aplicar_script(cursor, SCRIPT_TRIGGERS)
    with aplicacao.app_context():
        mensagens = mensagem_repo.conversa(reserva)
    assert [m["legivel"] for m in mensagens] == [False]


def test_o_texto_e_escapado_nas_duas_telas(aplicacao, hospede, reserva):
    ataque = '<script>alert("x")</script><img src=x onerror=alert(1)>'
    _escrever(hospede, reserva, ataque)
    equipe = _entrar(aplicacao, *ADMIN)
    for html in (hospede.get(f"/reservas/{reserva}/conversa").data.decode(),
                 equipe.get(f"/reservas/{reserva}").data.decode()):
        assert "<script>alert" not in html
        assert "<img src=x" not in html
        assert "&lt;script&gt;" in html


# ===========================================================================
# 3. O que foi dito não muda
# ===========================================================================

@pytest.mark.parametrize("mudanca", [
    "texto_tag = texto_nonce",
    "reserva_id = reserva_id + 0, ordem = ordem + 1",
    "da_equipe = TRUE",
    "criado_em = '2020-01-01 00:00:00'",
])
def test_o_gatilho_recusa_reescrever(aplicacao, hospede, reserva, mudanca):
    """Pela conta de teste, que tem todo privilégio: quem recusa é o gatilho."""
    _escrever(hospede, reserva, SEGREDO)
    with pytest.raises(mysql.connector.Error) as erro:
        _sql(aplicacao, "UPDATE mensagem SET " + mudanca)  # nosec B608 - lista fixa do teste
    assert erro.value.errno == 1644


def test_lida_nao_volta_a_nao_lida(aplicacao, hospede, reserva):
    _escrever(hospede, reserva, SEGREDO)
    _sql(aplicacao, "UPDATE mensagem SET lida_pela_equipe = TRUE")
    with pytest.raises(mysql.connector.Error) as erro:
        _sql(aplicacao, "UPDATE mensagem SET lida_pela_equipe = FALSE")
    assert erro.value.errno == 1644


def test_a_ordem_cresce_de_um_em_um(aplicacao, hospede, reserva):
    _escrever(hospede, reserva, "um")
    _responder(_entrar(aplicacao, *RECEPCAO), reserva, "dois")
    _escrever(hospede, reserva, "três")
    ordens = [o for (o,) in _linhas(
        aplicacao, "SELECT ordem FROM mensagem WHERE reserva_id = %s ORDER BY id",
        (reserva,))]
    assert ordens == [1, 2, 3]


@pytest.mark.parametrize("texto,trecho", [
    ("   ", "Escreva a mensagem"),
    ("x" * (mensagem_repo.LIMITE_DO_TEXTO + 1), "o limite é"),
])
def test_texto_vazio_ou_longo_e_recusado_sem_perder_o_digitado(
        aplicacao, hospede, reserva, texto, trecho):
    resposta = _escrever(hospede, reserva, texto)
    assert resposta.status_code == 400
    html = resposta.data.decode()
    assert trecho in html
    if texto.strip():
        assert texto in html, "o texto digitado volta ao campo"
    assert _linhas(aplicacao, "SELECT id FROM mensagem") == []


# ===========================================================================
# 4. A não lida no Painel
# ===========================================================================

def _grupo_de_acao(html):
    inicio = html.index("Precisa de a")
    fim = html.index("Próximas estadias", inicio) if "Próximas estadias" in html[inicio:] else len(html)
    return html[inicio:fim]


def test_a_nao_lida_leva_a_precisa_de_acao(aplicacao, hospede, reserva):
    equipe = _entrar(aplicacao, *RECEPCAO)
    _sql(aplicacao, "UPDATE reserva SET situacao = 'confirmada'")
    assert f"/reservas/{reserva}#conversa" not in _grupo_de_acao(
        equipe.get("/painel").data.decode())

    _escrever(hospede, reserva, SEGREDO)
    acao = _grupo_de_acao(equipe.get("/painel").data.decode())
    assert f"/reservas/{reserva}#conversa" in acao
    assert "Responder mensagem" in acao
    assert SEGREDO not in acao, "o Painel não mostra o texto"

    detalhe = equipe.get(f"/reservas/{reserva}").data.decode()
    assert "não lida" in detalhe


def test_responder_tira_de_precisa_de_acao(aplicacao, hospede, reserva):
    _sql(aplicacao, "UPDATE reserva SET situacao = 'confirmada'")
    _escrever(hospede, reserva, SEGREDO)
    equipe = _entrar(aplicacao, *RECEPCAO)
    _responder(equipe, reserva, "Combinado.")
    assert f"/reservas/{reserva}#conversa" not in _grupo_de_acao(
        equipe.get("/painel").data.decode())


def test_marcar_como_lida_tira_de_precisa_de_acao(aplicacao, hospede, reserva):
    _sql(aplicacao, "UPDATE reserva SET situacao = 'confirmada'")
    _escrever(hospede, reserva, SEGREDO)
    equipe = _entrar(aplicacao, *ADMIN)
    resposta = equipe.post(
        f"/reservas/{reserva}/conversa/lidas",
        data={"csrf_token": _token(equipe, f"/reservas/{reserva}")})
    assert resposta.status_code == 302
    assert f"/reservas/{reserva}#conversa" not in _grupo_de_acao(
        equipe.get("/painel").data.decode())
    assert "não lida" not in equipe.get(f"/reservas/{reserva}").data.decode()


def test_a_mensagem_da_equipe_nao_pede_acao(aplicacao, reserva):
    _sql(aplicacao, "UPDATE reserva SET situacao = 'confirmada'")
    equipe = _entrar(aplicacao, *ADMIN)
    _responder(equipe, reserva, "Bem-vindos!")
    assert f"/reservas/{reserva}#conversa" not in _grupo_de_acao(
        equipe.get("/painel").data.decode())


def test_o_card_do_hospede_leva_a_conversa(aplicacao, hospede, reserva):
    html = hospede.get("/reservas/minhas").data.decode()
    assert f'href="/reservas/{reserva}/conversa"' in html
    assert "Conversa com a pousada" in html


# ===========================================================================
# 5. O log
# ===========================================================================

def test_o_log_diz_que_houve_mensagem_e_nunca_o_texto(aplicacao, hospede,
                                                      reserva):
    _escrever(hospede, reserva, SEGREDO)
    _responder(_entrar(aplicacao, *RECEPCAO), reserva, "Resposta reservada")
    detalhes = [d for (d,) in _linhas(
        aplicacao, "SELECT detalhe FROM log_auditoria WHERE acao = 'MENSAGEM_ENVIADA'")]
    assert detalhes == [
        f"mensagem enviada na reserva {reserva} pelo hóspede",
        f"mensagem enviada na reserva {reserva} pela equipe",
    ]
    todos = " ".join(d or "" for (d,) in _linhas(
        aplicacao, "SELECT detalhe FROM log_auditoria"))
    assert "cachorro" not in todos and "reservada" not in todos


# ===========================================================================
# 6. O que saiu, e "Fale com a pousada"
# ===========================================================================

@pytest.mark.parametrize("rota", ["/contato", "/contato/enviado"])
def test_o_formulario_publico_de_contato_saiu(aplicacao, rota):
    visitante = aplicacao.test_client()
    assert visitante.get(rota).status_code == 404
    assert visitante.post(rota, data={"nome": "x"}).status_code in (404, 405)


def test_a_tela_contatos_da_equipe_saiu(aplicacao):
    equipe = _entrar(aplicacao, *ADMIN)
    assert equipe.get("/contatos/").status_code == 404
    painel = equipe.get("/painel").data.decode()
    assert ">Contatos<" not in painel


def test_fale_com_a_pousada_mostra_so_os_contatos(aplicacao):
    from app.rotas.publico import HOSPEDARIA

    html = aplicacao.test_client().get("/fale-com-a-pousada").data.decode()
    corpo = html.split("<main", 1)[1]
    for contato in HOSPEDARIA["contatos"]:
        assert contato["valor"] in corpo
    assert "provisórios" in corpo
    assert "<textarea" not in corpo and 'method="post"' not in corpo


def test_os_contatos_provisorios_sao_ficticios():
    """Formatos que não podem ser de ninguém: o domínio `.exemplo` não
    existe, e os números não discam para uma pessoa real."""
    from app.rotas.publico import HOSPEDARIA

    valores = {c["rotulo"]: c["valor"] for c in HOSPEDARIA["contatos"]}
    assert valores["E-mail"].endswith("@villaju.exemplo")
    assert re.fullmatch(r"\(12\) 3333-0000", valores["Telefone"])
    assert re.fullmatch(r"\(12\) 90000-0000", valores["WhatsApp"])


def test_fale_com_a_pousada_e_o_ultimo_item_do_menu(aplicacao):
    """Decisão do Dimas em 20/11: no fim, depois de "Localização"."""
    html = aplicacao.test_client().get("/").data.decode()
    menu = html[html.index("<nav>"):html.index("</nav>")]
    itens = re.findall(r">([^<>]+)</a>", menu)
    assert itens[-2:] == ["Localização", "Fale com a pousada"]
    assert "Falar com a recepção" not in html


# ===========================================================================
# 7. Bloco 17b — o aviso de "nova resposta da pousada" e o campo fixo
# ===========================================================================

AVISO = "Nova resposta da pousada"


def _abrir_painel(cliente):
    return cliente.get("/painel").data.decode()


def _abrir_minhas(cliente):
    return cliente.get("/reservas/minhas").data.decode()


def test_a_resposta_avisa_no_painel_e_no_card(aplicacao, hospede, reserva):
    _escrever(hospede, reserva, SEGREDO)
    assert AVISO not in _abrir_painel(hospede), "a própria mensagem não avisa"
    assert AVISO not in _abrir_minhas(hospede)

    _responder(_entrar(aplicacao, *RECEPCAO), reserva, "Resposta reservada")

    painel = _abrir_painel(hospede)
    assert AVISO in painel
    assert f'href="/reservas/{reserva}/conversa"' in painel
    assert "Resposta reservada" not in painel, "o aviso não mostra o texto"
    assert AVISO in _abrir_minhas(hospede)


def test_abrir_a_conversa_apaga_os_dois_avisos(aplicacao, hospede, reserva):
    _responder(_entrar(aplicacao, *ADMIN), reserva, "Bem-vindos!")

    primeira = hospede.get(f"/reservas/{reserva}/conversa").data.decode()
    assert ">nova<" in primeira, "na visita em que chega, a resposta é destacada"

    assert AVISO not in _abrir_painel(hospede)
    assert AVISO not in _abrir_minhas(hospede)
    segunda = hospede.get(f"/reservas/{reserva}/conversa").data.decode()
    assert ">nova<" not in segunda


def test_so_o_dono_apaga_o_aviso(aplicacao, conta, outra, reserva):
    with aplicacao.app_context():
        conversa.enviar_da_equipe({"id": 1}, reserva, "Olá")
        assert mensagem_repo.marcar_como_lidas_pelo_hospede(
            reserva, outra["hospede_id"]) == 0
        assert mensagem_repo.respostas_novas(outra["hospede_id"]) == []
        assert [r["reserva_id"] for r in
                mensagem_repo.respostas_novas(conta["hospede_id"])] == [reserva]


def test_lida_pelo_hospede_nao_volta(aplicacao, reserva):
    _responder(_entrar(aplicacao, *ADMIN), reserva, "Olá")
    _sql(aplicacao, "UPDATE mensagem SET lida_pelo_hospede = TRUE")
    with pytest.raises(mysql.connector.Error) as erro:
        _sql(aplicacao, "UPDATE mensagem SET lida_pelo_hospede = FALSE")
    assert erro.value.errno == 1644


def test_a_mensagem_do_hospede_nasce_lida_por_ele(aplicacao, hospede, reserva):
    _escrever(hospede, reserva, SEGREDO)
    assert _linhas(aplicacao, "SELECT lida_pelo_hospede FROM mensagem") == [(1,)]


def test_o_campo_da_conversa_tem_tamanho_fixo(aplicacao, hospede, reserva):
    """Pedido do Dimas em 21/11: largura das caixas do mural, sem o canto
    que estica o campo."""
    import pathlib
    from test_pagina_do_chale import _valor_final

    css = (pathlib.Path(__file__).resolve().parents[1]
           / "app/static/css/villaju.css").read_text(encoding="utf-8")
    seletor = ".formulario--conversa textarea"
    assert _valor_final(css, seletor, "resize") == "none"
    assert _valor_final(css, seletor, "width") == "100%"
    assert _valor_final(css, ".formulario--conversa", "max-width") == \
        _valor_final(css, ".mural-mensagem", "max-width")

    equipe = _entrar(aplicacao, *ADMIN)
    for html in (hospede.get(f"/reservas/{reserva}/conversa").data.decode(),
                 equipe.get(f"/reservas/{reserva}").data.decode()):
        assert 'class="formulario formulario--conversa"' in html
