#!/bin/sh
# Prepara o banco e entrega o processo pro comando real.
#
# `exec` no fim e o ponto: sem ele, o shell continua como PID 1 e o
# SIGTERM do `docker stop` morre nele em vez de chegar no uvicorn -- o
# lifespan nao rodaria, e o bot do Telegram ficaria com long polling
# pendurado ate o timeout de 10s do Docker matar tudo no tapa.
set -e

echo "==> schema + seed (idempotente)"
python -m scripts.preparar_banco

echo "==> subindo app"
exec "$@"
