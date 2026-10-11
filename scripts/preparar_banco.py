"""Aplica schema e semeia imóveis, se preciso. Idempotente.

Existe pro entrypoint do container, que roda a cada `docker compose up` --
mas serve igual fora do Docker, no lugar do par `psql -f schema.sql` +
`python -m app.db.seed` do run.sh.

Duas diferenças que importam em relação a chamar o seed direto:

1. O schema é aplicado com psycopg, não com o binário `psql`. Dentro da
   imagem isso evita instalar o postgresql-client inteiro só pra rodar um
   arquivo .sql, e usa a mesma dependência que o app já tem.

2. `app.db.seed` começa com `TRUNCATE imoveis RESTART IDENTITY CASCADE`.
   Rodar isso a cada boot apagaria as VISITAS junto (o CASCADE desce por
   `visitas.imovel_id`) -- um restart do container levaria embora a agenda
   dos corretores. Por isso aqui ele só é chamado quando a tabela está
   vazia; `--forcar` existe pra quem realmente quer resemear.
"""

import argparse
import logging
import sys
from pathlib import Path

import psycopg

from app.core.config import get_settings

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("preparar_banco")

SCHEMA = Path(__file__).resolve().parent.parent / "app" / "db" / "schema.sql"


def _sem_comentarios(sql: str) -> str:
    """Tira os comentários `--` antes de separar os comandos.

    Necessário, não cosmético: o schema tem 71 linhas de comentário e
    várias com ponto-e-vírgula dentro ("...centro; fora dela",
    "repo.retomar_atendimento; se havia visita"). Separar em `;` sem
    limpar isso corta comandos no meio e o Postgres recusa com
    `syntax error at end of input`.

    Só trata comentário de linha. Serve porque este arquivo não tem `/*
    */` nem literal com `--` dentro (conferido) -- não é um parser de SQL
    de uso geral.
    """
    return "\n".join(linha.split("--")[0] for linha in sql.splitlines())


def aplicar_schema(url: str) -> None:
    """O arquivo é escrito pra ser reaplicado: `CREATE TABLE` duplicado
    falha e é ignorado (é o que o `|| true` do run.sh faz), e a cauda de
    `ALTER TABLE ... IF NOT EXISTS` é o caminho pelo qual coluna nova chega
    num banco que já existe. Por isso cada comando vai numa transação
    própria -- num bloco só, o primeiro erro abortaria todo o resto.
    """
    sql = _sem_comentarios(SCHEMA.read_text())
    aplicados = ignorados = 0
    with psycopg.connect(url, autocommit=True) as conn:
        for comando in filter(None, (c.strip() for c in sql.split(";"))):
            try:
                conn.execute(comando)
                aplicados += 1
            except psycopg.errors.DuplicateTable:
                ignorados += 1
            except psycopg.errors.DuplicateObject:  # índice já existente
                ignorados += 1
    logger.info("schema: %s comandos aplicados, %s já existiam", aplicados, ignorados)


def imoveis_no_banco(url: str) -> int:
    with psycopg.connect(url) as conn:
        return conn.execute("SELECT count(*) FROM imoveis").fetchone()[0]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--forcar", action="store_true",
        help="resemeia mesmo com imóveis no banco (TRUNCATE: apaga visitas junto)",
    )
    args = parser.parse_args()

    url = get_settings().database_url
    aplicar_schema(url)

    existentes = imoveis_no_banco(url)
    if existentes and not args.forcar:
        logger.info("seed: %s imóveis já no banco, nada a fazer", existentes)
        return 0

    # Import tardio: o seed abre pool e fala com o Ollama pros embeddings.
    # Carregar isso quando só se quer o schema seria custo à toa.
    from app.db.seed import run  # noqa: PLC0415

    run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
