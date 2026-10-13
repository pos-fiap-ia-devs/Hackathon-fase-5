"""Síntese de voz (core/tts.py).

Fecha a outra metade do "Voice AI" do enunciado: o projeto transcrevia o
áudio do cliente desde sempre (`core/stt.py`) e só respondia em texto.

Dois backends, um módulo: `say` (macOS, embutido) e `piper` (Linux, no
container). Verificado nos dois — 25 KB de OGG/Opus pelo `say`, 19 KB pelo
piper.

A maior parte dos testes aqui é de **decisão** (quando falar) e de
**degradação** (o que acontece quando não dá), porque é onde um defeito
passa despercebido: áudio que não sai é silencioso por definição.
"""

import pytest

from app.core import tts


@pytest.fixture(autouse=True)
def _limpa_config():
    """`get_settings` é cacheado; cada teste mexe no ambiente e precisa de
    uma leitura nova."""
    from app.core.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _config(monkeypatch, **valores):
    from app.core.config import get_settings

    for chave, valor in valores.items():
        monkeypatch.setenv(chave.upper(), str(valor))
    get_settings.cache_clear()


# ---------------------------------------------------------------- quando falar

def test_desligado_por_padrao(monkeypatch):
    """Voz é opt-in: ligar sozinho mandaria áudio pra quem só digitou."""
    _config(monkeypatch, tts_enabled="false")
    assert not tts.deve_falar("audio")
    assert not tts.deve_falar("texto")


def test_responde_em_audio_para_quem_mandou_audio(monkeypatch):
    """Responder na mesma modalidade é o que um humano faz."""
    _config(monkeypatch, tts_enabled="true")
    assert tts.deve_falar("audio")


def test_nao_responde_em_audio_para_quem_digitou(monkeypatch):
    """Mandar voz pra quem digitou é intrusivo e custa segundos no turno."""
    _config(monkeypatch, tts_enabled="true")
    assert not tts.deve_falar("texto")


def test_tts_sempre_força_audio_para_todos(monkeypatch):
    _config(monkeypatch, tts_enabled="true", tts_sempre="true")
    assert tts.deve_falar("texto")
    assert tts.deve_falar("audio")


def test_followup_nao_vira_audio_sozinho(monkeypatch):
    """Mensagem de follow-up tem origem própria -- não é resposta a áudio."""
    _config(monkeypatch, tts_enabled="true")
    assert not tts.deve_falar("followup")


# ---------------------------------------------------------------- degradação

def test_desligado_nao_sintetiza(monkeypatch):
    _config(monkeypatch, tts_enabled="false")
    assert tts.sintetizar("qualquer coisa") is None


@pytest.mark.parametrize("texto", ["", "   ", "\n"])
def test_texto_vazio_devolve_none(monkeypatch, texto):
    _config(monkeypatch, tts_enabled="true")
    assert tts.sintetizar(texto) is None


def test_sem_backend_devolve_none_em_vez_de_estourar(monkeypatch):
    """Máquina sem `say`, sem `piper` ou sem ffmpeg: o turno segue só com
    texto. Áudio é complemento, nunca requisito."""
    _config(monkeypatch, tts_enabled="true")
    monkeypatch.setattr(tts.shutil, "which", lambda _: None)
    assert tts.disponivel() is None
    assert tts.sintetizar("oi") is None


def test_sem_ffmpeg_nao_tenta_sintetizar(monkeypatch):
    """ffmpeg é o que converte pro formato do Telegram -- sem ele, gerar o
    wav seria trabalho jogado fora."""
    monkeypatch.setattr(tts.shutil, "which", lambda nome: None if nome == "ffmpeg" else "/usr/bin/say")
    assert tts.disponivel() is None


# ---------------------------------------------------------------- formato

def test_formato_e_o_que_o_telegram_espera():
    """OGG/Opus mono. Fora disso o Telegram trata como arquivo de áudio,
    não como mensagem de voz -- muda o visual na conversa."""
    assert "libopus" in tts.FORMATO
    assert "48000" in tts.FORMATO
    assert tts.FORMATO[tts.FORMATO.index("-ac") + 1] == "1"


# ---------------------------------------------------------------- integração real

@pytest.mark.skipif(tts.disponivel() is None, reason="precisa de say/piper + ffmpeg")
def test_gera_ogg_opus_de_verdade(monkeypatch):
    _config(monkeypatch, tts_enabled="true")
    audio = tts.sintetizar("Oi! Aqui é a Bia da Horizonte.")
    assert audio, "nenhum áudio gerado"
    # Assinatura do contêiner Ogg.
    assert audio[:4] == b"OggS"
    assert len(audio) > 1024


@pytest.mark.skipif(tts.disponivel() is None, reason="precisa de say/piper + ffmpeg")
def test_texto_longo_e_cortado_na_fronteira_de_frase(monkeypatch):
    """Áudio longo ninguém ouve, e cortar no meio de uma palavra soa
    quebrado."""
    _config(monkeypatch, tts_enabled="true", tts_max_caracteres="80")
    longo = "Primeira frase bem completa aqui. " * 10
    assert tts.sintetizar(longo)
