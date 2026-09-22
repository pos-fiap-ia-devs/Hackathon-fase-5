from app.core.scoring import calcular_score


def test_lead_sem_nada_score_zero():
    assert calcular_score({}) == 0


def test_lead_completo_urgente_ticket_alto_com_visita():
    """Ticket alto e relativo ao mercado do seed: com o estado de Sao Paulo
    (venda ate R$ 2 mi, `TICKET_TETO`), "alto" e perto do topo -- os
    R$ 700 mil desta fixture eram topo no seed antigo (Goiania, teto R$ 1
    mi) e hoje sao mercado medio, entao pontuam menos. Ver
    test_ticket_medio_pontua_menos_que_alto abaixo."""
    slots = {
        "faixa_preco_min": 900_000,
        "faixa_preco_max": 1_800_000,
        "quartos": 3,
        "regiao": "zona sul",
        "urgencia": "alta",
        "perfil_investidor": None,
        "ticket": 1_800_000,
        "expectativa_retorno": None,
    }
    # 7/8 campos preenchidos (perfil_investidor e expectativa_retorno ficam None,
    # mas so 1 conta como ausente pois ticket supre o outro cenario)
    score = calcular_score(slots, visita_agendada=True)
    assert score > 80


def test_ticket_medio_pontua_menos_que_alto():
    base = {"quartos": 2, "regiao": "Campinas", "urgencia": "alta"}
    medio = calcular_score({**base, "faixa_preco_max": 700_000, "ticket": 700_000})
    alto = calcular_score({**base, "faixa_preco_max": 1_900_000, "ticket": 1_900_000})
    assert alto > medio


def test_urgencia_alta_pontua_mais_que_baixa():
    base = {"faixa_preco_max": 400_000}
    alta = calcular_score({**base, "urgencia": "alta"})
    baixa = calcular_score({**base, "urgencia": "baixa"})
    assert alta > baixa


def test_visita_agendada_soma_dez_pontos():
    slots = {"urgencia": "media"}
    sem_visita = calcular_score(slots, visita_agendada=False)
    com_visita = calcular_score(slots, visita_agendada=True)
    assert com_visita - sem_visita == 10
