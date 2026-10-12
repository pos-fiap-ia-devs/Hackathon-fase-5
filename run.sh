#!/usr/bin/env bash
# Sobe o projeto local inteiro: Ollama, Postgres (docker), venv, deps,
# schema+seed idempotentes e uvicorn (Telegram + dashboard + chat web +
# widget + CRM + follow-up, tudo num processo so). Parar: ./stop.sh
#
# Para avaliar sem GPU, veja LLM_DEMO no README -- nao precisa deste script.
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
# `scripts/preparar_banco.py`, nao `psql -f` + `python -m app.db.seed`.
# Dois motivos, os dois descobertos doendo:
#
# 1. O seed antigo comecava com `TRUNCATE imoveis ... CASCADE`, e
#    `visitas.imovel_id` referencia `imoveis`: chama-lo a cada subida
#    apagava a agenda dos corretores. O seed agora CARREGA o catalogo real
#    por upsert (`scripts/carregar_catalogo.py`), que nao trunca nada --
#    entao roda a cada subida: atualiza o que mudou, preserva ids e visitas.
#    So `--forcar` apaga, de proposito.
# 2. O `psql -f ... 2>&1 || true` engolia TUDO: sem o binario do psql no
#    PATH, o schema simplesmente nao era aplicado e ninguem ficava sabendo
#    -- com tabela nova (usuarios/sessoes/convites) isso vira erro de
#    coluna inexistente la na frente. O script usa psycopg, que o projeto
#    ja tem, e informa o que aplicou.
#
# Precisa do Ollama no ar (embeddings) so na PRIMEIRA carga; nas seguintes
# nada muda e nenhum embedding e refeito (~2s).
python -m scripts.preparar_banco

echo "==> site da imobiliaria"
# Estatico: o build le os imoveis do banco (ja de pe acima) e escreve
# site/dist. Rapido e idempotente -- refaz do zero a cada subida, entao
# imovel novo no seed aparece sem ninguem lembrar de rodar nada.
python site/build.py >/dev/null
nohup python -m http.server 5500 --directory site/dist > site.log 2>&1 &
echo $! > .site.pid
echo "   http://localhost:5500 (pid $(cat .site.pid))"

echo "==> subindo app em background: http://localhost:8000"
nohup uvicorn app.web.api:app --host 0.0.0.0 --port 8000 > run.log 2>&1 &
echo $! > .run.pid
sleep 1
echo "   pid $(cat .run.pid) | log: run.log | parar: ./stop.sh"
