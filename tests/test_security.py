from app.core.security import mascarar_telefone


def test_mascara_ddd_e_ultimos_quatro_digitos():
    assert mascarar_telefone("(62) 99999-8888") == "62*****8888"


def test_mascara_numero_sem_formatacao():
    assert mascarar_telefone("62999998888") == "62*****8888"


def test_numero_curto_vira_so_asteriscos():
    assert mascarar_telefone("123") == "****"


def test_nunca_expoe_o_numero_cru():
    original = "62988887777"
    mascarado = mascarar_telefone(original)
    assert original not in mascarado
    assert "9888" not in mascarado  # miolo do numero nao pode sobrar em nenhum pedaço
