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
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Form, Request, Response
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates

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


@app.get("/health")
def health():
    return {"status": "ok"}


# ---------------------------------------------------------------- dashboard (Etapa 7)

@app.get("/", response_class=HTMLResponse)
def raiz(request: Request):
    return templates.TemplateResponse("dashboard.html", {"request": request})


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request):
    return templates.TemplateResponse("dashboard.html", {"request": request})


@app.get("/dashboard/leads-table", response_class=HTMLResponse)
def leads_table(request: Request):
    leads = repo.listar_leads()
    return templates.TemplateResponse("_leads_table.html", {"request": request, "leads": leads})


@app.get("/dashboard/lead/{lead_id}", response_class=HTMLResponse)
def lead_detail(request: Request, lead_id: int):
    lead = repo.get_lead(lead_id)
    mensagens = repo.listar_mensagens(lead_id)
    slots = repo.get_slots(lead_id)
    resumo = repo.get_resumo(lead_id)
    return templates.TemplateResponse(
        "_lead_detail.html",
        {"request": request, "lead": lead, "mensagens": mensagens, "slots": slots, "resumo": resumo},
    )


@app.get("/dashboard/traces", response_class=HTMLResponse)
def traces(request: Request):
    linhas = repo.listar_traces()
    return templates.TemplateResponse("_traces.html", {"request": request, "traces": linhas})


# ---------------------------------------------------------------- CRM (diferencial)

@app.post("/crm/leads")
async def crm_receber_lead(request: Request):
    """Entrada simulada de CRM externo -- cria/reconhece um lead vindo de
    fora do Telegram, mesma tabela `leads`, mesmo pipeline a partir daqui."""
    payload = await request.json()
    chat_id = payload.get("chat_id") or f"crm:{payload.get('telefone', uuid.uuid4().hex[:8])}"
    lead = repo.get_or_create_lead(chat_id=chat_id, canal="crm")
    return {"lead_id": lead["id"], "status": lead["status"]}


@app.get("/crm/leads.csv")
def crm_exportar_csv():
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
        response.set_cookie(SESSION_COOKIE, sid, max_age=SESSION_MAX_AGE_S)
    return sid


@app.get("/chat", response_class=HTMLResponse)
def chat_page(request: Request, response: Response):
    sid = _session_id(request, response)
    lead_chat_id = f"web:{sid}"
    lead = repo.get_or_create_lead(chat_id=lead_chat_id, canal="web")
    mensagens = repo.listar_mensagens(lead["id"])
    pagina = templates.TemplateResponse("chat.html", {"request": request, "mensagens": mensagens})
    pagina.set_cookie(SESSION_COOKIE, sid, max_age=SESSION_MAX_AGE_S)
    return pagina


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
    r.set_cookie(SESSION_COOKIE, sid, max_age=SESSION_MAX_AGE_S)
    return r
