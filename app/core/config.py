"""Configuracao central. Le do .env, valores default seguros para dev local."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = "postgresql://sdr:sdr@localhost:5433/sdr"

    ollama_host: str = "http://localhost:11434"
    ollama_model: str = "qwen3.5:9b"
    ollama_embed_model: str = "nomic-embed-text"
    ollama_num_ctx: int = 8192
    ollama_timeout_s: int = 90

    telegram_bot_token: str = ""
    web_chat_enabled: bool = True

    stt_enabled: bool = False
    stt_model: str = "mlx-community/whisper-small-mlx"
    stt_model_path: str = ""
    stt_max_audio_s: int = 60
    stt_timeout_s: int = 45

    demo_mode: bool = True
    followup_intervalos_min: str = "30,120,1440"
    followup_intervalos_demo_s: str = "90,180,300"
    followup_scan_s: int = 30
    followup_max_tentativas: int = 3

    # Fechamento por inatividade -- pedido do usuario, fixo (nao escala com
    # DEMO_MODE de proposito, diferente do follow-up acima): passado esse
    # tempo TOTAL sem resposta do cliente, encerra o atendimento com uma
    # mensagem fixa. Roda em paralelo ao follow-up (que ja tenta reengajar
    # antes disso) -- ver core/followup.py::verificar_inatividade.
    inatividade_fechamento_min: int = 10

    rate_limit_msgs_por_min: int = 20
    crm_webhook_url: str = ""

    app_host: str = "0.0.0.0"
    app_port: int = 8000
    log_level: str = "INFO"

    @property
    def followup_intervalos_s(self) -> list[int]:
        """Intervalos de follow-up em segundos, alternando por DEMO_MODE."""
        raw = self.followup_intervalos_demo_s if self.demo_mode else self._intervalos_min_em_s()
        return [int(v) for v in raw.split(",")]

    def _intervalos_min_em_s(self) -> str:
        return ",".join(str(int(v) * 60) for v in self.followup_intervalos_min.split(","))


@lru_cache
def get_settings() -> Settings:
    return Settings()
