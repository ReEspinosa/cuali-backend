import os
import uuid

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.core.deps import get_current_user
from app.models.user import User
from app.services.llm import (
    ajustar_contenido_cuestionario,
    generar_contenido_ahorcado,
    generar_contenido_cartel,
    generar_contenido_crucigrama,
    generar_contenido_cuestionario,
    generar_contenido_diapositivas,
    generar_contenido_mapa_mental,
    generar_contenido_memorama,
    generar_contenido_ruleta,
    generar_contenido_sopa_letras,
    generar_contenido_verdadero_falso,
    generar_documento_laboratorio,
    generar_respuesta_laboratorio,
)
from app.services.laboratorio_generator import generar_docx_laboratorio
from app.services.crucigrama_generator import construir_crucigrama
from app.services.sopa_letras_generator import construir_sopa_letras
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


class MapaMentalCreate(BaseModel):
    tema: str
    resumen: str
    texto_adjunto: str | None = None


@router.post("/mapas-mentales")
def crear_mapa_mental(
    payload: MapaMentalCreate,
    user: User = Depends(get_current_user),
):
    contenido = generar_contenido_mapa_mental(
        tema=payload.tema,
        resumen=payload.resumen,
        texto_adjunto=payload.texto_adjunto,
    )
    return {
        "tema_central": contenido["tema_central"],
        "ramas": contenido["ramas"],
    }


class MemoramaCreate(BaseModel):
    tema: str
    num_pares: int = Field(ge=4, le=10)
    texto_adjunto: str | None = None


@router.post("/juegos/memorama")
def crear_memorama(
    payload: MemoramaCreate,
    user: User = Depends(get_current_user),
):
    contenido = generar_contenido_memorama(
        tema=payload.tema,
        num_pares=payload.num_pares,
        texto_adjunto=payload.texto_adjunto,
    )
    return {"tema": payload.tema, "pares": contenido["pares"]}


class SopaLetrasCreate(BaseModel):
    tema: str
    num_palabras: int = Field(ge=6, le=12)
    texto_adjunto: str | None = None


@router.post("/juegos/sopa-de-letras")
def crear_sopa_de_letras(
    payload: SopaLetrasCreate,
    user: User = Depends(get_current_user),
):
    contenido = generar_contenido_sopa_letras(
        tema=payload.tema,
        num_palabras=payload.num_palabras,
        texto_adjunto=payload.texto_adjunto,
    )
    sopa = construir_sopa_letras(contenido["palabras"])

    if not sopa["palabras"]:
        raise HTTPException(status_code=500, detail="No se pudo construir la sopa de letras. Intenta de nuevo.")

    return {
        "tema": payload.tema,
        "grid": sopa["grid"],
        "soluciones": sopa["soluciones"],
        "palabras": sopa["palabras"],
        "tamano": sopa["tamano"],
    }


class RuletaCreate(BaseModel):
    tema: str
    num_preguntas: int = Field(ge=6, le=10)
    texto_adjunto: str | None = None


@router.post("/juegos/ruleta")
def crear_ruleta(
    payload: RuletaCreate,
    user: User = Depends(get_current_user),
):
    contenido = generar_contenido_ruleta(
        tema=payload.tema,
        num_preguntas=payload.num_preguntas,
        texto_adjunto=payload.texto_adjunto,
    )
    return {"tema": payload.tema, "preguntas": contenido["preguntas"]}


class CrucigramaCreate(BaseModel):
    tema: str
    num_palabras: int = Field(ge=6, le=12)
    texto_adjunto: str | None = None


@router.post("/juegos/crucigrama")
def crear_crucigrama(
    payload: CrucigramaCreate,
    user: User = Depends(get_current_user),
):
    contenido = generar_contenido_crucigrama(
        tema=payload.tema,
        num_palabras=payload.num_palabras,
        texto_adjunto=payload.texto_adjunto,
    )
    crucigrama = construir_crucigrama(contenido["palabras"])

    if not crucigrama["pistas"]:
        raise HTTPException(status_code=500, detail="No se pudo construir el crucigrama. Intenta de nuevo.")

    return {
        "tema": payload.tema,
        "celdas": crucigrama["celdas"],
        "numeros": crucigrama["numeros"],
        "pistas": crucigrama["pistas"],
        "ancho": crucigrama["ancho"],
        "alto": crucigrama["alto"],
        "no_colocadas": crucigrama["no_colocadas"],
    }


