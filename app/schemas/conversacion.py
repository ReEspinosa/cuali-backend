from datetime import datetime

from pydantic import BaseModel, Field


class ConversacionOut(BaseModel):
    id: str
    titulo: str
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class SourceOut(BaseModel):
    documento: str
    campo: str | None = None
    pagina: str | int | None = None


class AdjuntoIn(BaseModel):
    url: str = Field(max_length=500)
    filename: str = Field(max_length=255)
    tipo: str = Field(max_length=50)
    # El texto extraído de un PDF puede ser largo, pero acotado: sin tope,
    # un cliente malicioso mete megabytes directo al prompt del LLM.
    texto_extraido: str | None = Field(default=None, max_length=60_000)


class ChatMensajeCreate(BaseModel):
    # 8,000 caracteres es muchísimo para un mensaje escrito por una maestra,
    # pero corta en seco los prompts gigantes de un bot.
    content: str = Field(min_length=1, max_length=8_000)
    adjuntos: list[AdjuntoIn] = Field(default=[], max_length=5)


class ChatMensajeOut(BaseModel):
    id: str
    role: str
    content: str
    adjuntos: list[AdjuntoIn] = []
    sources: list[SourceOut] = []
    created_at: datetime

    class Config:
        from_attributes = True


class ConversacionDetalle(ConversacionOut):
    mensajes: list[ChatMensajeOut] = []