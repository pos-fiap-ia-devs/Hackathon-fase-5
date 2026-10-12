#!/usr/bin/env bash
# Reconstroi e sobe tudo. Apagar os dados e OPCIONAL.
#
#   ./rebuild.sh --manter   reconstroi e sobe NATIVO, SEM apagar nada
#   ./rebuild.sh --docker   reconstroi a IMAGEM e sobe em container, sem apagar
#   ./rebuild.sh            apaga leads e conversas antes (pede confirmacao)
#   ./rebuild.sh --tudo     idem, e TAMBEM resemeia os 200 imoveis
#   ./rebuild.sh -y         sem perguntar (para script/CI)
#
# Com `--manter` e `--docker` nao ha confirmacao: nada e destruido. Sao os
# modos pra usar depois de mexer no codigo, no site ou nas dependencias,
# mantendo a conversa que esta no meio de uma demonstracao.
#
# `--manter` roda a app NATIVA (uvicorn no host); `--docker` reconstroi a
# imagem e roda em container. Os dois disputam a porta 8000, entao o
# script derruba tudo antes -- nunca sobem juntos. O Ollama fica NATIVO
# nos dois casos: dentro do container no macOS nao ha Metal.
#
# Sem ele, apaga: leads, mensagens, slots, memoria, visitas,
# encaminhamentos, resumos, agent_traces e follow_up_jobs.
# Preserva sempre: imoveis (200 chamadas de embedding pra recriar) e
# usuarios (apagar reabriria o cadastro de administrador do painel).
#
# NAO duplica logica: compoe `stop.sh`, `limpar_leads.sh` e `run.sh`, que
# ja existem e sao testados. Uma copia a mais de "como derrubar o
# container" ou "como aplicar o schema" e uma copia pra divergir depois.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

RESEMEAR=""
SEM_PERGUNTAR=""
MANTER=""
DOCKER=""
for arg in "$@"; do
    case "$arg" in
        --manter|--sem-limpar) MANTER="1" ;;
        --docker) MANTER="1"; DOCKER="1" ;;
        --tudo) RESEMEAR="1" ;;
        -y|--sim) SEM_PERGUNTAR="1" ;;
        -h|--help) sed -n '2,24p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "argumento desconhecido: $arg (use --help)" >&2; exit 2 ;;
    esac
done

if [ -n "$MANTER" ] && [ -n "$RESEMEAR" ]; then
    echo "--manter/--docker e --tudo se contradizem: --tudo apaga os imoveis." >&2
    exit 2
fi

# ---------------------------------------------------------------- previa
# O banco precisa estar de pe so pra CONTAR. Se estiver parado, sobe so
# ele -- o resto sobe no fim, pelo run.sh.
docker compose up -d db >/dev/null 2>&1 || true
until docker compose exec -T db pg_isready -h 127.0.0.1 -U sdr -d sdr >/dev/null 2>&1; do
    sleep 1
done

if [ -z "$MANTER" ]; then
    if [ -d .venv ]; then
        source .venv/bin/activate
        echo "==> o que sera apagado"
        python -m scripts.limpar_leads --contar
        if [ -n "$RESEMEAR" ]; then
            echo "   + os 200 imoveis serao recriados (--tudo)"
        fi
    fi

    if [ -z "$SEM_PERGUNTAR" ]; then
        echo
        echo "Isto NAO tem desfazer. Digite 'rebuild' para confirmar:"
        read -r resposta
        if [ "$resposta" != "rebuild" ]; then
            echo "cancelado."
            exit 0
        fi
    fi
else
    if [ -n "$DOCKER" ]; then
        echo "==> modo --docker: imagem reconstruida, dados preservados"
    else
        echo "==> modo --manter: leads e conversas serao preservados"
    fi
fi

# ---------------------------------------------------------------- limpeza
echo
echo "==> parando tudo"
./stop.sh >/dev/null 2>&1 || true

echo "==> subindo o banco"
docker compose up -d db >/dev/null
until docker compose exec -T db pg_isready -h 127.0.0.1 -U sdr -d sdr >/dev/null 2>&1; do
    sleep 1
done

source .venv/bin/activate
if [ -z "$MANTER" ]; then
    echo "==> apagando leads e conversas"
    python -m scripts.limpar_leads
fi

if [ -n "$RESEMEAR" ]; then
    echo "==> resemeando imoveis (precisa do Ollama pros embeddings)"
    # `--forcar` faz TRUNCATE: so faz sentido depois da limpeza acima, com
    # as visitas ja apagadas.
    python -m scripts.preparar_banco --forcar
fi

# ---------------------------------------------------------------- reconstrucao
echo "==> reinstalando dependencias"
uv pip install -e . -q

echo "==> limpando bytecode"
# `__pycache__` de um modulo renomeado continua importavel e mascara erro
# de import ate alguem rodar num clone limpo.
find app scripts site tests -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true

# ---------------------------------------------------------------- sobe tudo
if [ -z "$DOCKER" ]; then
    echo
    echo "==> subindo tudo (run.sh)"
    ./run.sh
    exit 0
fi

# --docker: a app vai pra imagem, nao pro uvicorn do host.
echo
echo "==> Ollama (fica NATIVO: dentro do container nao ha Metal)"
if ! curl -sf http://localhost:11434 >/dev/null 2>&1; then
    OLLAMA_KEEP_ALIVE=24h ollama serve &
    sleep 2
fi

echo "==> reconstruindo a imagem e subindo"
docker compose --profile app up -d --build

echo -n "   aguardando healthcheck"
until [ "$(docker inspect -f '{{.State.Health.Status}}' sdr_app 2>/dev/null)" = "healthy" ]; do
    echo -n "."
    sleep 2
done
echo " ok"

echo "==> site da imobiliaria"
# O site aponta pro mesmo :8000 -- quem atende agora e o container.
python site/build.py >/dev/null
nohup python -m http.server 5500 --directory site/dist > site.log 2>&1 &
echo $! > .site.pid
echo "   http://localhost:5500 (pid $(cat .site.pid))"

echo
echo "   app em container: http://localhost:8000 | logs: docker compose logs -f app"
echo "   parar: ./stop.sh"
