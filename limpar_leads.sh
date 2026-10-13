#!/usr/bin/env bash
# Apaga leads e TUDO que depende deles: mensagens, slots, memoria, visitas,
# encaminhamentos, resumos, agent_traces e follow_up_jobs.
#
#   ./limpar_leads.sh           apaga tudo (pede confirmacao)
#   ./limpar_leads.sh --teste   so o lixo de teste/captura, preserva os reais
#   ./limpar_leads.sh -y        sem perguntar (para script/CI)
#
# NAO toca em `imoveis` (200 chamadas de embedding pra recriar) nem em
# `usuarios` (apagar reabriria o cadastro de administrador do painel).
#
# Mostra o que vai sumir ANTES de perguntar: confirmar no escuro nao e
# confirmacao. A operacao nao tem desfazer.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

APENAS_TESTE=""
SEM_PERGUNTAR=""
for arg in "$@"; do
    case "$arg" in
        --teste) APENAS_TESTE="--teste" ;;
        -y|--sim) SEM_PERGUNTAR="1" ;;
        -h|--help) sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "argumento desconhecido: $arg (use --help)" >&2; exit 2 ;;
    esac
done

if [ ! -d .venv ]; then
    echo "venv nao encontrada -- rode ./run.sh primeiro." >&2
    exit 1
fi
source .venv/bin/activate

# O banco precisa estar de pe; sem isso o erro sai como traceback de pool.
if ! docker compose ps --status running --services 2>/dev/null | grep -qx db; then
    echo "Postgres nao esta rodando. Suba com: docker compose up -d" >&2
    exit 1
fi

if [ -n "$APENAS_TESTE" ]; then
    echo "==> Leads de TESTE/captura que seriam apagados:"
else
    echo "==> TUDO que seria apagado:"
fi
python -m scripts.limpar_leads --contar $APENAS_TESTE

if [ -z "$SEM_PERGUNTAR" ]; then
    echo
    echo "Isto NAO tem desfazer. Digite 'apagar' para confirmar:"
    read -r resposta
    if [ "$resposta" != "apagar" ]; then
        echo "cancelado."
        exit 0
    fi
fi

echo
python -m scripts.limpar_leads $APENAS_TESTE
