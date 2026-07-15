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

    # --- Frontend ---
    # Se usa para construir el link de restablecer contraseña que va en el correo.
    frontend_url: str = "http://localhost:5173"

    # --- Correo (SMTP) ---
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 465
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""

    # --- LLM / RAG ---
    # TODO: aquí van las credenciales reales de tu backend OpenAI-compatible
    # y la ruta a tu colección de ChromaDB, una vez que conectemos services/llm.py
    # a tu RAG real en vez del stub que trae ahora.
    openai_base_url: str = "https://api.openai.com/v1"
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"

    class Config:
        env_file = ".env"


settings = Settings()