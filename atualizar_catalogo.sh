#!/usr/bin/env bash
# Atualiza o catalogo de imoveis de ponta a ponta.
#
# Por que existe: a importacao sao ~270 anuncios e ~800 fotos, vinda de um
# site de terceiro por uma rede que pode engasgar. Rodar "uma vez e torcer"
# nao serve -- qualquer 429 ou timeout no meio deixaria o catalogo pela
# metade, e o passo seguinte carregaria dado incompleto no banco sem
# ninguem perceber.
#
# Entao: o importador e RETOMAVEL de proposito (foto ja baixada e pulada,
# ver scripts/importar_orion.py) e este script o chama em laco ate o
# catalogo parar de crescer. So depois disso e que o banco e tocado.
#
#   ./atualizar_catalogo.sh            # importa, carrega no banco, refaz o site
#   ./atualizar_catalogo.sh --so-baixar # para depois da importacao
#
set -euo pipefail
cd "$(dirname "$0")"

SO_BAIXAR=0
for arg in "$@"; do
    case "$arg" in
        --so-baixar) SO_BAIXAR=1 ;;
        *) echo "argumento desconhecido: $arg" >&2; exit 2 ;;
    esac
done

if [ -d .venv ]; then
    # shellcheck disable=SC1091
    source .venv/bin/activate
fi

FOTOS="app/web/static/imoveis/orion"
CATALOGO="app/db/catalogo_orion.json"
MAX_TENTATIVAS=8

contar_fotos() { find "$FOTOS" -name '*.jpg' 2>/dev/null | wc -l | tr -d ' '; }

echo "==> importando catalogo (retomavel)"
anterior=-1
for tentativa in $(seq 1 "$MAX_TENTATIVAS"); do
    atual=$(contar_fotos)
    # Para quando uma passada inteira nao trouxe foto nova: ou acabou, ou
    # o que falta nao vai vir (anuncio sem dado estruturado, foto 404).
    if [ "$atual" -eq "$anterior" ] && [ "$tentativa" -gt 1 ]; then
        echo "    nada novo na tentativa $tentativa -- importacao estavel"
        break
    fi
    anterior=$atual
    echo "--- tentativa $tentativa/$MAX_TENTATIVAS (fotos ate agora: $atual)"
    # `|| true`: uma falha de rede no meio nao pode abortar o laco, que
    # existe justamente pra se recuperar dela.
    python -m scripts.importar_orion || true
done

echo
echo "==> catalogo: $(contar_fotos) fotos, $(du -sh "$FOTOS" 2>/dev/null | cut -f1)"
if [ ! -f "$CATALOGO" ]; then
    echo "ERRO: $CATALOGO nao foi gerado" >&2
    exit 1
fi
python - <<'PY'
import json, pathlib
d = json.loads(pathlib.Path("app/db/catalogo_orion.json").read_text(encoding="utf-8"))
sem_foto = [i["codigo"] for i in d if not i.get("fotos")]
print(f"    {len(d)} imoveis | {len(d) - len(sem_foto)} com foto | {len(sem_foto)} sem")
PY

[ "$SO_BAIXAR" -eq 1 ] && { echo "parando aqui (--so-baixar)"; exit 0; }

# Os passos abaixo so existem depois que o seed passar a ler o catalogo
# real. Enquanto nao existirem, avisa em vez de quebrar.
echo
if python -c "import importlib.util,sys; sys.exit(0 if importlib.util.find_spec('scripts.carregar_catalogo') else 1)" 2>/dev/null; then
    echo "==> carregando no banco"
    python -m scripts.carregar_catalogo
    echo "==> refazendo o site estatico"
    python -m site.build 2>/dev/null || python site/build.py
else
    echo "==> scripts/carregar_catalogo.py ainda nao existe: catalogo baixado, banco intacto"
fi

echo
echo "pronto."
