"""
Retroalimentacion de los docentes sobre lo que genera Cuali (util / no util).

Es la fuente de datos para evaluar la calidad de las respuestas en la tesis.
La restriccion UNIQUE (usuario, objetivo) garantiza un solo voto por docente
y por respuesta aunque lleguen dos peticiones al mismo tiempo: la base de
datos resuelve la carrera, no el codigo de Python.
"""

from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint

from app.db.session import Base
from app.models.user import gen_uuid


def _ahora() -> datetime:
    return datetime.now(timezone.utc)


class Retroalimentacion(Base):
    __tablename__ = "retroalimentaciones"
    __table_args__ = (
        UniqueConstraint("usuario_id", "objetivo_tipo", "objetivo_id", name="uq_retro_usuario_objetivo"),
    )

    id = Column(String, primary_key=True, default=gen_uuid)
    usuario_id = Column(String, ForeignKey("users.id"), nullable=False, index=True)

    # Que se esta evaluando: "mensaje" (chat de planeacion), "chat_mensaje"
    # (chat general), "planeacion" o "recurso".
    objetivo_tipo = Column(String, nullable=False)
    objetivo_id = Column(String, nullable=False)

    valor = Column(Integer, nullable=False)  # 1 = util, -1 = no util
    comentario = Column(Text, nullable=True)

    created_at = Column(DateTime, default=_ahora)
    updated_at = Column(DateTime, default=_ahora, onupdate=_ahora)
