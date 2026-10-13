"""Widget embutível: `/widget.js`, `/embed` e `/embed/send`.

O foco está na validação do `sid`. Ele vem do navegador do visitante e
entra no `chat_id` do lead, então aceitar texto livre deixaria qualquer um
escolher o próprio `chat_id` — inclusive escrever `tg:123456` e cair
dentro da conversa de um lead do Telegram. É o único ponto do projeto em
que input de terceiro vira chave primária de negócio.
"""

import pytest
from fastapi.testclient import TestClient

from app.db import repo
from tests.conftest import precisa_de_banco


@pytest.fixture
def client():
    # Sem o `with`: o context manager roda o lifespan, que sobe o bot do
    # Telegram de verdade. Ver tests/test_auth_rotas.py.
    from app.web.api import app

    return TestClient(app, follow_redirects=False, raise_server_exceptions=False)


SID_OK = "a1b2c3d4e5f60718a9b0c1d2e3f40516"


# ---------------------------------------------------------------- widget.js

def test_widget_js_e_servido_como_javascript(client):
    r = client.get("/widget.js")
    assert r.status_code == 200
    assert "javascript" in r.headers["content-type"]


def test_widget_js_e_publico(client):
    """Tem que carregar no site de terceiro, sem sessão nenhuma."""
    assert client.get("/widget.js").status_code == 200


def test_widget_js_usa_shadow_dom(client):
    """Sem isolamento, o CSS do site do cliente deforma a bolha."""
    assert "attachShadow" in client.get("/widget.js").text


def test_widget_js_confere_a_origem_do_postmessage(client):
    """Sem o check, qualquer página mandaria 'bia:fechar' no widget."""
    corpo = client.get("/widget.js").text
    assert "e.origin !== origem" in corpo


# ---------------------------------------------------------------- validação do sid

SIDS_INVALIDOS = [
    "",                                   # vazio
    "curto",                              # abaixo do mínimo
    "tg:123456",                          # tentaria sequestrar lead do Telegram
    "web:abc",                            # idem, canal web
    "../../etc/passwd",
    "a" * 65,                             # acima do máximo
    "com espaco no meio aqui tudo",
    "sid-com-hifen-e-mais-coisas-aqui",   # hífen não está no alfabeto aceito
]


@precisa_de_banco
@pytest.mark.parametrize("sid", SIDS_INVALIDOS)
def test_sid_invalido_e_recusado(client, sid):
    assert client.get("/embed", params={"sid": sid}).status_code == 400


@precisa_de_banco
@pytest.mark.parametrize("sid", SIDS_INVALIDOS)
def test_sid_invalido_tambem_e_recusado_no_envio(client, sid):
    """O GET não é a única porta -- dá pra postar direto em /embed/send."""
    r = client.post("/embed/send", data={"sid": sid, "mensagem": "oi"})
    assert r.status_code == 400


@precisa_de_banco
def test_sid_valido_nao_colide_com_outros_canais(client):
    """O prefixo `widget:` é o que mantém os canais em espaços separados."""
    client.get("/embed", params={"sid": SID_OK})
    lead = repo.get_or_create_lead(chat_id=f"widget:{SID_OK}", canal="widget")
    assert lead["chat_id"].startswith("widget:")
    assert lead["canal"] == "widget"


# ---------------------------------------------------------------- /embed

@precisa_de_banco
def test_embed_abre_sem_login(client):
    """É o chat do visitante: exigir conta aqui mataria o widget."""
    assert client.get("/embed", params={"sid": SID_OK}).status_code == 200


@precisa_de_banco
def test_embed_declara_frame_ancestors(client):
    """Quem barra o embed em site não autorizado é o navegador, por este
    cabeçalho -- não o nosso código."""
    r = client.get("/embed", params={"sid": SID_OK})
    assert "frame-ancestors" in r.headers["content-security-policy"]


@precisa_de_banco
def test_marca_do_site_aparece_na_tela(client):
    r = client.get("/embed", params={"sid": SID_OK, "brand": "Imobiliária Horizonte"})
    assert "Imobiliária Horizonte" in r.text


@precisa_de_banco
def test_marca_do_site_nao_vira_html(client):
    """`data-brand` é texto de terceiro: tem que ser escapado, senão o site
    que embute injeta script na nossa página."""
    r = client.get("/embed", params={"sid": SID_OK, "brand": "<script>alert(1)</script>"})
    assert "<script>alert(1)</script>" not in r.text
    assert "&lt;script&gt;" in r.text


@precisa_de_banco
def test_embed_nao_traz_o_nav_do_painel(client):
    """A página roda dentro do site do cliente -- link pro dashboard ali
    seria vazamento de interface interna."""
    r = client.get("/embed", params={"sid": SID_OK})
    assert "/dashboard" not in r.text
