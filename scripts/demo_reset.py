"""Reseta o estado de demo sem tocar no seed de imoveis (Etapa 10 do
PLANO.md, item 6 do checklist pre-demo). `leads` e todo o resto que
depende dele (slots, mensagens, memoria, visitas, encaminhamentos,
resumos, agent_traces, follow_up_jobs) cascateia; `imoveis` fica intocado
-- e o dado caro de recriar (120 chamadas de embedding).

Rodar: python -m scripts.demo_reset
"""

from app.db.repo import get_conn

TABELAS_DE_LEAD = (
    "leads",  # CASCADE cobre slots, mensagens, memoria, visitas,
    # encaminhamentos, resumos, agent_traces, follow_up_jobs -- todos com
    # FK lead_id ON DELETE CASCADE (schema.sql)
)


def run() -> None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            for tabela in TABELAS_DE_LEAD:
                cur.execute(f"TRUNCATE {tabela} RESTART IDENTITY CASCADE")
        conn.commit()
    print("demo reset concluido -- leads e derivados limpos, imoveis intocado.")


if __name__ == "__main__":
    run()
