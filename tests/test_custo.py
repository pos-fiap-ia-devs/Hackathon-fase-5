"""Custo contrafactual (core/custo.py).

Função pura, sem banco: a conta é tokens × preço de tabela. O que estes
testes protegem é o que quebraria em silêncio -- entrada e saída trocadas
(o erro mais provável, porque saída custa mais caro) e divisão por zero
numa janela sem atendimento.
"""

from app.core import custo
from app.core.config import get_settings


def test_sem_token_nao_custa_nada():
    c = custo.calcular(0, 0)
    assert c.usd == 0.0
    assert c.brl == 0.0


def test_um_milhao_de_tokens_de_entrada_custa_o_preco_de_tabela():
    s = get_settings()
    assert custo.calcular(custo.UM_MILHAO, 0).usd == s.preco_ref_usd_por_mi_entrada


def test_um_milhao_de_tokens_de_saida_custa_o_preco_de_tabela():
    s = get_settings()
    assert custo.calcular(0, custo.UM_MILHAO).usd == s.preco_ref_usd_por_mi_saida


def test_saida_custa_mais_que_entrada():
    """Se alguém trocar os dois campos de lugar, este teste cai -- e é o
    erro mais provável neste arquivo."""
    assert custo.calcular(0, 1000).usd > custo.calcular(1000, 0).usd


def test_custo_soma_as_duas_pontas():
    só_entrada = custo.calcular(5000, 0).usd
    só_saida = custo.calcular(0, 3000).usd
    assert custo.calcular(5000, 3000).usd == só_entrada + só_saida


def test_brl_usa_a_cotacao_configurada():
    c = custo.calcular(10_000, 2_000)
    assert c.brl == c.usd * get_settings().cotacao_usd_brl


def test_total_de_tokens_soma_as_duas_pontas():
    assert custo.calcular(700, 300).total_tokens == 1000


def test_janela_sem_lead_nao_divide_por_zero():
    assert custo.por_conversa(10_000, 5_000, 0).usd == 0.0


def test_lead_negativo_tambem_e_tratado():
    """Defensivo: `count(DISTINCT ...)` nunca devolve negativo, mas o
    guard é `<= 0` e o teste documenta que é de propósito."""
    assert custo.por_conversa(10_000, 5_000, -1).usd == 0.0


def test_custo_por_conversa_divide_pelo_numero_de_leads():
    total = custo.calcular(12_000, 4_000).usd
    assert custo.por_conversa(12_000, 4_000, 4).usd == total / 4
