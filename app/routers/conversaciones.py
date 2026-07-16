from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.deps import get_current_user
from app.db.session import get_db
from app.models.conversacion import ChatMensaje, Conversacion
from app.models.user import User
from app.schemas.conversacion import (
    ChatMensajeCreate,
    ChatMensajeOut,
    ConversacionDetalle,
    ConversacionOut,
)
from app.services.llm import generar_respuesta_general

router = APIRouter(prefix="/conversaciones", tags=["conversaciones"])


def _get_conversacion_o_404(conversacion_id: str, user: User, db: Session) -> Conversacion:
    conversacion = (
        db.query(Conversacion)
        .filter(Conversacion.id == conversacion_id, Conversacion.owner_id == user.id)
        .first()
    )
    if not conversacion:
        raise HTTPException(status_code=404, detail="Conversación no encontrada.")
    return conversacion


@router.post("", response_model=ConversacionOut, status_code=201)
def crear_conversacion(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    conversacion = Conversacion(owner_id=user.id)
    db.add(conversacion)
    db.commit()
    db.refresh(conversacion)
    return conversacion


@router.get("", response_model=list[ConversacionOut])
def listar_conversaciones(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return (
        db.query(Conversacion)
        .filter(Conversacion.owner_id == user.id)
        .order_by(Conversacion.updated_at.desc())
        .all()
    )


@router.get("/{conversacion_id}", response_model=ConversacionDetalle)
def obtener_conversacion(
    conversacion_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return _get_conversacion_o_404(conversacion_id, user, db)


@router.post("/{conversacion_id}/mensajes", response_model=ChatMensajeOut)
def enviar_mensaje(
    conversacion_id: str,
    payload: ChatMensajeCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    conversacion = _get_conversacion_o_404(conversacion_id, user, db)

    mensaje_usuario = ChatMensaje(conversacion_id=conversacion.id, role="user", content=payload.content)
    mensaje_usuario.adjuntos = [a.model_dump() for a in payload.adjuntos]
    db.add(mensaje_usuario)

    if conversacion.titulo == "Nueva conversación":
        conversacion.titulo = payload.content[:60]

    db.commit()
    db.refresh(mensaje_usuario)

    respuesta_texto, fuentes = generar_respuesta_general(
        historial=conversacion.mensajes,
        nuevo_mensaje=payload.content,
        adjuntos_nuevos=mensaje_usuario.adjuntos,
    )

    mensaje_asistente = ChatMensaje(
        conversacion_id=conversacion.id,
        role="assistant",
        content=respuesta_texto,
    )
    mensaje_asistente.sources = fuentes

    db.add(mensaje_asistente)
    db.commit()
    db.refresh(mensaje_asistente)

    return mensaje_asistente


@router.delete("/{conversacion_id}", status_code=204)
def eliminar_conversacion(
    conversacion_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    conversacion = _get_conversacion_o_404(conversacion_id, user, db)
    db.delete(conversacion)
    db.commit()