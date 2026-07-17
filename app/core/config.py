import logging

from pydantic_settings import BaseSettings

logger = logging.getLogger("cuali.config")

_DEFAULT_SECRET = "CAMBIA_ESTO_EN_PRODUCCION_POR_ALGO_ALEATORIO_LARGO"


class Settings(BaseSettings):
    """
    Configuración central. En producción, todo esto viene de variables de
    entorno (.env) — nunca hardcodear el SECRET_KEY real en el repo.
    """

    # --- Base de datos ---
    database_url: str = "sqlite:///./cuali.db"

    # --- Auth / JWT ---
    secret_key: str = _DEFAULT_SECRET
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 60 * 24  # 24 horas

    # --- Correo (SMTP) ---
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 465
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""

    # --- LLM / RAG ---
    # Servidor OpenAI-compatible autohospedado (LM Studio, UNAM).
    # El proxy delante de LM Studio pide HTTP Basic Auth (llm_user/llm_password);
    # openai_api_key normalmente se deja vacío o con un valor dummy porque
    # LM Studio no valida ese header, pero el cliente OpenAI lo exige como
    # parámetro (no puede ir None).
    llm_base_url: str = "https://dinamica1.fciencias.unam.mx/lmstudio/v1/"
    llm_model: str = "openai/gpt-oss-20b"
    llm_user: str = ""
    llm_password: str = ""

    # Alias por compatibilidad con el .env actual (OPENAI_BASE_URL / OPENAI_MODEL)
    openai_base_url: str = "https://dinamica1.fciencias.unam.mx/lmstudio/v1/"
    openai_api_key: str = "not-needed"
    openai_model: str = "openai/gpt-oss-20b"

    # --- RAG ---
    chroma_persist_dir: str = "/app/data/vectordb"

    # =========================================================================
    # Seguridad: rate limiting y concurrencia
    # =========================================================================

    # Apagado de emergencia (RATE_LIMIT_ENABLED=false en .env para debug local)
    rate_limit_enabled: bool = True

    # --- Límite global por IP (middleware en main.py) ---
    # Techo generoso: un maestro navegando normal no se acerca; un bot en loop sí.
    global_ip_max_per_minute: int = 120

    # --- Concurrencia hacia el LLM (semáforo en llm_client.py) ---
    # gpt-oss-20b en LM Studio procesa muy pocas peticiones en paralelo.
    # 2 simultáneas + cola con timeout evita que se acumulen y todo haga timeout.
    llm_max_concurrent: int = 2
    llm_queue_timeout_seconds: int = 30

    # --- Cuota de generaciones LLM por usuario (llm_quota.py) ---
    llm_user_max_per_hour: int = 40

    # --- Límites de auth (routers/auth.py) ---
    register_max_per_hour_ip: int = 5        # cuentas nuevas por IP por hora
    login_max_per_5min_ip: int = 10          # intentos de login por IP
    login_max_per_15min_email: int = 15      # intentos de login por correo
    verify_max_attempts_per_15min: int = 5   # intentos de código por correo
    resend_cooldown_seconds: int = 60        # espera mínima entre reenvíos
    email_send_max_per_hour: int = 4         # correos (verificación/reset) por destinatario

    # --- Tamaño máximo de body (middleware en main.py) ---
    max_body_bytes: int = 2 * 1024 * 1024          # 2 MB para JSON normal
    max_upload_bytes: int = 25 * 1024 * 1024       # 25 MB para /archivos (uploads)

    class Config:
        env_file = ".env"


settings = Settings()

if settings.secret_key == _DEFAULT_SECRET:
    # No reventamos el arranque (para no romper dev local), pero queda gritado
    # en los logs: con el secret por defecto, cualquiera que lea el repo puede
    # firmar sus propios JWT y entrar como cualquier usuario.
    logger.warning(
        "SECRET_KEY sigue siendo el valor por defecto del repo. "
        "Genera uno con: python -c \"import secrets; print(secrets.token_urlsafe(64))\" "
        "y ponlo en .env antes de exponer el servidor."
    )