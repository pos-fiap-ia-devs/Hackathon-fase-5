"""Guards de infraestrutura da suíte.

O projeto roda 100% local, e boa parte dos testes é de integração de
verdade: fala com o Postgres e com o Ollama reais, sem mock. Isso é
proposital -- mockar o LLM mascararia justamente os bugs de formato de
schema que custaram caro (ver IA.md).

O problema não era esse. Era que, sem os serviços de pé, esses testes
FALHAVAM em vez de pular. Quem clonasse o repo e rodasse `pytest` antes de
subir o Docker via 17 falhas vermelhas e ~6 minutos de timeout, e não
tinha como distinguir "faltou infra" de "o projeto está quebrado".

Daí estes guards. Marcam o teste como `skipped` com a razão em texto, e o
comando pra resolver:

    17 skipped  ·  precisa do Postgres (docker compose up -d)

Três detalhes que importam:

- A sonda usa `psycopg.connect(connect_timeout=2)`, nunca `repo.get_conn()`.
  O pool do psycopg fica ~30s reconectando antes de desistir, e esse tempo
  seria pago em toda rodada com o Docker parado.
- O resultado é cacheado (`lru_cache`): a sonda roda uma vez por sessão,
  não uma vez por teste.
- Banco NO AR mas VAZIO também pula. É um estado real -- volume recém
  criado, seed nunca rodado -- e foi o que fez `test_cidade_nao_atendida`
  falhar mesmo depois do `docker compose up`.
"""

from functools import lru_cache

import httpx
import psycopg
import pytest

from app.core.config import get_settings

TIMEOUT_SONDA_S = 2


@lru_cache(maxsize=1)
def postgres_no_ar() -> bool:
    try:
        with psycopg.connect(get_settings().database_url, connect_timeout=TIMEOUT_SONDA_S) as conn:
            conn.execute("SELECT 1")
        return True
    except Exception:
        return False


@lru_cache(maxsize=1)
def banco_semeado() -> bool:
    """Postgres no ar E com imóveis. Separado de `postgres_no_ar` pra que a
    mensagem do skip diga qual dos dois passos falta."""
    if not postgres_no_ar():
        return False
    try:
        with psycopg.connect(get_settings().database_url, connect_timeout=TIMEOUT_SONDA_S) as conn:
            return conn.execute("SELECT count(*) FROM imoveis").fetchone()[0] > 0
    except Exception:
        return False


@lru_cache(maxsize=1)
def ollama_no_ar() -> bool:
    """Checa o /api/tags, que só lista o que já está em disco -- não
    carrega modelo nem gasta inferência."""
    try:
        base = get_settings().ollama_host.rstrip("/")
        return httpx.get(f"{base}/api/tags", timeout=TIMEOUT_SONDA_S).status_code == 200
    except Exception:
        return False


# Marcadores prontos pros arquivos de teste: `pytestmark = precisa_de_banco`.
precisa_de_banco = pytest.mark.skipif(
    not postgres_no_ar(),
    reason="precisa do Postgres (docker compose up -d)",
)

precisa_de_seed = pytest.mark.skipif(
    not banco_semeado(),
    reason="precisa do Postgres com imóveis (docker compose up -d && python -m app.db.seed)",
)

precisa_de_ollama = pytest.mark.skipif(
    not ollama_no_ar(),
    reason="precisa do Ollama nativo (ollama serve && ollama pull qwen3.5:9b)",
)