class AhorcadoCreate(BaseModel):
    tema: str
    num_palabras: int = Field(ge=3, le=8)
    texto_adjunto: str | None = None


@router.post("/juegos/ahorcado")
def crear_ahorcado(
    payload: AhorcadoCreate,
    user: User = Depends(get_current_user),
):
    contenido = generar_contenido_ahorcado(
        tema=payload.tema,
        num_palabras=payload.num_palabras,
        texto_adjunto=payload.texto_adjunto,
    )
    return {"tema": payload.tema, "palabras": contenido["palabras"]}


class VerdaderoFalsoCreate(BaseModel):
    tema: str
    num_afirmaciones: int = Field(ge=6, le=10)
    texto_adjunto: str | None = None


@router.post("/juegos/verdadero-falso")
def crear_verdadero_falso(
    payload: VerdaderoFalsoCreate,
    user: User = Depends(get_current_user),
):
    contenido = generar_contenido_verdadero_falso(
        tema=payload.tema,
        num_afirmaciones=payload.num_afirmaciones,
        texto_adjunto=payload.texto_adjunto,
    )
    return {"tema": payload.tema, "afirmaciones": contenido["afirmaciones"]}


class CartelCreate(BaseModel):
    tema: str
    descripcion: str
    tema_color: str
    texto_adjunto: str | None = None


@router.post("/carteles")
def crear_cartel(
    payload: CartelCreate,
    user: User = Depends(get_current_user),
):
    if payload.tema_color not in TEMAS:
        raise HTTPException(status_code=400, detail=f"Tema de color inválido. Opciones: {', '.join(TEMAS)}")

    contenido = generar_contenido_cartel(
        tema=payload.tema,
        descripcion=payload.descripcion,
        texto_adjunto=payload.texto_adjunto,
    )
    return {
        "titulo": contenido["titulo"],
        "subtitulo": contenido["subtitulo"],
        "puntos": contenido["puntos"],
        "emoji": contenido["emoji"],
        "tema_color": payload.tema_color,
    }


class LaboratorioMensaje(BaseModel):
    historial: list[dict]
    mensaje: str


@router.post("/laboratorio/mensaje")
def laboratorio_mensaje(
    payload: LaboratorioMensaje,
    user: User = Depends(get_current_user),
):
    respuesta = generar_respuesta_laboratorio(payload.historial, payload.mensaje)
    return {"content": respuesta}


class LaboratorioGenerar(BaseModel):
    historial: list[dict]


@router.post("/laboratorio/generar")
def laboratorio_generar(
    payload: LaboratorioGenerar,
    user: User = Depends(get_current_user),
):
    if len(payload.historial) < 2:
        raise HTTPException(
            status_code=400,
            detail="Platica un poco más con Cuali antes de generar el documento.",
        )

    contenido = generar_documento_laboratorio(payload.historial)
    recurso_id = str(uuid.uuid4())
    generar_docx_laboratorio(recurso_id, contenido["titulo"], contenido["contenido"])

    return {
        "id": recurso_id,
        "titulo": contenido["titulo"],
        "contenido": contenido["contenido"],
        "docx_url": f"/recursos/laboratorio/{recurso_id}/docx",
    }


@router.get("/laboratorio/{recurso_id}/docx")
def descargar_laboratorio_docx(
    recurso_id: str,
    user: User = Depends(get_current_user),
):
    path = os.path.join("generated_docs", f"laboratorio_{recurso_id}.docx")
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Este documento ya no está disponible. Genera uno nuevo.")

    return FileResponse(
        path,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        filename="laboratorio_cuali.docx",
    )