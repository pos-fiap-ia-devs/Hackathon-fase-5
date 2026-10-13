"""Configuracao central. Le do .env, valores default seguros para dev local."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = "postgresql://sdr:sdr@localhost:5433/sdr"

    # Modo demonstracao: substitui o Ollama por um provedor de regras
    # (core/llm_demo.py). Serve pra rodar o projeto inteiro em qualquer
    # maquina, sem GPU e sem baixar modelo -- avaliacao, CI, demo em
    # Windows/Linux. Respostas muito piores de proposito: demonstra FLUXO,
    # nao qualidade de redacao. Os testes de integracao continuam exigindo
    # o Ollama real (ver core/llm_demo.py sobre por que nao se mocka aqui).
    llm_demo: bool = False

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

    # ---- sintese de voz (a outra metade do Voice AI) -------------------
    # Backend escolhido pelo que existe na maquina (core/tts.py): `say` no
    # macOS, `piper` no Linux/container. Zero API paga -- nenhum audio do
    # cliente sai da maquina, igual ao resto.
    tts_enabled: bool = False
    # Voz do `say`. `say -v '?'` lista as instaladas.
    tts_voz: str = "Luciana"
    # Modelo do piper (so no Linux). O Dockerfile baixa pra ca.
    tts_modelo_piper: str = "/opt/piper/pt_BR-faber-medium.onnx"
    # Audio longo ninguem ouve; corta na fronteira de frase.
    tts_max_caracteres: int = 600
    tts_timeout_s: int = 30
    # false = responde em audio so pra quem mandou audio (o padrao, e o que
    # um humano faz). true = sempre, inclusive pra quem digitou.
    tts_sempre: bool = False

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

    # ---- login do dashboard -------------------------------------------
    # 12h: turno de trabalho do corretor. Mais que isso e cookie esquecido
    # em maquina compartilhada; menos, e relogin no meio do expediente.
    sessao_horas: int = 12
    # Prazo do link de convite. Longo o bastante pra entregar por fora
    # (nao ha SMTP nesta POC), curto o bastante pra nao virar porta aberta.
    convite_horas: int = 72
    # True em producao (HTTPS). Em dev o app roda em http://localhost, e
    # com `secure` o navegador simplesmente nao devolve o cookie -- o login
    # "funciona" e a proxima pagina volta pro /login, sem erro nenhum.
    cookie_secure: bool = False

    # ---- observabilidade ----------------------------------------------
    # A inferencia e local: o custo REAL deste projeto e zero, e o painel
    # diz isso. Estes precos servem pra calcular o contrafactual -- quanto
    # a MESMA conversa teria custado numa API paga -- usando os tokens que
    # `agent_traces` ja mede de verdade. Sao valor de REFERENCIA, nao
    # cotacao: ajuste no .env pro preco do provedor que voce comparar.
    preco_ref_usd_por_mi_entrada: float = 0.60
    preco_ref_usd_por_mi_saida: float = 2.40
    # Idem -- cotacao fixa de referencia, nao ha chamada a cambio aqui.
    cotacao_usd_brl: float = 5.40
    # Janela padrao dos indicadores da aba de observabilidade.
    traces_janela_horas: int = 24

    # ---- widget embutivel ---------------------------------------------
    # Dominios autorizados a embutir /embed num iframe, separados por
    # virgula. Vira `frame-ancestors` no CSP: o NAVEGADOR recusa renderizar
    # em site fora da lista -- e defesa que nao depende do nosso codigo.
    # Vazio = qualquer site pode embutir (default de POC, pra demo rodar
    # sem configuracao). Em producao, liste os dominios.
    widget_dominios: str = ""

    @property
    def widget_frame_ancestors(self) -> str:
        dominios = [d.strip() for d in self.widget_dominios.split(",") if d.strip()]
        return " ".join(dominios) if dominios else "*"

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
