from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship

from app.db.session import Base
from app.models.user import gen_uuid


class Planeacion(Base):
    __tablename__ = "planeaciones"

    id = Column(String, primary_key=True, default=gen_uuid)
    owner_id = Column(String, ForeignKey("users.id"), nullable=False)

    grado = Column(Integer, nullable=False)
    campo_formativo = Column(String, nullable=False)
    contenido = Column(String, nullable=False)  # título del contenido NEM
    pda = Column(Text, nullable=False)  # texto completo del PDA (contexto oculto para el LLM)
    grupo = Column(String, nullable=False)
    sesiones = Column(Integer, nullable=False)
    tema = Column(Text, nullable=False)

    status = Column(String, default="borrador", nullable=False)
    pdf_path = Column(String, nullable=True)

    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    owner = relationship("User", back_populates="planeaciones")
    mensajes = relationship(
        "Mensaje", back_populates="planeacion", cascade="all, delete-orphan", order_by="Mensaje.created_at"
    )


class Mensaje(Base):
    __tablename__ = "mensajes"

    id = Column(String, primary_key=True, default=gen_uuid)
    planeacion_id = Column(String, ForeignKey("planeaciones.id"), nullable=False)

    role = Column(String, nullable=False)
    content = Column(Text, nullable=False)
    adjuntos_json = Column(Text, nullable=True)

    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    planeacion = relationship("Planeacion", back_populates="mensajes")

    @property
    def adjuntos(self) -> list[dict]:
        import json
        return json.loads(self.adjuntos_json) if self.adjuntos_json else []

    @adjuntos.setter
    def adjuntos(self, value: list[dict]) -> None:
        import json
        self.adjuntos_json = json.dumps(value, ensure_ascii=False)

