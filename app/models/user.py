import secrets
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import Column, DateTime, String
from sqlalchemy.orm import relationship

from app.db.session import Base


def gen_uuid() -> str:
    return str(uuid.uuid4())


def gen_verification_code() -> str:
    return f"{secrets.randbelow(1000000):06d}"


class User(Base):
    __tablename__ = "users"

    id = Column(String, primary_key=True, default=gen_uuid)

    nombre = Column(String, nullable=False)
    apellido = Column(String, nullable=False)
    email = Column(String, unique=True, index=True, nullable=False)
    hashed_password = Column(String, nullable=False)
    rol = Column(String, nullable=False)
    tipo_escuela = Column(String, nullable=False)
    genero = Column(String, nullable=False)

    grado_imparte = Column(String, nullable=True)
    nombre_escuela = Column(String, nullable=True)
    estado = Column(String, nullable=True)

    is_verified = Column(String, default="false", nullable=False)
    verification_code = Column(String, nullable=True)
    verification_code_expires = Column(DateTime, nullable=True)

    reset_token = Column(String, nullable=True)
    reset_token_expires = Column(DateTime, nullable=True)

    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    planeaciones = relationship("Planeacion", back_populates="owner", cascade="all, delete-orphan")

    def generar_codigo_verificacion(self) -> str:
        code = gen_verification_code()
        self.verification_code = code
        self.verification_code_expires = datetime.utcnow() + timedelta(minutes=15)
        return code

    def codigo_valido(self, code: str) -> bool:
        if not self.verification_code or not self.verification_code_expires:
            return False
        if datetime.utcnow() > self.verification_code_expires:
            return False
        return self.verification_code == code

    def generar_reset_token(self) -> str:
        token = secrets.token_urlsafe(32)
        self.reset_token = token
        self.reset_token_expires = datetime.utcnow() + timedelta(hours=2)
        return token

    def reset_token_valido(self, token: str) -> bool:
        if not self.reset_token or not self.reset_token_expires:
            return False
        if datetime.utcnow() > self.reset_token_expires:
            return False
        return self.reset_token == token