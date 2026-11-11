"""
Documentos que los docentes suben a "Libros de texto SEP > Documentos de
docentes" (fichas, examenes, lecturas).

El archivo en disco ya tiene dueno en la tabla `archivos`; esta tabla agrega
lo que se muestra en la biblioteca (titulo, campo, grado) y con quien se
comparte. Un archivo solo puede registrarse una vez (UNIQUE): dos peticiones
simultaneas con el mismo archivo no crean dos documentos.
"""

from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import relationship

from app.db.session import Base
from app.models.user import gen_uuid


class DocumentoDocente(Base):
    __tablename__ = "documentos_docentes"
    __table_args__ = (
        UniqueConstraint("archivo_nombre", name="uq_documento_archivo"),
        # El listado de la comunidad filtra por visibilidad y ordena por
        # fecha: con este indice es O(log n + k) para una pagina de k.
        Index("ix_documentos_visibilidad_fecha", "visibilidad", "created_at"),
    )

    id = Column(String, primary_key=True, default=gen_uuid)
    owner_id = Column(String, ForeignKey("users.id"), nullable=False, index=True)
    archivo_nombre = Column(String, ForeignKey("archivos.nombre_guardado"), nullable=False)

    titulo = Column(String(120), nullable=False)
    campo = Column(String(80), nullable=True)
    grado = Column(Integer, nullable=True)
    # "privado": solo el dueno. "comunidad": cualquier docente con sesion.
    visibilidad = Column(String(20), nullable=False, default="privado")

    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)

    owner = relationship("User")
    archivo = relationship("Archivo")
