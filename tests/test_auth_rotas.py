"""Fluxo de acesso ao painel: bootstrap, convite e guard das rotas.

Precisa do Postgres (usuarios/sessoes/convites). `pytest.importorskip` nao
serve aqui -- o modulo importa, o que falta e o BANCO -- entao o skip e
explicito, com a razao na mensagem. Sem isso, rodar `pytest` com o Docker
parado devolve erro de conexao e parece suite quebrada.
"""

import uuid

import pytest
from fastapi.testclient import TestClient

from app.db import repo
from tests.conftest import precisa_de_banco

pytestmark = [precisa_de_banco]


@pytest.fixture
def client():
    """`raise_server_exceptions=False` porque o guard sinaliza redirect e
    403 via HTTPException: sem isso o TestClient relanca no teste em vez de
    devolver a resposta que o navegador veria.

    SEM o `with`, de proposito -- o context manager do TestClient roda o
    lifespan, e o lifespan sobe o bot do Telegram. Com `TELEGRAM_BOT_TOKEN`
    preenchido no .env isso custava 3,75s de teardown POR TESTE e, pior,
    abria long polling contra o bot de verdade com `drop_pending_updates`:
    rodar a suite comia as mensagens reais do bot. Nenhuma rota aqui
    depende do lifespan."""
    from app.web.api import app

    return TestClient(app, follow_redirects=False, raise_server_exceptions=False)


@pytest.fixture
def admin(client):
    """Um admin ativo + o client ja autenticado como ele.

    Se o banco ja tem usuario (execucao anterior, uso real), o bootstrap
    por `/registro` nao esta mais disponivel -- entao a conta entra direto
    pelo repo e a sessao pelo login. Testa o mesmo guard dos dois jeitos.
    """
    from app.core import auth

    email = f"admin_{uuid.uuid4().hex[:8]}@teste.local"
    senha = "senha-de-teste-123"
    usuario = repo.criar_usuario(
        email=email, nome="Admin de Teste", senha_hash=auth.hash_senha(senha), papel="admin",
    )
    client.post("/login", data={"email": email, "senha": senha})
    yield {"id": usuario["id"], "email": email, "senha": senha}

    # Limpa o que criou. Sem isto cada rodada de `pytest` deixava ~36
    # contas `@teste.local` no banco -- e este é o MESMO banco da demo,
    # então a tela de usuários do painel ia enchendo de lixo a cada
    # execução da suíte. As sessões somem junto, por `ON DELETE CASCADE`.
    with repo.get_conn() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM usuarios WHERE email LIKE %s", ("%@teste.local",))
        cur.execute("DELETE FROM convites WHERE email LIKE %s", ("%@teste.local",))
        conn.commit()


# ---------------------------------------------------------------- guard

ROTAS_DO_PAINEL = [
    "/", "/dashboard", "/dashboard/leads-table", "/dashboard/lead/1",
    "/dashboard/traces", "/dashboard/usuarios", "/crm/leads.csv",
]


@pytest.mark.parametrize("rota", ROTAS_DO_PAINEL)
def test_rota_do_painel_sem_login_nao_entrega_dado(client, rota):
    """O que nao pode acontecer e 200 com dado de lead no corpo."""
    r = client.get(rota)
    assert r.status_code != 200


def test_sem_login_redireciona_pro_login(client):
    r = client.get("/dashboard")
    assert r.status_code == 303
    assert r.headers["location"] == "/login"


def test_requisicao_htmx_expirada_pede_troca_de_pagina(client):
    """Fragmento do HTMX nao pode receber redirect: o HTML do login seria
    injetado dentro do dashboard. O contrato e 401 + HX-Redirect."""
    r = client.get("/dashboard/leads-table", headers={"HX-Request": "true"})
    assert r.status_code == 401
    assert r.headers["hx-redirect"] == "/login"


def test_chat_do_lead_continua_publico(client):
    assert client.get("/chat").status_code == 200


def test_health_continua_publico(client):
    assert client.get("/health").status_code == 200


# ---------------------------------------------------------------- login

def test_login_valido_abre_o_dashboard(client, admin):
    assert client.get("/dashboard").status_code == 200


def test_senha_errada_nao_cria_sessao(client, admin):
    client.post("/logout")
    r = client.post("/login", data={"email": admin["email"], "senha": "errada-mas-longa"})
    assert r.status_code == 401
    assert client.get("/dashboard").status_code == 303


def test_email_inexistente_responde_igual_a_senha_errada(client, admin):
    """Resposta diferente pros dois casos entrega quais e-mails existem."""
    r_inexistente = client.post(
        "/login", data={"email": "ninguem@teste.local", "senha": "qualquer-coisa-123"}
    )
    r_senha_errada = client.post(
        "/login", data={"email": admin["email"], "senha": "errada-mas-longa"}
    )
    assert r_inexistente.status_code == r_senha_errada.status_code == 401
    assert r_inexistente.text == r_senha_errada.text


