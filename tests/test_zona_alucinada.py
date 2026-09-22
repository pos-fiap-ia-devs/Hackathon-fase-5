"""_sanitizar_zona_alucinada -- tic recorrente do modelo (mencionar uma
zona que o cliente nao disse), sobreviveu as 2 tentativas de retry em
`chat_text` em 3 variantes de frase reais, achadas ao longo da sessao."""

from app.core.llm import _sanitizar_zona_alucinada


def test_remove_abertura_curta():
    texto = "Zona sul, ótimo. 🏠 E quantos quartos você precisa?"
    assert _sanitizar_zona_alucinada(texto) == "🏠 E quantos quartos você precisa?"


def test_remove_frase_com_bia():
    texto = "Zona Sul não é o foco agora, Bia! Vamos direto ao ponto: você prefere 1 ou 2 quartos?"
    resultado = _sanitizar_zona_alucinada(texto)
    assert resultado == "Vamos direto ao ponto: você prefere 1 ou 2 quartos?"


def test_remove_meta_comentario():
    texto = (
        "Zona sul não é necessário mencionar aqui, pois o cliente ainda não citou região. "
        "Ótima faixa de preço! Quantos quartos você precisa?"
    )
    resultado = _sanitizar_zona_alucinada(texto)
    assert resultado == "Ótima faixa de preço! Quantos quartos você precisa?"


def test_nao_mexe_quando_regiao_e_real():
    texto = "Zona Sul, ótimo! Qual a faixa de preço?"
    assert _sanitizar_zona_alucinada(texto, slots={"regiao": "zona sul"}) == texto


def test_texto_limpo_fica_intacto():
    texto = "Perfeito! Qual valor você tem em mente?"
    assert _sanitizar_zona_alucinada(texto) == texto


def test_nunca_devolve_vazio():
    texto = "Zona sul."
    assert _sanitizar_zona_alucinada(texto) == texto
