"""
Entorno de pruebas aislado: base SQLite temporal y carpeta de trabajo
temporal (uploads/ y generated_docs/ se crean ahi, no en el repo).

Las variables de entorno se fijan ANTES de importar la app, porque
app.core.config lee la configuracion al importarse.
"""

import os
import sys
import tempfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

_TMP = tempfile.mkdtemp(prefix="cuali-tests-")
os.chdir(_TMP)
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP}/pruebas.db"
os.environ["SECRET_KEY"] = "clave-solo-para-pruebas-" + "x" * 40
os.environ["RATE_LIMIT_ENABLED"] = "false"

from fastapi.testclient import TestClient  # noqa: E402

from app.core.security import create_access_token  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.main import app  # noqa: E402
from app.models.user import User  # noqa: E402


@pytest.fixture()
def client():
    return TestClient(app)


@pytest.fixture()
def db():
    sesion = SessionLocal()
    try:
        yield sesion
    finally:
        sesion.close()


_contador = {"n": 0}


@pytest.fixture()
def crear_docente(db):
    """Crea un docente y regresa (usuario, headers con su token)."""

    def _crear(escuela: str | None = None):
        _contador["n"] += 1
        n = _contador["n"]
        user = User(
            nombre=f"Docente{n}",
            apellido="Prueba",
            email=f"docente{n}@ejemplo.mx",
            hashed_password="no-se-usa",
            rol="docente",
            tipo_escuela="publica",
            genero="otro",
            nombre_escuela=escuela,
            is_verified="true",
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        headers = {"Authorization": f"Bearer {create_access_token(user.id)}"}
        return user, headers

    return _crear
