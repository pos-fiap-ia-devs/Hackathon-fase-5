#!/usr/bin/env bash
# Baixa os pesos do whisper-small-mlx direto (sem passar pelo cliente Python
# do HF Hub). Motivo: em teste real nesta rede, o download via
# huggingface_hub/mlx_whisper travou indefinidamente (0 bytes por minutos,
# HTTP/2 sem erro nem timeout natural) -- `curl --http1.1 -C -` no mesmo
# arquivo funcionou de primeira. Roda uma vez; depois aponte STT_MODEL_PATH
# no .env pro diretorio de destino, o app nao chama o HF Hub de novo.
set -euo pipefail

REPO="${1:-mlx-community/whisper-small-mlx}"
DESTINO="${STT_MODEL_PATH:-$HOME/.cache/agente-sdr-imobiliario/$(basename "$REPO")}"

mkdir -p "$DESTINO"
echo "Baixando $REPO -> $DESTINO"

curl -fSL --http1.1 -C - -o "$DESTINO/config.json" \
    "https://huggingface.co/$REPO/resolve/main/config.json"
curl -fSL --http1.1 -C - -o "$DESTINO/weights.npz" \
    "https://huggingface.co/$REPO/resolve/main/weights.npz"

echo "OK. Adicione no .env: STT_MODEL_PATH=$DESTINO"
