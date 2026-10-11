"""Score explicável e prioridade (core/scoring.py).

Dois contratos, e o segundo é o que costuma quebrar em silêncio:

1. A explicação **é** o cálculo, não uma narração dele. A soma das
   parcelas tem que ser exatamente o total -- se forem calculados por
   caminhos diferentes, um dia divergem e o painel mostra uma conta que
   não fecha.
2. Lista e detalhe do dashboard têm que concordar. A lista lê `leads.score`
   da coluna e o detalhe recalcula; sem a mesma função nos dois, um lead
   aparece "quente" na tabela e "morno" ao abrir.
"""

import pytest

from app.core.scoring import (
    SCORE_MORNO,
    SCORE_QUENTE,
    avaliar_criterios,
    calcular_score,
    calcular_score_detalhado,
    campos_faltantes,
    prioridade_de,
)

COMPLETO = {
    "intencao": "compra", "faixa_preco_min": 400_000, "faixa_preco_max": 800_000,
    "quartos": 3, "regiao": "zona sul", "urgencia": "alta",
}


# ---------------------------------------------------------------- a conta tem que fechar

def test_soma_das_parcelas_e_o_total():
    d = calcular_score_detalhado(COMPLETO)
    assert sum(c.pontos for c in d.criterios) == d.total


def test_total_bate_com_a_funcao_antiga():
    """`calcular_score` é o que grava em `leads.score` em 4 pontos de
    core/turno.py -- não pode divergir da versão detalhada."""
    for slots in [{}, COMPLETO, {"intencao": "investimento", "ticket": 500_000}]:
        for visita in (False, True):
            assert calcular_score(slots, visita_agendada=visita) == (
                calcular_score_detalhado(slots, visita_agendada=visita).total
            )


def test_nenhuma_parcela_passa_do_proprio_maximo():
    d = calcular_score_detalhado({**COMPLETO, "ticket": 99_000_000}, visita_agendada=True)
    assert all(0 <= c.pontos <= c.maximo for c in d.criterios)


def test_maximos_somam_cem():
    assert sum(c.maximo for c in avaliar_criterios({})) == 100


def test_toda_parcela_tem_motivo_legivel():
    """Parcela sem motivo vira linha vazia na tela."""
    for c in avaliar_criterios({}, visita_agendada=False):
        assert c.motivo.strip()
        assert c.nome.strip()


# ---------------------------------------------------------------- prioridade

def test_lead_vazio_e_frio():
    assert calcular_score_detalhado({}).prioridade == "frio"


def test_lead_completo_com_orcamento_e_quente():
    d = calcular_score_detalhado(COMPLETO, visita_agendada=True)
    assert d.total >= SCORE_QUENTE
    assert d.prioridade == "quente"


def test_sem_orcamento_nao_existe_lead_quente():
    """Regra de negócio sobreposta ao número: um lead pode somar 70+ com
    urgência alta e muitos campos e ainda não ter dito quanto pode pagar.
    Mandar o corretor ligar primeiro pra esse desperdiça a ligação."""
    sem_orcamento = {
        "intencao": "compra", "quartos": 3, "regiao": "zona sul", "urgencia": "alta",
        "perfil_investidor": "x", "expectativa_retorno": "y", "faixa_preco_min": 300_000,
    }
    d = calcular_score_detalhado(sem_orcamento, visita_agendada=True)
    assert d.prioridade != "quente"
    assert "orçamento" in d.justificativa


def test_ticket_conta_como_orcamento_para_investidor():
    """Investidor informa `ticket`, não `faixa_preco_max`."""
    investidor = {
        "intencao": "investimento", "ticket": 1_500_000, "urgencia": "alta",
        "perfil_investidor": "renda", "expectativa_retorno": "8% a.a.",
        "regiao": "centro", "quartos": 2,
    }
    assert calcular_score_detalhado(investidor, visita_agendada=True).prioridade == "quente"


# ---------------------------------------------------------------- lista × detalhe

@pytest.mark.parametrize(
    "slots",
    [
        {},
        COMPLETO,
        {**COMPLETO, "faixa_preco_max": None},                      # sem orçamento
        {"intencao": "investimento", "ticket": 900_000, "urgencia": "alta"},
        {"intencao": "compra", "quartos": 2, "urgencia": "alta", "regiao": "centro"},
    ],
)
@pytest.mark.parametrize("visita", [False, True])
def test_lista_e_detalhe_nunca_discordam(slots, visita):
    """`prioridade_de` (lista, a partir da coluna) tem que devolver o mesmo
    que `calcular_score_detalhado` (detalhe, recalculado)."""
    detalhe = calcular_score_detalhado(slots, visita_agendada=visita)
    assert prioridade_de(detalhe.total, slots) == detalhe.prioridade


def test_faixas_sao_as_mesmas_constantes_usadas_no_template():
    assert SCORE_QUENTE == 70
    assert SCORE_MORNO == 40


# ---------------------------------------------------------------- o que falta perguntar

def test_sem_intencao_falta_a_intencao():
    assert campos_faltantes({}) == ["intenção"]


def test_compra_nao_pede_campo_de_investidor():
    """Perguntar perfil de investidor a quem quer alugar é ruído."""
    faltantes = campos_faltantes({"intencao": "compra"})
    assert "orçamento" in faltantes
    assert not any("investidor" in f for f in faltantes)


def test_investimento_nao_pede_quartos():
    faltantes = campos_faltantes({"intencao": "investimento"})
    assert "ticket" in faltantes
    assert "quartos" not in faltantes


def test_qualificacao_completa_nao_tem_faltantes():
    assert campos_faltantes(COMPLETO) == []


# ---------------------------------------------------------------- completude × faltantes

@pytest.mark.parametrize(
    "slots",
    [
        COMPLETO,
        {"intencao": "aluguel", "faixa_preco_max": 3_000, "quartos": 2,
         "regiao": "centro", "urgencia": "baixa"},
        {"intencao": "investimento", "perfil_investidor": "renda", "ticket": 500_000,
         "expectativa_retorno": "8% a.a.", "regiao": "zona sul"},
    ],
)
def test_qualificacao_completa_pontua_a_completude_cheia(slots):
    """A contradição que o painel expôs: "Dados coletados 20/40 — 4 de 8"
    logo acima de "Qualificação completa". Um lead de compra jamais
    preenche perfil_investidor/ticket/expectativa_retorno, e estava
    limitado a 25 dos 40 pontos por campos que ninguém iria preencher."""
    detalhe = calcular_score_detalhado(slots)
    completude = next(c for c in detalhe.criterios if c.nome == "Dados coletados")
    assert detalhe.campos_faltantes == []
    assert completude.pontos == completude.maximo


def test_lead_de_compra_pode_chegar_a_cem():
    """Consequência direta: antes, o teto real de um lead de compra era 85."""
    assert calcular_score_detalhado(COMPLETO, visita_agendada=True) .total <= 100
    quase = {**COMPLETO, "faixa_preco_max": 2_000_000}
    assert calcular_score_detalhado(quase, visita_agendada=True).total == 100


def test_completude_e_faltantes_concordam_sempre():
    for intencao in ("compra", "aluguel", "investimento"):
        parcial = {"intencao": intencao, "regiao": "centro"}
        detalhe = calcular_score_detalhado(parcial)
        completude = next(c for c in detalhe.criterios if c.nome == "Dados coletados")
        assert bool(detalhe.campos_faltantes) == (completude.pontos < completude.maximo)
