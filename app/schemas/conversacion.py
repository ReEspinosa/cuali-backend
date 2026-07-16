from datetime import datetime

from pydantic import BaseModel


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
    pagina: int | None = None


class AdjuntoIn(BaseModel):
    url: str
    filename: str
    tipo: str
    texto_extraido: str | None = None


class ChatMensajeCreate(BaseModel):
    content: str
    adjuntos: list[AdjuntoIn] = []


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