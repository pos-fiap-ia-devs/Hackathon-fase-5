"""app.core.stt -- so os caminhos de codigo (guards, timeout), sem chamar
mlx_whisper de verdade (pesado, precisa do modelo baixado). Cobertura real
contra audio de fala verificada manualmente (Etapa 8 do PLANO.md)."""

import time
from unittest.mock import patch

from app.core import stt
from app.core.config import Settings


def _settings(**overrides):
    base = dict(stt_enabled=True, stt_max_audio_s=60, stt_timeout_s=1)
    base.update(overrides)
    return Settings(**base)


@patch("app.core.stt.get_settings")
def test_desligado_nao_chama_nada(mock_settings):
    mock_settings.return_value = _settings(stt_enabled=False)
    assert stt.transcrever("qualquer.ogg") is None


@patch("app.core.stt._liberar_llm")
@patch("app.core.stt._duracao_s")
@patch("app.core.stt._converter_para_wav")
@patch("app.core.stt.get_settings")
def test_audio_longo_demais_nao_transcreve(mock_settings, mock_converter, mock_duracao, mock_liberar):
    mock_settings.return_value = _settings(stt_max_audio_s=10)
    mock_converter.return_value = "audio.wav"
    mock_duracao.return_value = 999

    assert stt.transcrever("audio.ogg") is None
    mock_liberar.assert_not_called()


@patch("app.core.stt._converter_para_wav")
@patch("app.core.stt.get_settings")
def test_falha_na_conversao_devolve_none(mock_settings, mock_converter):
    mock_settings.return_value = _settings()
    mock_converter.side_effect = RuntimeError("ffmpeg explodiu")

    assert stt.transcrever("audio.ogg") is None


@patch("app.core.stt._transcrever_bloqueante")
@patch("app.core.stt._liberar_llm")
@patch("app.core.stt._duracao_s")
@patch("app.core.stt._converter_para_wav")
@patch("app.core.stt.get_settings")
def test_transcricao_travada_estoura_timeout(mock_settings, mock_converter, mock_duracao, mock_liberar, mock_transcrever):
    mock_settings.return_value = _settings(stt_timeout_s=1)
    mock_converter.return_value = "audio.wav"
    mock_duracao.return_value = 5
    mock_transcrever.side_effect = lambda _: time.sleep(5)

    assert stt.transcrever("audio.ogg") is None


@patch("app.core.stt._transcrever_bloqueante")
@patch("app.core.stt._liberar_llm")
@patch("app.core.stt._duracao_s")
@patch("app.core.stt._converter_para_wav")
@patch("app.core.stt.get_settings")
def test_transcricao_ok_devolve_texto(mock_settings, mock_converter, mock_duracao, mock_liberar, mock_transcrever):
    mock_settings.return_value = _settings()
    mock_converter.return_value = "audio.wav"
    mock_duracao.return_value = 5
    mock_transcrever.return_value = "estou procurando apartamento na zona sul"

    assert stt.transcrever("audio.ogg") == "estou procurando apartamento na zona sul"
    mock_liberar.assert_called_once()
