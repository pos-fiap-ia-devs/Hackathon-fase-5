"""Resposta em voz no Telegram: substitui o texto, não repete.

Achado em uso real: com `TTS_ENABLED=true`, quem mandava áudio recebia a
resposta escrita e, logo depois, a mesma frase falada. Não é como uma
pessoa responde. Agora a voz vai NO LUGAR do texto; o texto só vai quando
a voz falha, pra o cliente nunca ficar sem resposta.

Também aqui: o que a voz FALA. Medido por ida e volta (voz -> transcrição),
o `say` lia o emoji em voz alta ("...você precisa casa com jardim?").
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.channels import telegram as tg
from app.core import tts
from app.core.turno import RespostaTurno


def _update(*, texto: str | None = None, voz: bool = False):
    mensagem = SimpleNamespace(
        text=texto,
        voice=object() if voz else None,
        audio=None,
        reply_text=AsyncMock(),
    )
    return SimpleNamespace(message=mensagem, effective_chat=SimpleNamespace(id=123))


def _contexto():
    bot = SimpleNamespace(
        send_chat_action=AsyncMock(),
        send_voice=AsyncMock(),
        send_media_group=AsyncMock(),
        send_photo=AsyncMock(),
    )
    return SimpleNamespace(bot=bot)


@pytest.fixture
def turno_fixo(monkeypatch):
    """Turno sem LLM nem banco: o que se testa aqui é o envio."""
    resposta = RespostaTurno("Perfeito, anotei! Quantos quartos você precisa? 🏠")

    async def _processar(_chat, _texto, _origem="texto"):
        return resposta

    async def _transcrever(_update, _context):
        return "quero um apartamento"

    async def _digitando(_context, _chat):
        await asyncio.sleep(3600)

    monkeypatch.setattr(tg, "_processar_em_thread", _processar)
    monkeypatch.setattr(tg, "_transcrever_mensagem", _transcrever)
    monkeypatch.setattr(tg, "_manter_digitando", _digitando)
    monkeypatch.setattr(tg, "_enviar_galeria", AsyncMock())
    return resposta


def test_audio_recebe_so_voz_sem_texto_repetido(monkeypatch, turno_fixo):
    monkeypatch.setattr(tts, "deve_falar", lambda origem: origem == "audio")
    monkeypatch.setattr(tts, "sintetizar", lambda texto: b"OggS-falso")
    update, contexto = _update(voz=True), _contexto()

    asyncio.run(tg._on_message(update, contexto))

    contexto.bot.send_voice.assert_awaited_once()
    update.message.reply_text.assert_not_awaited()


def test_voz_falhando_cai_pro_texto(monkeypatch, turno_fixo):
    """Sem isso, uma falha de síntese deixaria o cliente sem resposta nenhuma."""
    monkeypatch.setattr(tts, "deve_falar", lambda origem: True)
    monkeypatch.setattr(tts, "sintetizar", lambda texto: None)
    update, contexto = _update(voz=True), _contexto()

    asyncio.run(tg._on_message(update, contexto))

    contexto.bot.send_voice.assert_not_awaited()
    update.message.reply_text.assert_awaited_once()


def test_quem_digitou_recebe_so_texto(monkeypatch, turno_fixo):
    monkeypatch.setattr(tts, "deve_falar", lambda origem: origem == "audio")
    monkeypatch.setattr(tts, "sintetizar", lambda texto: b"OggS-falso")
    update, contexto = _update(texto="quero um apartamento"), _contexto()

    asyncio.run(tg._on_message(update, contexto))

    contexto.bot.send_voice.assert_not_awaited()
    update.message.reply_text.assert_awaited_once()


# ---------------------------------------------------------------- o que se fala

@pytest.mark.parametrize("escrito, falado", [
    ("Quantos quartos você precisa? 🏠", "Quantos quartos você precisa?"),
    ("Visita marcada para 17/10/2026. 📅", "Visita marcada para 17/10/2026."),
    ("Oi! 👋🏽 Aqui é a Bia.", "Oi! Aqui é a Bia."),
    ("sem emoji nenhum", "sem emoji nenhum"),
])
def test_emoji_nao_e_lido_em_voz_alta(escrito, falado):
    assert tts.texto_falavel(escrito) == falado


def test_so_emoji_nao_gera_audio_vazio(monkeypatch):
    monkeypatch.setenv("TTS_ENABLED", "true")
    from app.core.config import get_settings

    get_settings.cache_clear()
    try:
        assert tts.sintetizar("🏠📅") is None
    finally:
        get_settings.cache_clear()
