import secrets
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import Column, DateTime, String
from sqlalchemy.orm import relationship

from app.db.session import Base


def gen_uuid() -> str:
    return str(uuid.uuid4())


def gen_verification_code() -> str:
    return f"{secrets.randbelow(1000000):06d}"  # código de 6 dígitos, tipo "042817"


class User(Base):
    __tablename__ = "users"

    id = Column(String, primary_key=True, default=gen_uuid)

    # --- Obligatorios en el registro ---
    nombre = Column(String, nullable=False)
    apellido = Column(String, nullable=False)
    email = Column(String, unique=True, index=True, nullable=False)
    hashed_password = Column(String, nullable=False)
    rol = Column(String, nullable=False)  # "maestro" | "estudiante" | "director"
    tipo_escuela = Column(String, nullable=False)  # "publica" | "privada"
    genero = Column(String, nullable=False)  # "hombre" | "mujer" | "prefiero_no_decirlo"

    # --- Opcionales ---
    grado_imparte = Column(String, nullable=True)  # "1" a "6"
    nombre_escuela = Column(String, nullable=True)
    estado = Column(String, nullable=True)  # estado de la república

    # --- Verificación de correo ---
    is_verified = Column(String, default="false", nullable=False)  # "true"/"false"
    verification_code = Column(String, nullable=True)
    verification_code_expires = Column(DateTime, nullable=True)

    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    planeaciones = relationship("Planeacion", back_populates="owner", cascade="all, delete-orphan")

    def generar_codigo_verificacion(self) -> str:
        code = gen_verification_code()
        self.verification_code = code
        # Naive UTC (sin tzinfo) porque SQLite no conserva la zona horaria
        # al leer de vuelta, y comparar naive vs aware truena.
        self.verification_code_expires = datetime.utcnow() + timedelta(minutes=15)
        return code

    def codigo_valido(self, code: str) -> bool:
        if not self.verification_code or not self.verification_code_expires:
            return False
        if datetime.utcnow() > self.verification_code_expires:
            return False
        return self.verification_code == code