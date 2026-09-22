# Agente SDR Imobiliário

POC de um agente de IA que atende, qualifica e agenda visitas com leads de uma imobiliária, 100% local — nenhum dado sai da máquina, nenhuma chamada a API paga. Projeto do **FIAP Tech Challenge Fase 5**.

Atende por Telegram (texto e áudio), por chat web, ou por CLI — os três canais batem no mesmo orquestrador. Qualifica intenção (compra/aluguel/investimento), busca imóveis reais no banco, agenda visita ou encaminha a um especialista, faz follow-up automático quando o lead some, gera resumo para o corretor e mantém um dashboard com tudo em tempo real.

Documentos relacionados: [ARQUITETURA.md](ARQUITETURA.md) (design técnico) · [IA.md](IA.md) (como a IA foi usada, achados reais) · [PITCH.md](PITCH.md) (pitch de 5 minutos) · [PLANO.md](PLANO.md) (diário de bordo completo, com os ~15 bugs reais achados e corrigidos) · [docs/ENUNCIADO.md](docs/ENUNCIADO.md) (enunciado literal do desafio).

## Como rodar

Pré-requisitos: macOS com Apple Silicon (Metal é usado pelo Ollama e pelo Whisper), Docker, Python 3.12, [`uv`](https://github.com/astral-sh/uv), [Ollama](https://ollama.com) instalado nativamente (**nunca em container** — sem Metal, a inferência cai pra velocidade de CPU).

```bash
# 1. modelos do Ollama (se ainda não estiverem em disco)
ollama pull qwen3.5:9b
ollama pull nomic-embed-text
ollama serve &

# 2. banco (Postgres 16 + pgvector)
docker compose up -d

# 3. dependências
uv venv --python 3.12
source .venv/bin/activate
uv pip install -e .

# 4. configuração
cp .env.example .env
# editar TELEGRAM_BOT_TOKEN (token do BotFather; vazio = Telegram desligado,
# web chat e CLI seguem funcionando)

# 5. schema + seed de imóveis
psql "$DATABASE_URL" -f app/db/schema.sql   # ou deixa o docker-entrypoint aplicar sozinho
python -m app.db.seed

# 6. subir tudo (Telegram + dashboard + chat web + CRM + follow-up, um processo só)
uvicorn app.web.api:app --host 0.0.0.0 --port 8000
```

Dashboard: `http://localhost:8000/dashboard`. Chat web do lead: `http://localhost:8000/chat`. CLI alternativa (sem Telegram): `python -m app.channels.cli`.

### Transcrição de áudio (opcional)

```bash
uv pip install -e ".[stt]"
```

No `.env`: `STT_ENABLED=true`. Se o download automático do modelo via HuggingFace Hub travar (aconteceu em teste real, ver [IA.md](IA.md)), rode `scripts/baixar_modelo_stt.sh` e aponte `STT_MODEL_PATH` para o diretório que ele baixa.

### Testes

```bash
uv pip install -e ".[dev]"
pytest
```

## Como usar

**Telegram**: conversa normal, texto ou áudio. Ex.: *"Estou procurando apartamento na zona sul"* → o agente pergunta faixa de preço, quartos, região, urgência, e no fim apresenta imóveis reais do banco com opção de agendar visita. *"Quero investir em imóveis para renda"* leva pro fluxo de investimento (perfil, ticket, expectativa de retorno) e termina em encaminhamento a um especialista.

**Dashboard** (`/dashboard`): lista de leads ordenada por score, com card de detalhe (slots coletados, resumo pro corretor, histórico de mensagens) e aba de observabilidade (`agent_traces` — latência, tokens, tokens/s de cada chamada ao LLM).

**CRM** (`POST /crm/leads`, `GET /crm/leads.csv`): entrada simulada de lead vindo de fora do Telegram e exportação da base de leads.

## Arquitetura em uma frase

Um processo FastAPI único hospeda o Telegram (long polling), o chat web, o dashboard, o CRM e o scheduler de follow-up. Todos os canais chamam o mesmo orquestrador (`core/graph.py`, um `StateGraph` do LangGraph), que por sua vez chama cinco agentes lógicos — não processos, funções puras — todos batendo no mesmo modelo Ollama já residente em memória. Detalhes completos em [ARQUITETURA.md](ARQUITETURA.md).

```
app/
  agents/   router.py  qualifier.py  search.py  scheduler_agent.py  summarizer.py
  core/     graph.py  turno.py  llm.py  prompts.py  memory.py  followup.py
            scoring.py  security.py  traces.py  stt.py  config.py
  channels/ telegram.py  web (via app/web/api.py)  cli.py
  web/      api.py  templates/  (dashboard + chat + CRM, tudo HTMX)
  db/       schema.sql  repo.py  seed.py
scripts/    buscar.py  baixar_modelo_stt.sh
```

## Stack

Postgres 16 + pgvector · Ollama nativo (`qwen3.5:9b`, `nomic-embed-text`) · LangGraph (orquestração) · FastAPI + Jinja2 + HTMX (dashboard e chat, zero build step) · `python-telegram-bot` (long polling) · APScheduler (follow-up) · `mlx-whisper` (STT local via Metal, opcional).

## Escalabilidade

- **Aplicação stateless.** Todo estado mora no Postgres — N réplicas do FastAPI atrás de um balanceador não exigem mudança de código.
- **O LLM já está atrás de HTTP.** Trocar `OLLAMA_HOST` por um cluster com GPU é variável de ambiente, nenhuma linha de código muda.
- **O gargalo é medido, não estimado.** `agent_traces` grava latência e tokens/s reais de cada chamada — decisão de escalar (ou não) parte de número real.
- **O único componente single-node é o scheduler de follow-up** (APScheduler in-process). `follow_up_jobs` já é tabela; virar fila distribuída é um `SELECT ... FOR UPDATE SKIP LOCKED`, sem trocar de tecnologia.
- **Busca vetorial:** scan sequencial é a escolha certa com 200 imóveis; índice HNSW entra a partir de ~10 mil registros, sem mudar a query.

## Segurança

- Telefone do lead nunca persiste cru — `core/security.py::mascarar_telefone` guarda só DDD + 4 últimos dígitos.
- Input do lead nunca é concatenado no system prompt (mitigação de prompt injection): sempre entra como mensagem `user`.
- Token do bot do Telegram nunca aparece em log — supressão explícita do logger do `httpx` (que loga a URL completa da requisição, e a API do Telegram embute o token na URL).
- Rate limit por `chat_id` (`RATE_LIMIT_MSGS_POR_MIN`).
- O dado do lead nunca sai da máquina — modelo local, banco local. Efeito colateral da arquitetura, não um add-on de compliance.

## Limitações conhecidas desta POC

- Sem índice vetorial (`ivfflat`/HNSW) — decisão deliberada para 200 imóveis, não escala além de ~10 mil sem adicionar um.
- `follow_up_jobs` roda in-process (APScheduler), não é uma fila distribuída — ver seção de escalabilidade.
- `DEMO_MODE=true` encurta os intervalos de follow-up (30 min/2 h/24 h → 90 s/180 s/300 s) para a demonstração acontecer ao vivo; produção usa `DEMO_MODE=false`.
- Sem TTS (resposta em áudio) — só transcrição de entrada (STT). Sem integração com WhatsApp — aprovação de número de negócio não cabe no escopo desta POC.
