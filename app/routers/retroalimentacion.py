"""
Retroalimentacion "me sirvio / no me sirvio" sobre lo que genera Cuali.

Solo se puede evaluar algo propio: la respuesta de una conversacion o
planeacion del mismo docente, o un recurso que el mismo genero. Asi nadie
puede inflar o hundir la evaluacion de contenido ajeno.

Concurrencia: es un "upsert". Si el mismo docente manda dos votos al mismo
tiempo, la restriccion UNIQUE (usuario, objetivo) deja pasar solo una
insercion; la otra recibe IntegrityError, se reintenta como actualizacion y
el resultado final es un solo registro con el ultimo valor.

Complejidad: O(log n) por peticion (busquedas por llave e indice unico).
"""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.deps import get_current_user
from app.db.session import get_db
from app.models.archivo import RecursoGenerado
from app.models.conversacion import ChatMensaje, Conversacion
from app.models.planeacion import Mensaje, Planeacion
from app.models.retroalimentacion import Retroalimentacion
from app.models.user import User

router = APIRouter(prefix="/retroalimentacion", tags=["retroalimentacion"])

ObjetivoTipo = Literal["mensaje", "chat_mensaje", "planeacion", "recurso"]


class RetroalimentacionIn(BaseModel):
    objetivo_tipo: ObjetivoTipo
    objetivo_id: str = Field(min_length=1, max_length=64)
    valor: Literal[1, -1]
    comentario: str | None = Field(None, max_length=1000)


def _es_propio(objetivo_tipo: str, objetivo_id: str, user: User, db: Session) -> bool:
    if objetivo_tipo == "planeacion":
        return (
            db.query(Planeacion.id)
            .filter(Planeacion.id == objetivo_id, Planeacion.owner_id == user.id)
            .first()
            is not None
        )
    if objetivo_tipo == "mensaje":
        return (
            db.query(Mensaje.id)
            .join(Planeacion, Mensaje.planeacion_id == Planeacion.id)
            .filter(Mensaje.id == objetivo_id, Planeacion.owner_id == user.id, Mensaje.role == "assistant")
            .first()
            is not None
        )
    if objetivo_tipo == "chat_mensaje":
        return (
            db.query(ChatMensaje.id)
            .join(Conversacion, ChatMensaje.conversacion_id == Conversacion.id)
            .filter(ChatMensaje.id == objetivo_id, Conversacion.owner_id == user.id, ChatMensaje.role == "assistant")
            .first()
            is not None
        )
    if objetivo_tipo == "recurso":
        registro = db.get(RecursoGenerado, objetivo_id)
        return registro is not None and registro.owner_id == user.id
    return False


def _aplicar(registro: Retroalimentacion, payload: RetroalimentacionIn) -> None:
    registro.valor = payload.valor
    registro.comentario = (payload.comentario or "").strip() or None


@router.put("")
def guardar_retroalimentacion(
    payload: RetroalimentacionIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if not _es_propio(payload.objetivo_tipo, payload.objetivo_id, user, db):
        raise HTTPException(status_code=404, detail="No encontramos lo que quieres evaluar.")

    filtro = (
        Retroalimentacion.usuario_id == user.id,
        Retroalimentacion.objetivo_tipo == payload.objetivo_tipo,
        Retroalimentacion.objetivo_id == payload.objetivo_id,
    )

    registro = db.query(Retroalimentacion).filter(*filtro).first()
    if registro is None:
        registro = Retroalimentacion(
            usuario_id=user.id,
            objetivo_tipo=payload.objetivo_tipo,
            objetivo_id=payload.objetivo_id,
        )
        _aplicar(registro, payload)
        db.add(registro)
        try:
            db.commit()
        except IntegrityError:
            # Otra peticion inserto primero: se actualiza la que gano.
            db.rollback()
            registro = db.query(Retroalimentacion).filter(*filtro).one()
            _aplicar(registro, payload)
            db.commit()
    else:
        _aplicar(registro, payload)
        db.commit()

    return {"objetivo_tipo": payload.objetivo_tipo, "objetivo_id": payload.objetivo_id, "valor": payload.valor}
