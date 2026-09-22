"""Etapa 3 -- criterio de pronto: as 3 frases dos cenarios do PDF preenchem os
slots certos, sem Telegram e sem banco. Chama o Ollama real (sem mock) --
condiz com a proposta 100% local do projeto: se o Ollama nao estiver de pe
com as variaveis tunadas (secao 2 do PLANO), o teste falha de verdade, nao
mascara o problema.
"""

from app.agents.qualifier import extrair_slots, merge_slots


def test_cenario_1_compra():
    """PDF, Exemplo 1: 'Estou procurando apartamento na zona sul.'"""
    resultado, trace = extrair_slots("Estou procurando apartamento na zona sul.", {})
    assert resultado.intencao == "compra"
    assert "sul" in (resultado.regiao or "").lower()
    assert trace.erro is None


def test_cenario_2_investimento():
    """PDF, Exemplo 2: 'Quero investir em imóveis para renda.'"""
    resultado, trace = extrair_slots("Quero investir em imóveis para renda.", {})
    assert resultado.intencao == "investimento"
    assert trace.erro is None


def test_aluguel_com_quartos():
    """PDF exige identificar aluguel tambem, nao so compra/investimento."""
    resultado, trace = extrair_slots("Quero alugar um apartamento de 2 quartos.", {})
    assert resultado.intencao == "aluguel"
    assert resultado.quartos == 2
    assert trace.erro is None


def test_merge_nao_apaga_intencao_ja_conhecida():
    """2a mensagem nao fala de intencao -- merge nao pode voltar para 'indefinido'."""
    atuais = {"intencao": "compra", "regiao": "zona sul"}
    novo, _ = extrair_slots("E tem varanda?", atuais)
    merged = merge_slots(atuais, novo)
    assert merged["intencao"] == "compra"


def test_merge_preenche_campo_novo_sem_apagar_existente():
    atuais = {"intencao": "compra", "regiao": "zona sul"}
    novo, _ = extrair_slots("Ate uns 700 mil, uns 3 quartos.", atuais)
    merged = merge_slots(atuais, novo)
    assert merged["regiao"] == "zona sul"  # preservado
    assert merged["quartos"] == 3
    assert merged["faixa_preco_max"] == 700_000
