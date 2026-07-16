from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """
    Configuración central. En producción, todo esto viene de variables de
    entorno (.env) — nunca hardcodear el SECRET_KEY real en el repo.
    """

    # --- Base de datos ---
    database_url: str = "sqlite:///./cuali.db"

    # --- Auth / JWT ---
    secret_key: str = "CAMBIA_ESTO_EN_PRODUCCION_POR_ALGO_ALEATORIO_LARGO"
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

    class Config:
        env_file = ".env"


settings = Settings()