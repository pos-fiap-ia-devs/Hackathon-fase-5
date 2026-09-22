"""Transcricao de audio -- Etapa 8, diferencial (whisper local via MLX).

Roda 100% local (`mlx_whisper`), nenhuma chamada externa -- mesma filosofia
"tudo local" do resto do projeto (secao 3 do PLANO.md). `mlx_whisper` e
extra opcional (`pip install -e .[stt]`, pyproject.toml) pra nao travar o
nucleo do projeto quando ninguem precisa de audio; por isso o import mora
DENTRO de `transcrever`, nao no topo do modulo -- assim o resto do app
sobe normal mesmo sem o extra instalado (STT_ENABLED=false e o default).

Duas coisas nao obvias, achadas testando contra hardware real (M1 16GB):

1. `OLLAMA_KEEP_ALIVE=24h` mantem qwen3.5:9b (~6.6GB) residente o tempo
   todo. Carregar o whisper-small (~1GB) por cima disso deu OOM real
   (SIGKILL, confirmado via `top -l 1 -n 0`, "15G used / 71M unused" antes
   do crash). `_liberar_llm` derruba o modelo do Ollama (`keep_alive: 0`)
   ANTES de transcrever -- custa um reload de alguns segundos na proxima
   chamada de `chat_text`, mas evita o OOM. Trade-off aceito: turno com
   audio ja e mais lento (conversao + transcricao), o reload some no ruido.
2. Download do modelo via HF Hub (dentro de `mlx_whisper.transcribe`)
   travou indefinidamente nesta rede -- 0 bytes por minutos, sem erro nem
   timeout natural (HTTP/2 preso). `curl --http1.1 -C -` no mesmo arquivo
   funcionou de primeira (`scripts/baixar_modelo_stt.sh`). Por isso a
   chamada roda dentro de um timeout duro (`STT_TIMEOUT_S`) em thread
   separada -- se travar de novo, falha rapido e cai no fallback de texto,
   em vez de travar o turno (e o processo do bot) pra sempre.
"""

import logging
import subprocess
import wave
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturaTimeoutError
from pathlib import Path

import httpx

from app.core.config import get_settings

logger = logging.getLogger(__name__)


def _liberar_llm() -> None:
    """Forca o Ollama a descarregar o modelo de chat antes da transcricao
    (ver nota 1 do modulo). Best-effort -- se falhar, segue tentando
    transcrever mesmo assim; o pior caso e o OOM que ja conhecemos, nao um
    erro novo."""
    settings = get_settings()
    try:
        httpx.post(
            f"{settings.ollama_host}/api/generate",
            json={"model": settings.ollama_model, "keep_alive": 0},
            timeout=10,
        )
    except Exception:
        logger.warning("falha ao liberar RAM do Ollama antes da transcricao", exc_info=True)


def _duracao_s(caminho_wav: str) -> float:
    with wave.open(caminho_wav, "rb") as w:
        return w.getnframes() / w.getframerate()


def _converter_para_wav(caminho_entrada: str) -> str:
    """Telegram manda voice em OGG/Opus -- converte pra WAV 16kHz mono, tanto
    pra medir duracao (guard STT_MAX_AUDIO_S) quanto porque e o formato que
    o whisper espera."""
    caminho_wav = str(Path(caminho_entrada).with_suffix(".wav"))
    subprocess.run(
        ["ffmpeg", "-y", "-i", caminho_entrada, "-ar", "16000", "-ac", "1", caminho_wav],
        check=True, capture_output=True,
    )
    return caminho_wav


def _transcrever_bloqueante(caminho_wav: str) -> str:
    # Import tardio: `mlx-whisper` e extra opcional -- ver docstring do modulo.
    import mlx_whisper  # noqa: PLC0415

    settings = get_settings()
    modelo = settings.stt_model_path or settings.stt_model
    resultado = mlx_whisper.transcribe(caminho_wav, path_or_hf_repo=modelo, language="pt")
    return resultado["text"].strip()


def transcrever(caminho_audio: str) -> str | None:
    """Ponto de entrada do canal. Recebe caminho de arquivo local ja salvo
    em disco (o adapter de canal cuida do download). `None` se STT
    desligado, audio longo demais, ou qualquer falha -- quem chama trata
    como "nao deu pra transcrever" e cai no fallback pedindo texto."""
    settings = get_settings()
    if not settings.stt_enabled:
        return None

    try:
        caminho_wav = _converter_para_wav(caminho_audio)
    except Exception:
        logger.exception("falha convertendo audio pra WAV")
        return None

    try:
        if _duracao_s(caminho_wav) > settings.stt_max_audio_s:
            logger.warning("audio maior que STT_MAX_AUDIO_S (%ss) -- ignorado", settings.stt_max_audio_s)
            return None
    except Exception:
        logger.exception("falha lendo duracao do audio")
        return None

    _liberar_llm()

    # Nao usa `with` aqui de proposito: o context manager chama
    # `shutdown(wait=True)` na saida, que travaria ate a thread terminar --
    # anulando o timeout de baixo (ver nota 2 do modulo: e exatamente o
    # travamento indefinido que isso precisa cortar). `shutdown(wait=False)`
    # deixa a thread travada morrer sozinha em segundo plano se precisar.
    executor = ThreadPoolExecutor(max_workers=1)
    try:
        texto = executor.submit(_transcrever_bloqueante, caminho_wav).result(timeout=settings.stt_timeout_s)
    except FuturaTimeoutError:
        logger.error("transcricao excedeu STT_TIMEOUT_S (%ss)", settings.stt_timeout_s)
        return None
    except Exception:
        logger.exception("falha na transcricao")
        return None
    finally:
        executor.shutdown(wait=False)

    return texto or None
