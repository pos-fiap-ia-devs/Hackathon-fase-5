"""_sanitizar_vazamento_prompt -- achado real: o modelo colou o proprio
prompt interno na resposta ("Cliente: 2 quartos\\n\\nDados ja coletados do
cliente: {...}\\nUltima mensagem do cliente: ...\\nO que fazer agora: ...")
em vez de responder. Mesma filosofia de `_sanitizar_resposta`: guardrail
deterministico depois do retry, corta a partir do vazamento."""

from app.core.llm import _sanitizar_vazamento_prompt


def test_corta_vazamento_dados_coletados():
    texto = (
        "Zona Sul não é o foco agora, então vamos direto ao ponto: quantos quartos você precisa?\n\n"
        "Cliente: 2 quartos\n\n"
        "Dados ja coletados do cliente: {'intencao': 'compra'}\n"
        'Ultima mensagem do cliente: "2 quartos"\n\n'
        "O que fazer agora: Pergunte a regiao"
    )
    resultado = _sanitizar_vazamento_prompt(texto)
    assert resultado == "Zona Sul não é o foco agora, então vamos direto ao ponto: quantos quartos você precisa?"


def test_texto_limpo_fica_intacto():
    texto = "Perfeito! Qual é a faixa de preço que você tem em mente?"
    assert _sanitizar_vazamento_prompt(texto) == texto


def test_nunca_devolve_vazio_se_vazamento_desde_o_inicio():
    texto = "Dados ja coletados do cliente: {'intencao': 'compra'}"
    assert _sanitizar_vazamento_prompt(texto) == texto
