from datetime import datetime

from pydantic import BaseModel, Field


class PlaneacionCreate(BaseModel):
    grado: int
    campo_formativo: str
    contenido: str
    pda: str
    grupo: str
    sesiones: int
    tema: str


class PlaneacionOut(BaseModel):
    id: str
    grado: int
    campo_formativo: str
    contenido: str
    grupo: str
    sesiones: int
    tema: str
    status: str
    pdf_path: str | None
    docx_path: str | None
    created_at: datetime

    class Config:
        from_attributes = True


class AdjuntoIn(BaseModel):
    url: str = Field(max_length=500)
    filename: str = Field(max_length=255)
    tipo: str = Field(max_length=50)
    # Antes este campo no existia aqui y Pydantic lo descartaba en silencio:
    # el texto de los PDF/Word adjuntos nunca llegaba al LLM en el chat de
    # planeacion. Mismo tope que en el chat general.
    texto_extraido: str | None = Field(default=None, max_length=60_000)


class MensajeCreate(BaseModel):
    content: str = Field(min_length=1, max_length=8_000)
    adjuntos: list[AdjuntoIn] = Field(default=[], max_length=5)


class MensajeOut(BaseModel):
    id: str
    role: str
    content: str
    adjuntos: list[AdjuntoIn] = []
    created_at: datetime

    class Config:
        from_attributes = True


class PlaneacionDetalle(PlaneacionOut):
    mensajes: list[MensajeOut] = []


class GenerarPlaneacionOut(BaseModel):
    docx_url: str