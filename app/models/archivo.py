"""
Registro de propiedad de los archivos que viven en disco.

Antes, los archivos subidos (uploads/) y los generados (generated_docs/) solo
existian en disco: el servidor no sabia de quien eran. Eso obligaba a servir
/uploads como carpeta publica y a dejar que cualquier usuario con sesion
descargara cualquier recurso generado si conocia su id.

Estas tablas guardan el dueno de cada archivo para poder validarlo en cada
descarga. La busqueda es por llave primaria, asi que validar un acceso cuesta
O(log n) en el indice B-tree de Postgres, sin importar cuantos archivos haya.
"""

from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, String

from app.db.session import Base


def _ahora() -> datetime:
    return datetime.now(timezone.utc)


class Archivo(Base):
    __tablename__ = "archivos"

    # Nombre con el que se guardo en disco: "<uuid4>.<ext>". Es la llave
    # porque es lo que llega en la URL.
    nombre_guardado = Column(String, primary_key=True)
    owner_id = Column(String, ForeignKey("users.id"), nullable=False, index=True)

    filename_original = Column(String, nullable=False)
    tipo = Column(String, nullable=False)  # "imagen" | "documento"

    # "privado": solo el dueno.
    # "comunidad": cualquier docente con sesion (se marca al usarlo en una
    # publicacion de la comunidad).
    visibilidad = Column(String, nullable=False, default="privado")

    created_at = Column(DateTime, default=_ahora)


class RecursoGenerado(Base):
    __tablename__ = "recursos_generados"

    # Mismo uuid que se usa en el nombre del archivo en generated_docs/.
    id = Column(String, primary_key=True)
    owner_id = Column(String, ForeignKey("users.id"), nullable=False, index=True)
    tipo = Column(String, nullable=False)  # "diapositivas" | "cuestionario" | "laboratorio"

    created_at = Column(DateTime, default=_ahora)
