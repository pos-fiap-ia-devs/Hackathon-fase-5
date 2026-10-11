"""`_chat_id_telegram` -- guarda do `int()` no scanner de follow-up.

Achado em produção, duas vezes: o `run.log` do projeto tem 71 ocorrências
de `ValueError: invalid literal for int() with base 10: 'adversarial_...'`,
e o mesmo erro voltou a aparecer de 30 em 30 segundos dentro do container.

Causa: lead gravado com `canal='telegram'` e `chat_id` não-numérico (as
fixtures de `test_adversarial.py` fazem isso). A guarda de canal que já
existia não pega esse caso, e sem error handler no ptb a exceção subia a
cada varredura -- impedindo que os follow-ups legítimos da MESMA rodada
fossem enviados.
"""

from app.channels.telegram import _chat_id_telegram


def test_chat_id_normal_vira_inteiro():
    assert _chat_id_telegram("tg:123456789") == 123456789


def test_negativo_e_valido():
    """Grupo e supergrupo no Telegram têm id negativo."""
    assert _chat_id_telegram("tg:-1001234567890") == -1001234567890


def test_sem_prefixo_ainda_funciona():
    assert _chat_id_telegram("123456") == 123456


def test_chat_id_de_fixture_nao_estoura():
    """O caso exato do run.log."""
    assert _chat_id_telegram("tg:adversarial_df0568b1") is None


def test_outros_canais_nao_estouram():
    assert _chat_id_telegram("web:abc123") is None
    assert _chat_id_telegram("cli:local") is None


def test_vazio_e_none_nao_estouram():
    assert _chat_id_telegram("") is None
    assert _chat_id_telegram(None) is None


def test_nunca_levanta_excecao():
    """É o contrato inteiro desta função: ela roda dentro de um job
    periódico, e exceção ali vira log infinito."""
    for entrada in ["tg:", "tg:12.5", "tg:1e5", "tg:0x10", "???", "tg:  ", None, ""]:
        _chat_id_telegram(entrada)  # não pode levantar
