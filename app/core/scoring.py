"""Score do lead (secao 5.7). Regra determinística, nunca pedida ao LLM --
um numero estavel no dashboard vale mais que um numero "inteligente".

    score = 40 * (slots_preenchidos / slots_totais)
          + 30 * peso_urgencia      (alta=1,0 · media=0,6 · baixa=0,3)
          + 20 * peso_ticket        (faixa normalizada)
          + 10 * (visita_agendada ? 1 : 0)
"""

# intencao fica de fora -- e sempre preenchida pelo schema (Literal obrigatorio),
# nao mede progresso de qualificacao.
CAMPOS_QUALIFICACAO = [
    "faixa_preco_min",
    "faixa_preco_max",
    "quartos",
    "regiao",
    "urgencia",
    "perfil_investidor",
    "ticket",
    "expectativa_retorno",
]

PESO_URGENCIA = {"alta": 1.0, "media": 0.6, "baixa": 0.3}

# normaliza o ticket ate R$ 2 mi -- acima disso o peso satura em 1.0.
# Ajustado ao mercado do seed (estado de Sao Paulo): venda vai de
# R$110 mil a R$2 mi, entao o teto acompanha o topo real da base.
TICKET_TETO = 2_000_000


def calcular_score(slots: dict, *, visita_agendada: bool = False) -> int:
    preenchidos = sum(1 for c in CAMPOS_QUALIFICACAO if slots.get(c) is not None)
    completude = preenchidos / len(CAMPOS_QUALIFICACAO)

    urgencia = PESO_URGENCIA.get(slots.get("urgencia"), 0.0)

    ticket = slots.get("ticket") or slots.get("faixa_preco_max") or 0
    peso_ticket = min(ticket / TICKET_TETO, 1.0)

    score = 40 * completude + 30 * urgencia + 20 * peso_ticket + 10 * (1 if visita_agendada else 0)
    return round(score)