def test_logout_derruba_a_sessao(client, admin):
    client.post("/logout")
    assert client.get("/dashboard").status_code == 303


def test_cookie_de_sessao_e_httponly(client, admin):
    client.post("/logout")
    r = client.post("/login", data={"email": admin["email"], "senha": admin["senha"]})
    assert "httponly" in r.headers["set-cookie"].lower()


def test_usuario_desativado_perde_o_acesso_na_hora(client, admin):
    """A razao de a sessao ser server-side: com JWT assinado, o token
    continuaria valido ate expirar."""
    assert client.get("/dashboard").status_code == 200
    repo.definir_usuario_ativo(admin["id"], False)
    assert client.get("/dashboard").status_code == 303


# ---------------------------------------------------------------- convite

def test_registro_sem_convite_e_recusado(client, admin):
    """Com usuario no banco, o bootstrap ja fechou."""
    assert client.get("/registro").status_code == 403


def test_registro_com_token_invalido_e_recusado(client, admin):
    assert client.get("/registro?token=token-que-nunca-existiu").status_code == 403


def test_convite_cria_conta_e_so_funciona_uma_vez(client, admin):
    email_convidado = f"corretor_{uuid.uuid4().hex[:8]}@teste.local"
    r = client.post("/dashboard/convites", data={"email": email_convidado, "papel": "corretor"})
    assert r.status_code == 200
    token = r.text.split("registro?token=")[1].split('"')[0]

    assert client.get(f"/registro?token={token}").status_code == 200

    criou = client.post(
        "/registro",
        data={"nome": "Corretor", "email": email_convidado,
              "senha": "senha-de-teste-123", "token": token},
    )
    assert criou.status_code == 303

    # Mesmo link de novo: o convite ja foi queimado.
    assert client.get(f"/registro?token={token}").status_code == 403


def test_convite_ignora_email_trocado_no_formulario(client, admin):
    """O campo vai `readonly` na tela, mas isso e so o navegador -- quem
    manda o POST na mao poderia criar conta com outro e-mail."""
    email_convidado = f"corretor_{uuid.uuid4().hex[:8]}@teste.local"
    r = client.post("/dashboard/convites", data={"email": email_convidado, "papel": "corretor"})
    token = r.text.split("registro?token=")[1].split('"')[0]

    client.post(
        "/registro",
        data={"nome": "Espertinho", "email": "outro@teste.local",
              "senha": "senha-de-teste-123", "token": token},
    )
    assert repo.get_usuario_por_email(email_convidado) is not None
    assert repo.get_usuario_por_email("outro@teste.local") is None


def test_convite_nao_promove_a_admin_pelo_formulario(client, admin):
    """O papel vem do convite, nao do POST."""
    email_convidado = f"corretor_{uuid.uuid4().hex[:8]}@teste.local"
    r = client.post("/dashboard/convites", data={"email": email_convidado, "papel": "corretor"})
    token = r.text.split("registro?token=")[1].split('"')[0]

    client.post(
        "/registro",
        data={"nome": "Corretor", "email": email_convidado, "papel": "admin",
              "senha": "senha-de-teste-123", "token": token},
    )
    assert repo.get_usuario_por_email(email_convidado)["papel"] == "corretor"


def test_senha_fraca_nao_cria_conta(client, admin):
    email_convidado = f"corretor_{uuid.uuid4().hex[:8]}@teste.local"
    r = client.post("/dashboard/convites", data={"email": email_convidado, "papel": "corretor"})
    token = r.text.split("registro?token=")[1].split('"')[0]

    recusado = client.post(
        "/registro",
        data={"nome": "Corretor", "email": email_convidado, "senha": "curta", "token": token},
    )
    assert recusado.status_code == 400
    assert repo.get_usuario_por_email(email_convidado) is None


# ---------------------------------------------------------------- papeis

def test_corretor_nao_abre_a_tela_de_usuarios(client, admin):
    from app.core import auth

    email = f"corretor_{uuid.uuid4().hex[:8]}@teste.local"
    senha = "senha-de-teste-123"
    repo.criar_usuario(
        email=email, nome="Corretor", senha_hash=auth.hash_senha(senha), papel="corretor",
    )
    client.post("/logout")
    client.post("/login", data={"email": email, "senha": senha})

    assert client.get("/dashboard").status_code == 200       # painel, sim
    assert client.get("/dashboard/usuarios").status_code == 403  # administracao, nao


def test_admin_nao_consegue_desativar_a_propria_conta(client, admin):
    r = client.post(f"/dashboard/usuarios/{admin['id']}/ativo", data={"ativo": "false"})
    assert r.status_code == 400
    assert client.get("/dashboard").status_code == 200
