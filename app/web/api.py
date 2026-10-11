"""Dashboard + CRM + chat web do lead -- Etapas 7 e 7.5 do PLANO.md.

Um processo só, junto com o resto (seção 4: "Processo único FastAPI").
Jinja2 + HTMX + Tailwind via CDN -- zero build, zero node_modules (decisão
da seção 4/Estrutura de pastas: cortar Next.js/Vite pra entrega rápida).

O chat web reusa `processar_turno` -- mesmo orquestrador do Telegram e do
CLI, só troca o canal (seção 5.8: "canal é adapter"). Rotas `def` (não
`async def`) de propósito: FastAPI roda handlers síncronos numa
threadpool automaticamente, o que já resolve o mesmo problema que
`asyncio.to_thread` resolve manualmente em channels/telegram.py --
`processar_turno` é bloqueante (psycopg + httpx, ~9-10s por turno).
"""

import csv
import io
import logging
import re
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import Depends, FastAPI, Form, HTTPException, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates

from app.core import auth, custo, injecao, scoring
from app.core.config import get_settings
from app.core.turno import processar_turno
from app.db import repo

logging.basicConfig(level=logging.INFO)
# httpx loga a URL inteira da requisicao em INFO -- o bot do Telegram agora
# roda DENTRO deste processo (lifespan abaixo), entao a mesma supressao que
# existia so no script standalone (channels/telegram.py) precisa valer
# aqui tambem, senao o token do bot vaza em texto puro no log (secao 7).
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Sobe o bot do Telegram (long polling + follow-up scheduler) dentro
    do MESMO processo do FastAPI -- e o que o diagrama da seção 3 do
    PLANO.md sempre disse ("Processo único FastAPI"), só que até agora o
    bot rodava como script separado (`python -m app.channels.telegram`).
    API de baixo nível do ptb (`initialize/start/updater.start_polling`),
    não `run_polling()` -- essa bloqueia com o próprio loop de eventos,
    incompatível com o lifespan do FastAPI, que já tem um loop rodando."""
    settings = get_settings()
    telegram_app = None
    if settings.telegram_bot_token:
        # Import condicional: so carrega o adapter se o canal estiver ligado.
        from app.channels import telegram as canal_tg  # noqa: PLC0415

        telegram_app = canal_tg.build_app()
        await telegram_app.initialize()
        await telegram_app.start()
        await telegram_app.updater.start_polling(drop_pending_updates=True)
        logger.info(
            "Telegram: long polling iniciado (mesmo processo do FastAPI). Follow-up a cada %ss.",
            settings.followup_scan_s,
        )
    else:
        logger.warning("TELEGRAM_BOT_TOKEN vazio -- canal Telegram desligado nesta execução.")

    yield

    if telegram_app:
        await telegram_app.updater.stop()
        await telegram_app.stop()
        await telegram_app.shutdown()


app = FastAPI(title="Agente SDR Imobiliário", lifespan=lifespan)
templates = Jinja2Templates(directory="app/web/templates")

SESSION_COOKIE = "web_session"
# 180 dias: o cookie e a UNICA coisa que liga o navegador ao lead no canal
# web (chat_id = "web:<uuid>"). Com os 7 dias de antes, voltar duas semanas
# depois criava um lead novo e zerado -- o cliente via um agente que tinha
# esquecido nome, telefone e a visita marcada, mesmo com tudo gravado no
# banco. Vida do cookie >= vida util do atendimento.
SESSION_MAX_AGE_S = 60 * 60 * 24 * 180

AUTH_COOKIE = "sdr_auth"


# ---------------------------------------------------------------- login do dashboard
# O dashboard mostra telefone, score e a conversa inteira de cada lead.
# Ate aqui qualquer um em :8000/dashboard lia tudo. Agora:
#
#   - sem nenhum usuario no banco -> `/registro` abre e o primeiro cadastro
#     vira admin (bootstrap); a rota fecha sozinha depois disso;
#   - dai em diante, so entra quem o admin convidar por link de uso unico;
#   - chat do lead, /health e as telas de login seguem publicos -- quem e
#     atendido nao tem conta.


def _usuario_logado(request: Request) -> dict | None:
    token = request.cookies.get(AUTH_COOKIE)
    if not token:
        return None
    return repo.get_sessao(auth.hash_token(token))


def exigir_login(request: Request) -> dict:
    """Guard das rotas do painel. Em requisicao normal manda pro /login;
    em requisicao do HTMX responde 401 com `HX-Redirect`, senao o fragmento
    da tela de login seria injetado DENTRO do dashboard expirado em vez de
    trocar a pagina."""
    usuario = _usuario_logado(request)
    if usuario:
        return usuario
    if request.headers.get("HX-Request") == "true":
        raise HTTPException(status_code=401, headers={"HX-Redirect": "/login"})
    raise HTTPException(status_code=303, headers={"Location": "/login"})


def exigir_admin(usuario: dict = Depends(exigir_login)) -> dict:
    if usuario["papel"] != "admin":
        raise HTTPException(status_code=403, detail="Só administradores.")
    return usuario


def _definir_cookie_sessao(response: Response, token: str) -> None:
    settings = get_settings()
    response.set_cookie(
        AUTH_COOKIE, token,
        max_age=settings.sessao_horas * 3600,
        httponly=True,       # nenhum JS do painel le este cookie
        samesite="lax",      # corta CSRF de outro site sem quebrar link externo
        secure=settings.cookie_secure,
        path="/",
    )


@app.get("/login", response_class=HTMLResponse)
def login_form(request: Request):
    # Banco vazio: nao ha a quem pedir senha ainda -- manda montar o admin.
    if repo.contar_usuarios() == 0:
        return RedirectResponse("/registro", status_code=303)
    if _usuario_logado(request):
        return RedirectResponse("/dashboard", status_code=303)
    return templates.TemplateResponse("login.html", {"request": request})


@app.post("/login", response_class=HTMLResponse)
def login(request: Request, email: str = Form(...), senha: str = Form(...)):
    usuario = repo.get_usuario_por_email(email.strip().lower())

    # Mensagem unica pros dois casos (e-mail inexistente / senha errada) de
    # proposito: distinguir entrega uma lista de e-mails validos a quem
    # testa de fora.
    erro = {"request": request, "erro": "E-mail ou senha incorretos."}
    if not usuario or not usuario["ativo"]:
        return templates.TemplateResponse("login.html", erro, status_code=401)

    confere, hash_novo = auth.verificar_senha(usuario["senha_hash"], senha)
    if not confere:
        return templates.TemplateResponse("login.html", erro, status_code=401)
    if hash_novo:
        repo.atualizar_senha(usuario["id"], hash_novo)

    repo.limpar_sessoes_expiradas()
    token = auth.gerar_token()
    settings = get_settings()
    repo.criar_sessao(
        token_hash=auth.hash_token(token),
        usuario_id=usuario["id"],
        expira_em=datetime.now(timezone.utc) + timedelta(hours=settings.sessao_horas),
    )
    resposta = RedirectResponse("/dashboard", status_code=303)
    _definir_cookie_sessao(resposta, token)
    return resposta


@app.post("/logout")
def logout(request: Request):
    token = request.cookies.get(AUTH_COOKIE)
    if token:
        repo.apagar_sessao(auth.hash_token(token))
    resposta = RedirectResponse("/login", status_code=303)
    resposta.delete_cookie(AUTH_COOKIE, path="/")
    return resposta


def _contexto_registro(token: str | None) -> dict | None:
    """Quem pode cadastrar agora, e com que papel. None = ninguem."""
    if repo.contar_usuarios() == 0:
        return {"bootstrap": True, "email": "", "papel": "admin", "token": ""}
    if token:
        convite = repo.get_convite_valido(auth.hash_token(token))
        if convite:
            return {
                "bootstrap": False, "email": convite["email"],
                "papel": convite["papel"], "token": token,
            }
    return None


@app.get("/registro", response_class=HTMLResponse)
def registro_form(request: Request, token: str = ""):
    contexto = _contexto_registro(token or None)
    if not contexto:
        return templates.TemplateResponse(
            "registro.html",
            {"request": request, "bloqueado": True,
             "erro": "Cadastro só por convite. Peça um link a um administrador."},
            status_code=403,
        )
    return templates.TemplateResponse("registro.html", {"request": request, **contexto})


@app.post("/registro", response_class=HTMLResponse)
def registro(
    request: Request,
    nome: str = Form(...),
    email: str = Form(...),
    senha: str = Form(...),
    token: str = Form(""),
):
    contexto = _contexto_registro(token or None)
    if not contexto:
        return templates.TemplateResponse(
            "registro.html",
            {"request": request, "bloqueado": True,
             "erro": "Cadastro só por convite. Peça um link a um administrador."},
            status_code=403,
        )

    # No fluxo de convite o e-mail vem do convite, nao do formulario: senao
    # bastaria editar o campo no navegador pra criar conta com outro e-mail.
    email_final = (contexto["email"] or email).strip().lower()

    def _erro(msg: str, status: int):
        return templates.TemplateResponse(
            "registro.html", {"request": request, **contexto, "erro": msg}, status_code=status,
        )

    try:
        senha_hash = auth.hash_senha(senha)
    except auth.SenhaFraca as e:
        return _erro(str(e), 400)

    # Fecha o convite ANTES de criar o usuario: dois cadastros simultaneos
    # com o mesmo link disputam este UPDATE, e so um ganha (o WHERE
    # usado_em IS NULL resolve no banco).
    if not contexto["bootstrap"] and not repo.marcar_convite_usado(auth.hash_token(token)):
        return _erro("Este convite já foi usado.", 409)

    usuario = repo.criar_usuario(
        email=email_final, nome=nome.strip(), senha_hash=senha_hash, papel=contexto["papel"],
    )
    if not usuario:
        return _erro("Já existe uma conta com este e-mail.", 409)

    token_sessao = auth.gerar_token()
    settings = get_settings()
    repo.criar_sessao(
        token_hash=auth.hash_token(token_sessao),
        usuario_id=usuario["id"],
        expira_em=datetime.now(timezone.utc) + timedelta(hours=settings.sessao_horas),
    )
    resposta = RedirectResponse("/dashboard", status_code=303)
    _definir_cookie_sessao(resposta, token_sessao)
    return resposta


# ---------------------------------------------------------------- usuarios e convites (admin)

@app.get("/dashboard/usuarios", response_class=HTMLResponse)
def usuarios_page(request: Request, usuario: dict = Depends(exigir_admin)):
    return templates.TemplateResponse(
        "usuarios.html",
        {"request": request, "usuario": usuario,
         "usuarios": repo.listar_usuarios(), "convites": repo.listar_convites_pendentes()},
    )


@app.post("/dashboard/convites", response_class=HTMLResponse)
def criar_convite(
    request: Request,
    email: str = Form(...),
    papel: str = Form("corretor"),
    usuario: dict = Depends(exigir_admin),
):
    """Gera o link e MOSTRA na tela -- nao ha SMTP nesta POC, o admin copia
    e entrega por fora. O token so existe aqui: o banco guarda o hash."""
    if papel not in ("admin", "corretor"):
        papel = "corretor"
    settings = get_settings()
    token = auth.gerar_token()
    repo.criar_convite(
        token_hash=auth.hash_token(token),
        email=email.strip().lower(),
        papel=papel,
        criado_por=usuario["id"],
        expira_em=datetime.now(timezone.utc) + timedelta(hours=settings.convite_horas),
    )
    return templates.TemplateResponse(
        "usuarios.html",
        {"request": request, "usuario": usuario,
         "usuarios": repo.listar_usuarios(), "convites": repo.listar_convites_pendentes(),
         "link_convite": f"{request.base_url}registro?token={token}",
         "horas_convite": settings.convite_horas},
    )


@app.post("/dashboard/usuarios/{usuario_id}/ativo", response_class=HTMLResponse)
def alternar_usuario_ativo(
    request: Request,
    usuario_id: int,
    ativo: str = Form(...),
    usuario: dict = Depends(exigir_admin),
):
    # Desativar a si mesmo tranca o admin pra fora do proprio painel.
    if usuario_id == usuario["id"]:
        raise HTTPException(status_code=400, detail="Você não pode desativar a própria conta.")
    repo.definir_usuario_ativo(usuario_id, ativo == "true")
    return templates.TemplateResponse(
        "usuarios.html",
        {"request": request, "usuario": usuario,
         "usuarios": repo.listar_usuarios(), "convites": repo.listar_convites_pendentes()},
    )


@app.get("/health")
def health():
    return {"status": "ok"}


# ---------------------------------------------------------------- dashboard (Etapa 7)

@app.get("/", response_class=HTMLResponse)
def raiz(request: Request, usuario: dict = Depends(exigir_login)):
    return templates.TemplateResponse("dashboard.html", {"request": request, "usuario": usuario})


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request, usuario: dict = Depends(exigir_login)):
    return templates.TemplateResponse("dashboard.html", {"request": request, "usuario": usuario})


@app.get("/dashboard/leads-table", response_class=HTMLResponse)
def leads_table(request: Request, usuario: dict = Depends(exigir_login)):
    leads = repo.listar_leads()
    # Prioridade pela mesma funcao do detalhe (core/scoring.py). Calcular
    # por faixa aqui e pela regra completa la faria a lista dizer "quente"
    # e o detalhe "morno" no mesmo lead.
    for lead in leads:
        lead["prioridade"] = scoring.prioridade_de(lead["score"], lead)
    return templates.TemplateResponse("_leads_table.html", {"request": request, "leads": leads})


@app.get("/dashboard/lead/{lead_id}", response_class=HTMLResponse)
def lead_detail(request: Request, lead_id: int, usuario: dict = Depends(exigir_login)):
    lead = repo.get_lead(lead_id)
    mensagens = repo.listar_mensagens(lead_id)
    slots = repo.get_slots(lead_id)
    resumo = repo.get_resumo(lead_id)
    # Recalculado na leitura, não lido de `leads.score`: a coluna guarda só
    # o total, e a quebra tem que bater com ele. Recalcular da mesma função
    # é o que impede o painel de mostrar uma conta que não fecha.
    detalhe = scoring.calcular_score_detalhado(
        slots, visita_agendada=lead["status"] == "visita_agendada"
    )
    return templates.TemplateResponse(
        "_lead_detail.html",
        {"request": request, "lead": lead, "mensagens": mensagens, "slots": slots,
         "resumo": resumo, "score": detalhe},
    )


@app.get("/dashboard/traces", response_class=HTMLResponse)
def traces(request: Request, usuario: dict = Depends(exigir_login)):
    settings = get_settings()
    janela = settings.traces_janela_horas

    resumo = repo.resumo_traces(janela)
    leads = repo.contar_leads_atendidos(janela)
    tokens_entrada = resumo["tokens_entrada"] or 0
    tokens_saida = resumo["tokens_saida"] or 0

    return templates.TemplateResponse(
        "_traces.html",
        {
            "request": request,
            "traces": repo.listar_traces(),
            "resumo": resumo,
            "por_agente": repo.resumo_traces_por_agente(janela),
            "janela_horas": janela,
            "leads_atendidos": leads,
            # Custo REAL e zero (modelo local). Estes dois sao o
            # contrafactual: o que os mesmos tokens custariam numa API
            # paga, a preco de referencia do .env -- ver core/custo.py.
            "custo": custo.calcular(tokens_entrada, tokens_saida),
            "custo_por_conversa": custo.por_conversa(tokens_entrada, tokens_saida, leads),
            "preco_entrada": settings.preco_ref_usd_por_mi_entrada,
            "preco_saida": settings.preco_ref_usd_por_mi_saida,
            # Tentativas de prompt injection -- marcadas, nunca bloqueadas
            # (core/injecao.py explica por quê).
            "injecoes": repo.resumo_injecoes(janela),
            "injecoes_recentes": repo.listar_mensagens_suspeitas(),
            "rotulos_injecao": injecao.ROTULOS,
        },
    )


# ---------------------------------------------------------------- CRM (diferencial)

@app.post("/crm/leads")
async def crm_receber_lead(request: Request, usuario: dict = Depends(exigir_login)):
    """Entrada simulada de CRM externo -- cria/reconhece um lead vindo de
    fora do Telegram, mesma tabela `leads`, mesmo pipeline a partir daqui."""
    payload = await request.json()
    chat_id = payload.get("chat_id") or f"crm:{payload.get('telefone', uuid.uuid4().hex[:8])}"
    lead = repo.get_or_create_lead(chat_id=chat_id, canal="crm")
    return {"lead_id": lead["id"], "status": lead["status"]}


@app.get("/crm/leads.csv")
def crm_exportar_csv(usuario: dict = Depends(exigir_login)):
    leads = repo.listar_leads()
    buffer = io.StringIO()
    campos = [
        "id", "chat_id", "canal", "nome", "status", "score",
        "intencao", "regiao", "quartos", "faixa_preco_max", "urgencia",
        "criado_em", "ultima_interacao_em",
    ]
    writer = csv.DictWriter(buffer, fieldnames=campos, extrasaction="ignore")
    writer.writeheader()
    for lead in leads:
        writer.writerow(lead)
    buffer.seek(0)
    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=leads.csv"},
    )


# ---------------------------------------------------------------- chat web do lead (Etapa 7.5, diferencial)

def _session_id(request: Request, response: Response) -> str:
    sid = request.cookies.get(SESSION_COOKIE)
    if not sid:
        sid = uuid.uuid4().hex
        response.set_cookie(
            SESSION_COOKIE, sid, max_age=SESSION_MAX_AGE_S,
            httponly=True, samesite="lax", secure=get_settings().cookie_secure,
        )
    return sid


@app.get("/chat", response_class=HTMLResponse)
def chat_page(request: Request, response: Response):
    sid = _session_id(request, response)
    lead_chat_id = f"web:{sid}"
    lead = repo.get_or_create_lead(chat_id=lead_chat_id, canal="web")
    mensagens = repo.listar_mensagens(lead["id"])
    pagina = templates.TemplateResponse("chat.html", {"request": request, "mensagens": mensagens})
    pagina.set_cookie(
        SESSION_COOKIE, sid, max_age=SESSION_MAX_AGE_S,
        httponly=True, samesite="lax", secure=get_settings().cookie_secure,
    )
    return pagina


# ---------------------------------------------------------------- widget embutivel (diferencial)
# Uma linha no site de qualquer imobiliaria:
#
#   <script src="https://SEU-APP/widget.js" data-brand="Imobiliaria X" defer></script>
#
# A bolha abre um iframe em /embed. O lead que chega por ai entra com
# canal "widget", entao o dashboard separa quem veio do site de quem veio
# do Telegram ou do chat proprio.

# Formato do id de sessao gerado pelo widget.js (`crypto.randomUUID` sem
# hifens, ou o fallback). Validar e obrigatorio: o sid entra no `chat_id`
# do lead, e aceitar texto livre deixaria o visitante escolher o chat_id
# de outro -- inclusive colidir com "tg:<algo>" e sequestrar a conversa de
# um lead do Telegram.
_SID_VALIDO = re.compile(r"^[a-zA-Z0-9]{16,64}$")


def _chat_id_widget(sid: str) -> str:
    if not _SID_VALIDO.match(sid or ""):
        raise HTTPException(status_code=400, detail="sid inválido")
    return f"widget:{sid}"


def _blindar_iframe(resposta: Response) -> Response:
    """`frame-ancestors` decide QUEM pode embutir esta página. Fica no CSP
    e não em `X-Frame-Options` porque aquele só aceita um domínio; este
    aceita lista. Quem barra é o navegador, não o nosso código."""
    resposta.headers["Content-Security-Policy"] = (
        f"frame-ancestors {get_settings().widget_frame_ancestors}"
    )
    return resposta


@app.get("/widget.js")
def widget_js():
    """Servido por rota própria, não por `StaticFiles` montado: é um
    arquivo só, e assim dá pra controlar o cache e o content-type sem
    expor um diretório inteiro."""
    caminho = Path(__file__).parent / "static" / "widget.js"
    return FileResponse(
        caminho,
        media_type="application/javascript",
        headers={"Cache-Control": "public, max-age=300"},
    )


@app.get("/embed", response_class=HTMLResponse)
def embed_page(request: Request, sid: str = "", brand: str = ""):
    lead = repo.get_or_create_lead(chat_id=_chat_id_widget(sid), canal="widget")
    pagina = templates.TemplateResponse(
        "embed.html",
        {
            "request": request,
            "mensagens": repo.listar_mensagens(lead["id"]),
            "sid": sid,
            # Jinja escapa -- `data-brand` é texto de terceiro e não pode
            # virar HTML dentro da nossa página.
            "marca": brand,
        },
    )
    return _blindar_iframe(pagina)


@app.post("/embed/send", response_class=HTMLResponse)
def embed_send(request: Request, sid: str = Form(...), mensagem: str = Form(...)):
    chat_id = _chat_id_widget(sid)
    try:
        resposta = processar_turno(chat_id=chat_id, canal="widget", mensagem=mensagem, origem="texto")
    except Exception:
        logger.exception("falha processando turno do widget (chat_id=%s)", chat_id)
        resposta = "Desculpa, tive um probleminha aqui agora. Pode repetir o que você disse?"

    return _blindar_iframe(
        templates.TemplateResponse(
            "_chat_mensagens.html",
            {
                "request": request,
                "mensagem_usuario": mensagem,
                "resposta": resposta,
                "galeria": getattr(resposta, "galeria", []),
            },
        )
    )


@app.post("/chat/send", response_class=HTMLResponse)
def chat_send(request: Request, mensagem: str = Form(...)):
    sid = request.cookies.get(SESSION_COOKIE)
    if not sid:
        sid = uuid.uuid4().hex
    lead_chat_id = f"web:{sid}"

    try:
        resposta = processar_turno(chat_id=lead_chat_id, canal="web", mensagem=mensagem, origem="texto")
    except Exception:
        logger.exception("falha processando turno do chat web (chat_id=%s)", lead_chat_id)
        resposta = "Desculpa, tive um probleminha aqui agora. Pode repetir o que você disse?"

    r = templates.TemplateResponse(
        "_chat_mensagens.html",
        {
            "request": request,
            "mensagem_usuario": mensagem,
            "resposta": resposta,
            # fotos dos imoveis apresentados neste turno (core/turno.py::
            # RespostaTurno) -- vazio em todo turno que nao apresenta imovel,
            # e ausente no fallback de excecao acima, que e string comum.
            "galeria": getattr(resposta, "galeria", []),
        },
    )
    r.set_cookie(
        SESSION_COOKIE, sid, max_age=SESSION_MAX_AGE_S,
        httponly=True, samesite="lax", secure=get_settings().cookie_secure,
    )
    return r
