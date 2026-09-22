"""Observabilidade (diferencial do PDF). Uma linha por chamada de agente:
turno, agente, modelo, tokens, latencia, tokens/s -- medido, nao estimado
(secao 7 do PLANO, argumento de escalabilidade).
"""

from dataclasses import dataclass

from app.db.repo import get_conn


@dataclass
class TraceInfo:
    agente: str
    modelo: str
    prompt_tokens: int | None
    completion_tokens: int | None
    latencia_ms: int
    tokens_por_s: float | None
    erro: str | None = None


def registrar_trace(trace: TraceInfo, *, lead_id: int | None = None, turno: int | None = None) -> None:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO agent_traces
                (lead_id, turno, agente, modelo, prompt_tokens, completion_tokens,
                 latencia_ms, tokens_por_s, erro)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                lead_id, turno, trace.agente, trace.modelo,
                trace.prompt_tokens, trace.completion_tokens,
                trace.latencia_ms, trace.tokens_por_s, trace.erro,
            ),
        )
        conn.commit()
