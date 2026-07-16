from datetime import datetime

from pydantic import BaseModel


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
    url: str
    filename: str
    tipo: str


class MensajeCreate(BaseModel):
    content: str
    adjuntos: list[AdjuntoIn] = []


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