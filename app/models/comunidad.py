from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import relationship

from app.db.session import Base
from app.models.user import gen_uuid


class Publicacion(Base):
    __tablename__ = "publicaciones"

    id = Column(String, primary_key=True, default=gen_uuid)
    autor_id = Column(String, ForeignKey("users.id"), nullable=False)

    contenido = Column(Text, nullable=False)
    categoria = Column(String, nullable=True)  # "Recursos" | "Ideas" | "Preguntas" | "Celebraciones" | None
    visibilidad = Column(String, nullable=False, default="publico")  # "publico" | "escuela"
    imagenes_json = Column(Text, nullable=False, default="[]")  # lista de URLs, como JSON string

    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    autor = relationship("User")
    comentarios = relationship("Comentario", back_populates="publicacion", cascade="all, delete-orphan")
    likes = relationship("Like", back_populates="publicacion", cascade="all, delete-orphan")

    @property
    def imagenes(self) -> list[str]:
        import json
        return json.loads(self.imagenes_json or "[]")

    @imagenes.setter
    def imagenes(self, value: list[str]) -> None:
        import json
        self.imagenes_json = json.dumps(value)


class Comentario(Base):
    __tablename__ = "comentarios"

    id = Column(String, primary_key=True, default=gen_uuid)
    publicacion_id = Column(String, ForeignKey("publicaciones.id"), nullable=False)
    autor_id = Column(String, ForeignKey("users.id"), nullable=False)
    parent_id = Column(String, ForeignKey("comentarios.id"), nullable=True)

    contenido = Column(Text, nullable=False)

    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    autor = relationship("User")
    publicacion = relationship("Publicacion", back_populates="comentarios")


class Like(Base):
    __tablename__ = "likes"
    __table_args__ = (UniqueConstraint("publicacion_id", "usuario_id", name="uq_like_publicacion_usuario"),)

    id = Column(String, primary_key=True, default=gen_uuid)
    publicacion_id = Column(String, ForeignKey("publicaciones.id"), nullable=False)
    usuario_id = Column(String, ForeignKey("users.id"), nullable=False)

    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    publicacion = relationship("Publicacion", back_populates="likes")
