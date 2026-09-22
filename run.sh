#!/usr/bin/env bash
# Sobe o projeto local: Ollama, Postgres (docker), venv, deps, schema+seed, uvicorn.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

echo "==> Ollama"
if ! pgrep -f "ollama serve" >/dev/null 2>&1 && ! curl -sf http://localhost:11434 >/dev/null 2>&1; then
    # Sem isso o Ollama descarrega o modelo apos 5min de silencio (default) --
    # cada resposta seguinte paga reload completo (10-40s medido, ver IA.md).
    OLLAMA_KEEP_ALIVE=24h ollama serve &
    sleep 2
fi
ollama pull qwen3.5:9b
ollama pull nomic-embed-text

echo "==> Postgres (docker compose)"
docker compose up -d
echo -n "   aguardando healthcheck"
until [ "$(docker inspect -f '{{.State.Health.Status}}' sdr_db 2>/dev/null)" = "healthy" ]; do
    echo -n "."
    sleep 1
done
echo " ok"

echo "==> venv + dependências"
if [ ! -d .venv ]; then
    uv venv --python 3.12
fi
source .venv/bin/activate
uv pip install -e . -q

if [ ! -f .env ]; then
    cp .env.example .env
    echo "   .env criado a partir de .env.example -- edite TELEGRAM_BOT_TOKEN se quiser Telegram"
fi
set -a
source .env
set +a

echo "==> schema + seed"
psql "$DATABASE_URL" -f app/db/schema.sql >/dev/null 2>&1 || true
python -m app.db.seed

echo "==> subindo app em background: http://localhost:8000"
nohup uvicorn app.web.api:app --host 0.0.0.0 --port 8000 > run.log 2>&1 &
echo $! > .run.pid
sleep 1
echo "   pid $(cat .run.pid) | log: run.log | parar: ./stop.sh"
