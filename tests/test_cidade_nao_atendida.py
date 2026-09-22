"""Cidade fora da area de atuacao (pedido do usuario): em vez de "nao
encontrei nada", o agente diz ONDE atende. Roda contra o Postgres real."""

from app.db import repo


def test_reconhece_cidade_bairro_e_zona_da_base():
    assert repo.regiao_existe("Campinas")   # cidade
    assert repo.regiao_existe("Moema")      # bairro
    assert repo.regiao_existe("zona sul")   # zona


def test_nao_reconhece_cidade_fora_da_base():
    assert not repo.regiao_existe("Bauru")
    assert not repo.regiao_existe("Rio de Janeiro")
    assert not repo.regiao_existe("Piracicaba")


def test_lista_de_cidades_vem_do_banco():
    """Nome de cidade e preco sao montados em codigo a partir do SELECT --
    o LLM so escreve a frase em volta (mesmo guardrail do card, secao 5.6).
    Prometer cidade que a imobiliaria nao atende seria promessa impossivel
    de cumprir."""
    cidades = repo.cidades_disponiveis("venda")
    assert cidades, "seed sem imovel de venda"
    nomes = {c["cidade"] for c in cidades}
    assert "Campinas" in nomes
    assert "Bauru" not in nomes
    # ordenado por volume de estoque, com preco de entrada real
    assert all(c["n"] > 0 and c["preco_min"] > 0 for c in cidades)
    assert [c["n"] for c in cidades] == sorted((c["n"] for c in cidades), reverse=True)
