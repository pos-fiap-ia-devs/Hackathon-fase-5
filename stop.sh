#!/usr/bin/env bash
# Para tudo que o run.sh sobe: uvicorn, Postgres (docker) e Ollama.
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

echo "==> uvicorn (app.web.api)"
if [ -f .run.pid ] && kill -0 "$(cat .run.pid)" 2>/dev/null; then
    kill "$(cat .run.pid)"
    rm -f .run.pid
    echo "   parado"
elif pgrep -f "uvicorn app.web.api" >/dev/null 2>&1; then
    pkill -f "uvicorn app.web.api"
    rm -f .run.pid
    echo "   parado"
else
    echo "   ja estava parado"
fi

echo "==> Postgres (docker compose)"
docker compose down

echo "==> Ollama"
if pgrep -f "ollama serve" >/dev/null 2>&1; then
    pkill -f "ollama serve"
    echo "   parado (outros projetos que dependam do Ollama tambem param)"
else
    echo "   ja estava parado"
fi

echo "==> feito"
