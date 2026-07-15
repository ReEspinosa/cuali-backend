import json
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import relationship

from app.db.session import Base
from app.models.user import gen_uuid


class Conversacion(Base):
    __tablename__ = "conversaciones"

    id = Column(String, primary_key=True, default=gen_uuid)
    owner_id = Column(String, ForeignKey("users.id"), nullable=False)

    titulo = Column(String, default="Nueva conversación", nullable=False)

    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    mensajes = relationship(
        "ChatMensaje", back_populates="conversacion", cascade="all, delete-orphan", order_by="ChatMensaje.created_at"
    )


class ChatMensaje(Base):
    __tablename__ = "chat_mensajes"

    id = Column(String, primary_key=True, default=gen_uuid)
    conversacion_id = Column(String, ForeignKey("conversaciones.id"), nullable=False)

    role = Column(String, nullable=False)
    content = Column(Text, nullable=False)

    sources_json = Column(Text, nullable=True)
    adjuntos_json = Column(Text, nullable=True)

    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    conversacion = relationship("Conversacion", back_populates="mensajes")

    @property
    def sources(self) -> list[dict]:
        if not self.sources_json:
            return []
        return json.loads(self.sources_json)

    @sources.setter
    def sources(self, value: list[dict]) -> None:
        self.sources_json = json.dumps(value, ensure_ascii=False)

    @property
    def adjuntos(self) -> list[dict]:
        if not self.adjuntos_json:
            return []
        return json.loads(self.adjuntos_json)

    @adjuntos.setter
    def adjuntos(self, value: list[dict]) -> None:
        self.adjuntos_json = json.dumps(value, ensure_ascii=False)