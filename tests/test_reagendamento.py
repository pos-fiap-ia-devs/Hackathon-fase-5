"""_combinar_horario e 100% codigo, sem LLM -- decide mesclar ou substituir
a preferencia de horario da visita (achado no Telegram real: pedir pro LLM
fazer as duas coisas de uma vez -- julgar E escrever a frase combinada --
falhava exatamente no caso de substituicao total)."""

from app.core.turno import _combinar_horario


def test_sem_preferencia_anterior_usa_a_nova():
    assert _combinar_horario(None, "tarde") == "tarde"


def test_fragmento_so_dia_mescla_com_periodo_anterior():
    assert _combinar_horario("tarde", "dia 22") == "tarde, dia 22"


def test_fragmento_so_hora_mescla_com_dia_anterior():
    assert _combinar_horario("dia 22", "horario 16") == "dia 22, horario 16"


def test_dia_e_periodo_juntos_substitui_tudo():
    assert _combinar_horario("tarde", "sexta que vem, de manha") == "sexta que vem, de manha"


def test_dia_e_hora_juntos_substitui_tudo():
    assert _combinar_horario("dia 22 as 16h", "sabado as 10h") == "sabado as 10h"
