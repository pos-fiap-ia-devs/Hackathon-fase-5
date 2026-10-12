"""Síntese de voz: a Bia responde em áudio.

Fecha a outra metade do "Voice AI" do enunciado -- o projeto já transcrevia
o áudio do cliente (`core/stt.py`), mas só respondia em texto.

100% LOCAL, como o resto
------------------------
Nenhuma API de voz paga, nenhum áudio do cliente saindo da máquina. Dois
backends, escolhidos pelo que existe na máquina:

- **`say`** (macOS): embutido no sistema, voz pt-BR nativa, zero instalação.
  É o caminho do `./run.sh`.
- **`piper`** (Linux/container): rede neural local, roda onde o `say` não
  existe. É o caminho do Docker.

Um módulo só, dois backends -- não duas implementações. Duas cópias da
mesma regra foi exatamente como `regiao_existe` e a busca divergiram e
geraram um laço no Telegram.

QUANDO FALA
-----------
Por padrão, só quando o cliente mandou áudio: responder na mesma
modalidade é o que um humano faz. Mandar voz pra quem digitou é intrusivo
e ainda custa segundos no turno. `TTS_SEMPRE=true` força o contrário.

Falha aqui NUNCA derruba o turno: o texto já foi entregue, o áudio é
complemento. Mesmo princípio de `channels/telegram.py::_enviar_galeria`.
"""

import logging
import shutil
import subprocess
import tempfile
from pathlib import Path

from app.core.config import get_settings

logger = logging.getLogger(__name__)

# Telegram quer OGG/Opus mono. Fora disso ele aceita como "arquivo de
# audio", nao como mensagem de voz -- muda o visual na conversa.
FORMATO = ("-c:a", "libopus", "-b:a", "32k", "-ar", "48000", "-ac", "1")


def disponivel() -> str | None:
    """Qual backend dá pra usar nesta máquina, ou None.

    Checagem de binário, não de sistema operacional: `platform.system()`
    mentiria num macOS sem as vozes instaladas, e acertaria por acaso num
    Linux com algo equivalente no PATH."""
    if shutil.which("ffmpeg") is None:
        return None
    if shutil.which("say"):
        return "say"
    if shutil.which("piper"):
        return "piper"
    return None


def _gerar_wav(texto: str, destino: Path, backend: str, timeout: int) -> bool:
    settings = get_settings()
    if backend == "say":
        comando = ["say", "-v", settings.tts_voz, "-o", str(destino),
                   "--data-format=LEF32@22050", texto]
    else:
        # piper lê o texto da entrada padrão e escreve o wav no arquivo.
        comando = ["piper", "--model", settings.tts_modelo_piper,
                   "--output_file", str(destino)]
    try:
        subprocess.run(
            comando,
            input=texto.encode() if backend == "piper" else None,
            capture_output=True, timeout=timeout, check=True,
        )
        return destino.exists() and destino.stat().st_size > 0
    except (subprocess.TimeoutExpired, subprocess.CalledProcessError, OSError):
        logger.warning("sintese de voz falhou (backend=%s)", backend, exc_info=True)
        return False


def sintetizar(texto: str) -> bytes | None:
    """Bytes de um OGG/Opus pronto pro Telegram, ou None.

    `None` em qualquer problema -- backend ausente, texto vazio, timeout,
    ffmpeg falhando. O chamador segue sem áudio, nunca quebra."""
    settings = get_settings()
    if not settings.tts_enabled or not texto or not texto.strip():
        return None

    backend = disponivel()
    if backend is None:
        logger.warning("TTS_ENABLED=true mas nenhum backend de voz disponivel")
        return None

    # Texto longo vira áudio longo, e áudio longo ninguém ouve. Corta na
    # fronteira de frase pra não terminar no meio de uma palavra.
    limite = settings.tts_max_caracteres
    falado = texto if len(texto) <= limite else texto[:limite].rsplit(".", 1)[0] + "."

    with tempfile.TemporaryDirectory() as tmp:
        bruto = Path(tmp) / "voz.wav"
        if not _gerar_wav(falado, bruto, backend, settings.tts_timeout_s):
            return None

        final = Path(tmp) / "voz.ogg"
        try:
            subprocess.run(
                ["ffmpeg", "-y", "-i", str(bruto), *FORMATO, str(final)],
                capture_output=True, timeout=settings.tts_timeout_s, check=True,
            )
        except (subprocess.TimeoutExpired, subprocess.CalledProcessError, OSError):
            logger.warning("conversao do audio falhou", exc_info=True)
            return None

        return final.read_bytes() if final.exists() else None


def deve_falar(origem_da_mensagem: str) -> bool:
    """Responde em áudio? Por padrão, só pra quem mandou áudio."""
    settings = get_settings()
    if not settings.tts_enabled:
        return False
    return settings.tts_sempre or origem_da_mensagem == "audio"
