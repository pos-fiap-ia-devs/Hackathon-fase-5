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

echo "==> site (http.server :5500)"
# Mesmo padrao do uvicorn acima: pid salvo, com pkill de reserva pra quando
# o arquivo some (ctrl-C no terminal errado, maquina reiniciada).
if [ -f .site.pid ] && kill -0 "$(cat .site.pid)" 2>/dev/null; then
    kill "$(cat .site.pid)"
    rm -f .site.pid
    echo "   parado"
elif pgrep -f "http.server 5500" >/dev/null 2>&1; then
    pkill -f "http.server 5500"
    rm -f .site.pid
    echo "   parado"
else
    echo "   ja estava parado"
fi

echo "==> Postgres + app (docker compose)"
# `--profile app` e obrigatorio: sem ele o `down` derruba so o `sdr_db` e
# deixa o `sdr_app` de pe, segurando a porta 8000. Testado -- o stop.sh
# dizia "feito" com o container ainda rodando, e o proximo `uvicorn` local
# perdia a porta pro container (que serve o codigo da imagem, nao o do
# disco). Com o profile, derruba os dois; sem o app rodando, nao muda nada.
docker compose --profile app down

echo "==> Ollama"
if pgrep -f "ollama serve" >/dev/null 2>&1; then
    pkill -f "ollama serve"
    echo "   parado (outros projetos que dependam do Ollama tambem param)"
else
    echo "   ja estava parado"
fi

echo "==> feito"
