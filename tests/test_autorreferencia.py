"""_sanitizar_autorreferencia -- o agente falando de si em 3a pessoa pelo
nome. `_ABERTURA_BIA` (test_sanitizar_resposta.py) so cobre a ABERTURA;
estes sao os casos no meio da frase, achados testando as tratativas de
objecao, que sobreviveram as 2 tentativas do retry."""

from app.core.llm import _sanitizar_autorreferencia


def test_remove_autointroducao_no_meio_da_frase():
    texto = "Entendo perfeitamente, Bia aqui da Horizonte! A gente ajusta a busca ao seu orçamento."
    assert _sanitizar_autorreferencia(texto) == "Entendo perfeitamente! A gente ajusta a busca ao seu orçamento."


def test_troca_terceira_pessoa_por_primeira():
    texto = "Compreendo, Bia está aqui quando você precisar."
    assert _sanitizar_autorreferencia(texto) == "Compreendo, estou aqui quando você precisar."


def test_conjuga_verbo_de_terceira_para_primeira_pessoa():
    """Conjugacao de verdade -- um "eu" generico geraria "eu vai"."""
    assert _sanitizar_autorreferencia("A Bia vai te mandar as opções.") == "Vou te mandar as opções."
    assert _sanitizar_autorreferencia("Isso é normal, Bia deixa tudo organizado.") == (
        "Isso é normal, deixo tudo organizado."
    )


def test_permite_nome_na_apresentacao():
    """Turno 1 e "com quem estou falando?" -- dizer o nome e o correto."""
    texto = "Oi! Aqui é a Bia da Horizonte. Tudo bem?"
    assert _sanitizar_autorreferencia(texto, permite_nome=True) == texto


def test_texto_sem_autorreferencia_fica_intacto():
    texto = "Perfeito! Quantos quartos você precisa?"
    assert _sanitizar_autorreferencia(texto) == texto


def test_nunca_devolve_vazio():
    assert _sanitizar_autorreferencia("Bia aqui!") == "Bia aqui!"
