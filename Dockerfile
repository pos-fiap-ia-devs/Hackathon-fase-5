# Imagem do app. O Postgres vem do compose; o OLLAMA FICA FORA, nativo no
# host -- é decisão de arquitetura, não omissão: dentro de um container no
# macOS não há Metal, e a inferência cai pra velocidade de CPU. O container
# alcança o host por `host.docker.internal` (configurado no compose).
#
# STT e TTS sao assimetricos aqui: a transcricao (`stt`) fica de fora, a
# sintese (TTS) entra. Motivo: `mlx-whisper` e Apple Silicon e nao instala
# em Linux; o piper instala e roda em qualquer lugar.
#
# O que NAO entra aqui: o extra `stt`. `mlx-whisper` depende do MLX, que é
# Apple Silicon + Metal e não instala em Linux. Transcrição de áudio
# continua existindo rodando nativo (run.sh); no container ela fica
# desligada, e `app/core/stt.py` já importa o mlx de forma tardia, então o
# import do app não quebra com STT_ENABLED=false.

FROM python:3.12-slim

# uv do binário oficial: mesma ferramenta do desenvolvimento local, sem
# pagar `pip install uv` no build.
COPY --from=ghcr.io/astral-sh/uv:0.5.14 /uv /usr/local/bin/uv

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_SYSTEM_PYTHON=1 \
    UV_COMPILE_BYTECODE=1

# ffmpeg: converte o audio da sintese pro OGG/Opus que o Telegram espera
# (app/core/tts.py). curl so pra baixar o modelo de voz, logo abaixo.
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg curl \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Dependências numa camada própria, antes do código: mexer em app/ não
# invalida o cache do install, que é a parte cara do build.
COPY pyproject.toml ./
# Os `__init__.py` dos DOIS pacotes declarados em `[tool.setuptools]`:
# sem o de `scripts`, o install falha com "package directory 'scripts'
# does not exist". O resto do codigo vem depois, pra nao invalidar o cache
# desta camada a cada edicao.
COPY app/__init__.py app/__init__.py
COPY scripts/__init__.py scripts/__init__.py
RUN uv pip install --system -e .

# Sintese de voz no Linux: o `say` do macOS nao existe aqui, entao o
# backend e o piper -- rede neural local, sem API paga, coerente com o
# resto do projeto. Modelo baixado NO BUILD: em tempo de execucao seria
# mais uma dependencia de rede, e ja aprendemos como isso termina (o
# loremflickr virou 401 e a galeria sumiu de todos os canais).
RUN uv pip install --system piper-tts==1.8.0 \
 && mkdir -p /opt/piper \
 && curl -fsSL -o /opt/piper/pt_BR-faber-medium.onnx \
      "https://huggingface.co/rhasspy/piper-voices/resolve/main/pt/pt_BR/faber/medium/pt_BR-faber-medium.onnx" \
 && curl -fsSL -o /opt/piper/pt_BR-faber-medium.onnx.json \
      "https://huggingface.co/rhasspy/piper-voices/resolve/main/pt/pt_BR/faber/medium/pt_BR-faber-medium.onnx.json"

COPY app/ app/
COPY scripts/ scripts/

# Sem shell no entrypoint: schema+seed (idempotentes, ver
# scripts/preparar_banco.py) e então o servidor, como PID 1, pra que o
# SIGTERM do `docker stop` chegue no uvicorn e o lifespan derrube o bot do
# Telegram direito.
COPY docker-entrypoint.sh /usr/local/bin/
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

EXPOSE 8000

# Não roda como root: se alguém escapar do processo, cai num usuário sem
# privilégio. `--no-create-home` porque nada aqui escreve em $HOME.
RUN useradd --system --no-create-home sdr && chown -R sdr:sdr /app
USER sdr

HEALTHCHECK --interval=10s --timeout=3s --start-period=20s --retries=5 \
    CMD python -c "import httpx,sys; sys.exit(0 if httpx.get('http://localhost:8000/health',timeout=2).status_code==200 else 1)"

ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["uvicorn", "app.web.api:app", "--host", "0.0.0.0", "--port", "8000"]
