# Imagem do app. O Postgres vem do compose; o OLLAMA FICA FORA, nativo no
# host -- é decisão de arquitetura, não omissão: dentro de um container no
# macOS não há Metal, e a inferência cai pra velocidade de CPU. O container
# alcança o host por `host.docker.internal` (configurado no compose).
#
# O que NÃO entra aqui: o extra `stt`. `mlx-whisper` depende do MLX, que é
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

WORKDIR /app

# Dependências numa camada própria, antes do código: mexer em app/ não
# invalida o cache do install, que é a parte cara do build.
COPY pyproject.toml ./
COPY app/__init__.py app/__init__.py
RUN uv pip install --system -e .

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
