from pydantic import BaseModel, EmailStr, field_validator, model_validator

ROLES_VALIDOS = {"maestro", "estudiante", "director"}
TIPOS_ESCUELA_VALIDOS = {"publica", "privada"}
GENEROS_VALIDOS = {"hombre", "mujer", "prefiero_no_decirlo"}


class UserRegister(BaseModel):
    nombre: str
    apellido: str
    email: EmailStr
    password: str
    password_confirm: str
    rol: str
    tipo_escuela: str
    genero: str

    grado_imparte: str | None = None
    nombre_escuela: str | None = None
    estado: str | None = None

    @field_validator("password")
    @classmethod
    def password_minima(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("La contraseña debe tener al menos 8 caracteres.")
        return v

    @field_validator("rol")
    @classmethod
    def rol_valido(cls, v: str) -> str:
        if v not in ROLES_VALIDOS:
            raise ValueError(f"Rol inválido. Debe ser uno de: {ROLES_VALIDOS}")
        return v

    @field_validator("tipo_escuela")
    @classmethod
    def tipo_escuela_valido(cls, v: str) -> str:
        if v not in TIPOS_ESCUELA_VALIDOS:
            raise ValueError(f"Tipo de escuela inválido. Debe ser uno de: {TIPOS_ESCUELA_VALIDOS}")
        return v

    @field_validator("genero")
    @classmethod
    def genero_valido(cls, v: str) -> str:
        if v not in GENEROS_VALIDOS:
            raise ValueError(f"Género inválido. Debe ser uno de: {GENEROS_VALIDOS}")
        return v

    @model_validator(mode="after")
    def passwords_coinciden(self):
        if self.password != self.password_confirm:
            raise ValueError("Las contraseñas no coinciden.")
        return self


class UserLogin(BaseModel):
    email: EmailStr
    password: str


class VerifyEmailRequest(BaseModel):
    email: EmailStr
    code: str


class ResendCodeRequest(BaseModel):
    email: EmailStr


class UserOut(BaseModel):
    id: str
    nombre: str
    apellido: str
    email: EmailStr
    rol: str
    tipo_escuela: str
    genero: str
    grado_imparte: str | None = None
    nombre_escuela: str | None = None
    estado: str | None = None

    class Config:
        from_attributes = True


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut


class RegisterResponse(BaseModel):
    message: str
    email: EmailStr

class ProfileUpdate(BaseModel):
    nombre: str
    apellido: str
    rol: str
    tipo_escuela: str
    genero: str
    grado_imparte: str | None = None
    nombre_escuela: str | None = None
    estado: str | None = None

    @field_validator("rol")
    @classmethod
    def rol_valido(cls, v: str) -> str:
        if v not in ROLES_VALIDOS:
            raise ValueError(f"Rol inválido. Debe ser uno de: {ROLES_VALIDOS}")
        return v

    @field_validator("tipo_escuela")
    @classmethod
    def tipo_escuela_valido(cls, v: str) -> str:
        if v not in TIPOS_ESCUELA_VALIDOS:
            raise ValueError(f"Tipo de escuela inválido. Debe ser uno de: {TIPOS_ESCUELA_VALIDOS}")
        return v

    @field_validator("genero")
    @classmethod
    def genero_valido(cls, v: str) -> str:
        if v not in GENEROS_VALIDOS:
            raise ValueError(f"Género inválido. Debe ser uno de: {GENEROS_VALIDOS}")
        return v


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    token: str
    password: str
    password_confirm: str

    @field_validator("password")
    @classmethod
    def password_minima(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("La contraseña debe tener al menos 8 caracteres.")
        return v

    @model_validator(mode="after")
    def passwords_coinciden(self):
        if self.password != self.password_confirm:
            raise ValueError("Las contraseñas no coinciden.")
        return self