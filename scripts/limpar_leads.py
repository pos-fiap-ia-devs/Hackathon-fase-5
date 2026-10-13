"""Apaga leads e tudo que depende deles. Usado por `./limpar_leads.sh`.

`imoveis` e `usuarios` NUNCA são tocados: o primeiro custa 200 chamadas de
embedding pra recriar, o segundo é quem opera o painel -- apagá-lo
reabriria o cadastro de administrador (web/api.py::_contexto_registro).

O resto cai por `ON DELETE CASCADE`: slots, mensagens, memoria, visitas,
encaminhamentos, resumos, agent_traces, follow_up_jobs (schema.sql).

    python -m scripts.limpar_leads --contar          só mostra
    python -m scripts.limpar_leads                   apaga tudo
    python -m scripts.limpar_leads --teste           só o lixo de teste
"""

import argparse
import sys

from app.db.repo import get_conn

# Leads que não são de cliente de verdade: criados pela suíte, pelas
# capturas de tela ou por abrir `/chat` (cada visita cria um `web:<uuid>`).
# Mantido como lista explícita, não heurística: apagar o lead de um
# cliente real por engano é o erro caro aqui.
PADROES_DE_TESTE = (
    "tg:adversarial%",
    "tg:teste%",
    "tg:debug%",
    "cli:teste%",
    "cli:demo%",
    "web:demo-captura%",
    "widget:demo-captura%",
)

TABELAS_DERIVADAS = (
    "mensagens", "slots", "memoria", "visitas",
    "encaminhamentos", "resumos", "agent_traces", "follow_up_jobs",
)


def _onde(apenas_teste: bool) -> tuple[str, dict]:
    if not apenas_teste:
        return "", {}
    condicoes = " OR ".join(f"chat_id LIKE %(p{i})s" for i in range(len(PADROES_DE_TESTE)))
    params = {f"p{i}": padrao for i, padrao in enumerate(PADROES_DE_TESTE)}
    return f" WHERE {condicoes}", params


def contar(apenas_teste: bool = False) -> dict[str, int]:
    """O que seria apagado, por tabela. Chamado antes de qualquer DELETE --
    confirmar no escuro não é confirmação."""
    onde, params = _onde(apenas_teste)
    contagens = {}
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(f"SELECT count(*) AS n FROM leads{onde}", params)
        contagens["leads"] = cur.fetchone()["n"]
        for tabela in TABELAS_DERIVADAS:
            cur.execute(
                f"SELECT count(*) AS n FROM {tabela}"
                f" WHERE lead_id IN (SELECT id FROM leads{onde})",
                params,
            )
            contagens[tabela] = cur.fetchone()["n"]
    return contagens


def limpar(apenas_teste: bool = False) -> int:
    """DELETE, não TRUNCATE: só o TRUNCATE aceita `RESTART IDENTITY`, mas
    ele não aceita WHERE -- e o modo `--teste` precisa de WHERE. Um único
    caminho pros dois modos vale mais que reiniciar a sequência de ids."""
    onde, params = _onde(apenas_teste)
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(f"DELETE FROM leads{onde}", params)
        apagados = cur.rowcount
        conn.commit()
    return apagados


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contar", action="store_true", help="só mostra, não apaga")
    parser.add_argument("--teste", action="store_true",
                        help="só os leads de teste/captura, preserva os reais")
    args = parser.parse_args()

    contagens = contar(args.teste)
    if args.contar:
        for tabela, n in contagens.items():
            print(f"{n:>8}  {tabela}")
        return 0

    if not contagens["leads"]:
        print("nada a apagar.")
        return 0

    apagados = limpar(args.teste)
    derivados = sum(n for t, n in contagens.items() if t != "leads")
    print(f"{apagados} leads apagados, {derivados} registros derivados em cascata.")
    print("imoveis e usuarios preservados.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
