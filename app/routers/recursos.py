import os
import uuid

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.core.deps import get_current_user
from app.models.user import User
from app.services.llm import (
    ajustar_contenido_cuestionario,
    generar_contenido_cuestionario,
    generar_contenido_diapositivas,
)
from app.services.cuestionario_generator import generar_docx_cuestionario
from app.services.pptx_generator import TEMAS, generar_pptx_diapositivas

router = APIRouter(prefix="/recursos", tags=["recursos"])

TIPOS_PREGUNTA_VALIDOS = {"opcion_multiple", "verdadero_falso", "abierta", "mixto"}


class DiapositivasCreate(BaseModel):
    titulo: str
    descripcion: str
    tema_color: str
    num_diapositivas: int = Field(ge=3, le=15)
    texto_adjunto: str | None = None


@router.post("/diapositivas")
def crear_diapositivas(
    payload: DiapositivasCreate,
    user: User = Depends(get_current_user),
):
    if payload.tema_color not in TEMAS:
        raise HTTPException(status_code=400, detail=f"Tema de color inválido. Opciones: {', '.join(TEMAS)}")

    contenido = generar_contenido_diapositivas(
        titulo=payload.titulo,
        descripcion=payload.descripcion,
        num_diapositivas=payload.num_diapositivas,
        texto_adjunto=payload.texto_adjunto,
    )

    recurso_id = str(uuid.uuid4())
    datos = {
        "titulo": payload.titulo,
        "tema_color": payload.tema_color,
        "diapositivas": contenido["diapositivas"],
    }
    generar_pptx_diapositivas(recurso_id, datos)

    return {
        "id": recurso_id,
        "titulo": payload.titulo,
        "tema_color": payload.tema_color,
        "diapositivas": contenido["diapositivas"],
        "pptx_url": f"/recursos/diapositivas/{recurso_id}/pptx",
    }


@router.get("/diapositivas/{recurso_id}/pptx")
def descargar_pptx(
    recurso_id: str,
    user: User = Depends(get_current_user),
):
    path = os.path.join("generated_docs", f"diapositivas_{recurso_id}.pptx")
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Estas diapositivas ya no están disponibles. Genera unas nuevas.")

    return FileResponse(
        path,
        media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
        filename="diapositivas_cuali.pptx",
    )


class CuestionarioCreate(BaseModel):
    titulo: str
    descripcion: str
    tipo_preguntas: str
    num_preguntas: int = Field(ge=3, le=10)
    texto_adjunto: str | None = None


class CuestionarioAjustar(BaseModel):
    titulo: str
    tipo_preguntas: str
    num_preguntas: int = Field(ge=3, le=10)
    preguntas_actuales: list[dict]
    instrucciones: str


def _validar_tipo(tipo: str) -> None:
    if tipo not in TIPOS_PREGUNTA_VALIDOS:
        raise HTTPException(
            status_code=400,
            detail=f"Tipo de pregunta inválido. Opciones: {', '.join(TIPOS_PREGUNTA_VALIDOS)}",
        )


@router.post("/cuestionarios")
def crear_cuestionario(
    payload: CuestionarioCreate,
    user: User = Depends(get_current_user),
):
    _validar_tipo(payload.tipo_preguntas)

    contenido = generar_contenido_cuestionario(
        titulo=payload.titulo,
        descripcion=payload.descripcion,
        tipo_preguntas=payload.tipo_preguntas,
        num_preguntas=payload.num_preguntas,
        texto_adjunto=payload.texto_adjunto,
    )

    recurso_id = str(uuid.uuid4())
    datos = {"titulo": payload.titulo, "preguntas": contenido["preguntas"]}
    generar_docx_cuestionario(recurso_id, datos)

    return {
        "id": recurso_id,
        "titulo": payload.titulo,
        "tipo_preguntas": payload.tipo_preguntas,
        "num_preguntas": payload.num_preguntas,
        "preguntas": contenido["preguntas"],
        "docx_url": f"/recursos/cuestionarios/{recurso_id}/docx",
    }


@router.post("/cuestionarios/{recurso_id}/ajustar")
def ajustar_cuestionario(
    recurso_id: str,
    payload: CuestionarioAjustar,
    user: User = Depends(get_current_user),
):
    _validar_tipo(payload.tipo_preguntas)

    contenido = ajustar_contenido_cuestionario(
        titulo=payload.titulo,
        tipo_preguntas=payload.tipo_preguntas,
        num_preguntas=payload.num_preguntas,
        preguntas_actuales=payload.preguntas_actuales,
        instrucciones=payload.instrucciones,
    )

    datos = {"titulo": payload.titulo, "preguntas": contenido["preguntas"]}
    generar_docx_cuestionario(recurso_id, datos)

    return {
        "id": recurso_id,
        "titulo": payload.titulo,
        "tipo_preguntas": payload.tipo_preguntas,
        "num_preguntas": payload.num_preguntas,
        "preguntas": contenido["preguntas"],
        "docx_url": f"/recursos/cuestionarios/{recurso_id}/docx",
    }


@router.get("/cuestionarios/{recurso_id}/docx")
def descargar_cuestionario_docx(
    recurso_id: str,
    user: User = Depends(get_current_user),
):
    path = os.path.join("generated_docs", f"cuestionario_{recurso_id}.docx")
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Este cuestionario ya no está disponible. Genera uno nuevo.")

    return FileResponse(
        path,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        filename="cuestionario_cuali.docx",
    )