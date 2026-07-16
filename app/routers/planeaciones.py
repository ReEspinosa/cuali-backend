from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.core.deps import get_current_user
from app.db.session import get_db
from app.models.planeacion import Mensaje, Planeacion
from app.models.user import User
from app.schemas.planeacion import (
    MensajeCreate, MensajeOut, PlaneacionCreate, PlaneacionDetalle, PlaneacionOut,
)
from app.services.llm import extraer_planeacion_estructurada, generar_respuesta_chat
from app.services.docx_generator import generar_docx_planeacion

router = APIRouter(prefix="/planeaciones", tags=["planeaciones"])


def _get_planeacion_o_404(planeacion_id: str, user: User, db: Session) -> Planeacion:
    planeacion = (
        db.query(Planeacion)
        .filter(Planeacion.id == planeacion_id, Planeacion.owner_id == user.id)
        .first()
    )
    if not planeacion:
        raise HTTPException(status_code=404, detail="Planeación no encontrada.")
    return planeacion


@router.post("", response_model=PlaneacionOut, status_code=201)
def crear_planeacion(
    payload: PlaneacionCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    planeacion = Planeacion(owner_id=user.id, **payload.model_dump())
    db.add(planeacion)
    db.commit()
    db.refresh(planeacion)
    return planeacion


@router.get("", response_model=list[PlaneacionOut])
def listar_planeaciones(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return (
        db.query(Planeacion)
        .filter(Planeacion.owner_id == user.id)
        .order_by(Planeacion.updated_at.desc())
        .all()
    )


@router.get("/{planeacion_id}", response_model=PlaneacionDetalle)
def obtener_planeacion(
    planeacion_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return _get_planeacion_o_404(planeacion_id, user, db)


@router.post("/{planeacion_id}/mensajes", response_model=MensajeOut)
def enviar_mensaje(
    planeacion_id: str,
    payload: MensajeCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    planeacion = _get_planeacion_o_404(planeacion_id, user, db)

    mensaje_usuario = Mensaje(planeacion_id=planeacion.id, role="user", content=payload.content)
    mensaje_usuario.adjuntos = [a.model_dump() for a in payload.adjuntos]
    db.add(mensaje_usuario)
    db.commit()
    db.refresh(mensaje_usuario)

    # TODO: cuando esto sea streaming real, este endpoint cambia a
    # StreamingResponse y el LLM se llama con stream=True.
    respuesta = generar_respuesta_chat(
        planeacion=planeacion,
        historial=planeacion.mensajes,
        nuevo_mensaje=payload.content,
        adjuntos_nuevos=mensaje_usuario.adjuntos,
    )

    mensaje_asistente = Mensaje(planeacion_id=planeacion.id, role="assistant", content=respuesta)
    db.add(mensaje_asistente)
    db.commit()
    db.refresh(mensaje_asistente)

    return mensaje_asistente


@router.post("/{planeacion_id}/generar")
def generar_planeacion_docx(
    planeacion_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    planeacion = _get_planeacion_o_404(planeacion_id, user, db)

    mensajes_usuario = [m for m in planeacion.mensajes if m.role == "user"]
    if len(mensajes_usuario) < 5:
        raise HTTPException(
            status_code=400,
            detail="Aún no hay información suficiente para generar la planeación. "
                   "Sigue platicando con Cuali un poco más.",
        )

    datos_estructurados = extraer_planeacion_estructurada(planeacion, planeacion.mensajes)
    docx_path = generar_docx_planeacion(planeacion.id, datos_estructurados)

    planeacion.docx_path = docx_path
    planeacion.status = "finalizada"
    db.commit()

    return {"docx_url": f"/planeaciones/{planeacion.id}/docx"}


@router.get("/{planeacion_id}/docx")
def descargar_docx(
    planeacion_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    planeacion = _get_planeacion_o_404(planeacion_id, user, db)
    if not planeacion.docx_path:
        raise HTTPException(status_code=404, detail="Esta planeación todavía no tiene un documento generado.")

    return FileResponse(
        planeacion.docx_path,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        filename=f"planeacion_{planeacion.contenido[:30]}.docx",
    )