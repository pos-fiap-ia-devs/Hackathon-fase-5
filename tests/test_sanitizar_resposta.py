"""_sanitizar_resposta e 100% codigo, sem LLM -- achado real: "Bia aqui!"
sobreviveu 2 tentativas de retry em 3/3 testes de follow-up, mesmo com a
regra explicita "nunca escreva Bia na resposta" em REGRAS."""

from app.core.llm import _sanitizar_resposta


def test_remove_abertura_bia_aqui():
    assert _sanitizar_resposta("Bia aqui! Lembrei que você estava sem pressa.") == "Lembrei que você estava sem pressa."


def test_remove_abertura_bia_virgula():
    assert _sanitizar_resposta("Bia, tudo bem? Vamos continuar.") == "Tudo bem? Vamos continuar."


def test_nao_mexe_em_bia_no_meio_da_frase():
    texto = "Perfeito, Bia. Aqui está a resposta."
    assert _sanitizar_resposta(texto) == texto


def test_texto_limpo_fica_intacto():
    texto = "Zona Sul, ótimo! Qual a faixa de preço?"
    assert _sanitizar_resposta(texto) == texto


def test_nunca_devolve_string_vazia():
    assert _sanitizar_resposta("Bia aqui!") == "Bia aqui!"
