"""Busca hibrida de imoveis (secao 5.4 do PLANO.md).

Preco, quartos, zona e finalidade sao atributos estruturados -- filtram com WHERE.
O pgvector so ranqueia o descritivo livre ("varanda gourmet", "pe na areia"),
combinado com tsvector:

    score_final = 0.6 * similaridade_vetorial + 0.4 * ts_rank

Sem indice vetorial de proposito -- 120 linhas nao justificam ivfflat/hnsw
(ver comentario em app/db/schema.sql).
"""

from pgvector.psycopg import Vector

from app.core.llm import embed_text
from app.db.repo import get_conn, sql_regiao_casa


def buscar_imoveis(
    *,
    finalidade: str | None = None,
    zona: str | None = None,
    preco_min: int | None = None,
    preco_max: int | None = None,
    quartos_min: int | None = None,
    tipo: str | None = None,
    texto_livre: str | None = None,
    so_investimento: bool = False,
    limite: int = 5,
) -> list[dict]:
    """Filtra por SQL (estruturado) e ranqueia por texto livre (vetor + tsvector).

    texto_livre entra sempre no ts_rank; so entra no calculo vetorial se houver
    conteudo -- descritivo vazio nao deve competir por similaridade.

    so_investimento: so imoveis com `rentabilidade_estimada` (os marcados
    pra investimento), maior rentabilidade primeiro -- e o que o cenario 2
    mostra antes de agendar com o especialista.
    """
    where = ["1=1"]
    params: dict = {}

    if finalidade:
        where.append("finalidade = %(finalidade)s")
        params["finalidade"] = finalidade
    if zona:
        # Match por zona OU bairro OU cidade -- o cliente responde "onde
        # voce procura?" de tres formas diferentes e todas sao validas:
        # "zona sul" (zona, so faz sentido na capital), "Moema" (bairro),
        # "Campinas" (cidade). O `qualifier` extrai o texto literal, sem
        # normalizar pra nenhuma das tres (nenhuma regra do prompt pede
        # isso), entao quem resolve e o SQL contra o dado real -- mesma
        # filosofia de guardrail de `_resolver_por_texto` (core/turno.py).
        #
        # Achado em uso real: com `zona = %s` sozinho, responder pelo nome
        # do bairro fazia a busca voltar vazia PRA SEMPRE, e a mensagem de
        # "nao encontrei nada" (fixa, guardrail da secao 5.6) se repetia
        # identica em todo turno seguinte, mesmo mudando preco/quartos.
        # Mesmo criterio da validacao (`repo.regiao_existe`), de uma
        # definicao so -- as duas ja divergiram: a validacao aceitava
        # "Sao Paulo (Capital)" e a busca recusava, entao o agente dizia
        # "nao encontrei nada" pra uma regiao que acabara de aceitar.
        where.append(sql_regiao_casa("zona", "zona_like"))
        params["zona"] = zona
        params["zona_like"] = f"%{zona}%"
    if preco_min is not None:
        where.append("preco >= %(preco_min)s")
        params["preco_min"] = preco_min
    if preco_max is not None:
        where.append("preco <= %(preco_max)s")
        params["preco_max"] = preco_max
    if quartos_min is not None:
        where.append("quartos >= %(quartos_min)s")
        params["quartos_min"] = quartos_min
    if tipo:
        # Igualdade exata: `tipo` vem do Literal fechado do qualificador, nao
        # de texto livre, entao nao ha o que normalizar.
        where.append("tipo = %(tipo)s")
        params["tipo"] = tipo
    if so_investimento:
        where.append("rentabilidade_estimada IS NOT NULL")

    where_sql = " AND ".join(where)

    if texto_livre:
        vetor = embed_text(texto_livre)
        params["vetor"] = Vector(vetor)
        params["query_ts"] = texto_livre
        params["limite"] = limite
        sql = f"""
            SELECT id, codigo, titulo, tipo, finalidade, bairro, zona, cidade, preco,
                   quartos, banheiros, vagas, area, condominio, iptu, suites,
                   comodidades, campos_estimados, origem,
                   rentabilidade_estimada, descricao, fotos,
                   0.6 * (1 - (embedding <=> %(vetor)s))
                   + 0.4 * coalesce(ts_rank(busca, plainto_tsquery('portuguese', %(query_ts)s)), 0)
                   AS score
            FROM imoveis
            WHERE {where_sql}
            ORDER BY score DESC
            LIMIT %(limite)s
        """
    else:
        params["limite"] = limite
        sql = f"""
            SELECT id, codigo, titulo, tipo, finalidade, bairro, zona, cidade, preco,
                   quartos, banheiros, vagas, area, condominio, iptu, suites,
                   comodidades, campos_estimados, origem,
                   rentabilidade_estimada, descricao, fotos, NULL AS score
            FROM imoveis
            WHERE {where_sql}
            ORDER BY {"rentabilidade_estimada DESC, preco ASC" if so_investimento else "preco ASC"}
            LIMIT %(limite)s
        """

    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()
