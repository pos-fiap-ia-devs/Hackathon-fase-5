"""_parece_pergunta_sobre_data e _combinar_horario sao 100% codigo, sem
LLM -- achado no Telegram real: "e amanhã, que dia é?" (pergunta) estava
sendo lido como preferencia de horario e corrompendo a visita ja marcada,
por causa de um bug duplo: (1) o extrator confundia pergunta com
preferencia, e (2) "manha" (periodo) e substring de "amanha" (dia),
fazendo o merge achar que tinha dia+periodo juntos e substituir tudo."""

from app.core.turno import _combinar_horario, _parece_pergunta_sobre_data


def test_pergunta_com_amanha_e_reconhecida_como_pergunta():
    assert _parece_pergunta_sobre_data("e amanha, que dia e?") is True


def test_preferencia_com_amanha_nao_e_confundida_com_pergunta():
    assert _parece_pergunta_sobre_data("pode ser amanha") is False
    assert _parece_pergunta_sobre_data("pode ser amanha as 16h") is False


def test_pergunta_sobre_a_reserva_e_reconhecida():
    assert _parece_pergunta_sobre_data("qual dia ficou a reserva? me mostre") is True


def test_amanha_sozinho_mescla_em_vez_de_substituir():
    # bug real: "manha" (periodo) e substring de "amanha" (dia) -- sem
    # \b no regex, isso contava como "dia + periodo juntos" e substituia
    # "tarde" inteiro em vez de so complementar.
    assert _combinar_horario("tarde", "amanha") == "tarde, amanha"
