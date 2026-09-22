"""_resolver_data/_resolver_hora_do_periodo/_formatar_data_extenso sao
100% codigo, sem LLM -- achado no Telegram real: o agente alucinou
"Quinta-feira é dia 19!" quando a proxima quinta era dia 24, e depois
"quarta semana do ano" quando a semana ISO real era 39. Calendario nunca
mais e conta do LLM."""

from datetime import date

from app.core.turno import (
    _contexto_data_mencionada,
    _formatar_data_extenso,
    _formatar_data_falada,
    _resolver_data,
    _resolver_hora_do_periodo,
)

SABADO_REFERENCIA = date(2026, 9, 19)  # confirmado: sabado


def test_dia_da_semana_resolve_para_proxima_ocorrencia_real():
    # quinta-feira seguinte a um sabado -- bug real reportado: usuario
    # esperava 24/09, agente alucinou 19/09 (que nem e quinta).
    assert _resolver_data("quinta que vem, a noite", SABADO_REFERENCIA) == date(2026, 9, 24)


def test_amanha_resolve_certo():
    assert _resolver_data("amanha", SABADO_REFERENCIA) == date(2026, 9, 20)


def test_dia_explicito_no_mes_atual():
    assert _resolver_data("dia 22", SABADO_REFERENCIA) == date(2026, 9, 22)


def test_dia_explicito_ja_passado_vira_mes_seguinte():
    assert _resolver_data("dia 1", SABADO_REFERENCIA) == date(2026, 10, 1)


def test_sem_marcador_de_dia_nao_inventa_data():
    assert _resolver_data("de manha", SABADO_REFERENCIA) is None
    assert _resolver_data("qualquer hora serve", SABADO_REFERENCIA) is None


def test_hora_explicita_tem_prioridade_sobre_periodo():
    assert _resolver_hora_do_periodo("sexta as 16h") == 16


def test_periodo_sem_hora_usa_horario_tipico():
    assert _resolver_hora_do_periodo("quinta a noite") == 19


def test_formato_pedido_pelo_usuario():
    assert _formatar_data_extenso(date(2026, 11, 21)) == "21/11/2026 (Sábado)"


def test_formato_falado_inclui_ano():
    # achado no Telegram real: perguntado "que dia e hoje?", o LLM
    # parafraseou o dia certo mas derrubou o ano -- dar a frase ja pronta
    # em portugues natural (com ano) reduz a chance disso acontecer de novo.
    assert _formatar_data_falada(date(2026, 9, 19)) == "sábado, 19 de setembro de 2026"


def test_pergunta_sobre_outro_dia_injeta_contexto():
    # achado no Telegram real: pergunta sobre um dia diferente do ja
    # reservado ("que dia e terca-feira?", com a visita marcada pra
    # quarta) so recebia a data da visita de volta, sem responder a
    # pergunta -- porque nada calculava a data do dia perguntado.
    contexto = _contexto_data_mencionada("que dia e terca-feira?")
    assert contexto != ""
    assert "cliente mencionou" in contexto


def test_mensagem_sem_dia_nao_injeta_nada():
    assert _contexto_data_mencionada("obrigado, ate mais!") == ""
