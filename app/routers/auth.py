from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.deps import get_current_user
from app.core.rate_limit import client_ip, limiter, rate_limit_ip
from app.core.security import create_access_token, hash_password, verify_password
from app.db.session import get_db
from app.models.user import User
from app.schemas.auth import (
    ForgotPasswordRequest,
    ProfileUpdate,
    RegisterResponse,
    ResendCodeRequest,
    ResetPasswordRequest,
    TokenResponse,
    UserLogin,
    UserOut,
    UserRegister,
    VerifyEmailRequest,
)
from app.services.email import enviar_codigo_verificacion, enviar_link_reseteo

router = APIRouter(prefix="/auth", tags=["auth"])


def _limitar_envio_correo(email: str) -> None:
    """
    Tope de correos por destinatario por hora (verificación + reset juntos).
    Protege dos cosas: (1) que no bombardeen la bandeja de una víctima con
    nuestros correos, y (2) la cuenta de Gmail del proyecto — si mandamos
    spam, Google la suspende y nadie puede verificar su cuenta.
    """
    limiter.check(
        key=f"email-send:{email.lower()}",
        max_hits=settings.email_send_max_per_hour,
        window_seconds=3600,
        error_detail="Ya enviamos varios correos a esa dirección. Espera una hora e intenta de nuevo.",
    )


@router.post(
    "/register",
    response_model=RegisterResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[
        Depends(
            rate_limit_ip(
                max_hits=settings.register_max_per_hour_ip,
                window_seconds=3600,
                scope="register",
                detail="Se crearon demasiadas cuentas desde tu conexión. Intenta más tarde.",
            )
        )
    ],
)
def register(payload: UserRegister, db: Session = Depends(get_db)):
    existing = db.query(User).filter(User.email == payload.email).first()
    if existing:
        raise HTTPException(status_code=400, detail="Ya existe una cuenta con ese correo.")

    _limitar_envio_correo(payload.email)

    user = User(
        nombre=payload.nombre,
        apellido=payload.apellido,
        email=payload.email,
        hashed_password=hash_password(payload.password),
        rol=payload.rol,
        tipo_escuela=payload.tipo_escuela,
        genero=payload.genero,
        grado_imparte=payload.grado_imparte,
        nombre_escuela=payload.nombre_escuela,
        estado=payload.estado,
        is_verified="false",
    )
    codigo = user.generar_codigo_verificacion()

    db.add(user)
    db.commit()

    enviar_codigo_verificacion(user.email, codigo)

    return RegisterResponse(
        message="Cuenta creada. Revisa tu correo para el código de verificación.",
        email=user.email,
    )


@router.post(
    "/verify",
    response_model=TokenResponse,
    dependencies=[
        Depends(rate_limit_ip(20, 900, "verify", "Demasiados intentos. Espera unos minutos."))
    ],
)
def verify_email(payload: VerifyEmailRequest, request: Request, db: Session = Depends(get_db)):
    # Anti fuerza bruta del código de 6 dígitos: máximo N intentos por correo
    # cada 15 minutos. Sin esto, un script prueba el millón de combinaciones
    # en minutos y verifica cuentas ajenas.
    limiter.check(
        key=f"verify:{payload.email.lower()}",
        max_hits=settings.verify_max_attempts_per_15min,
        window_seconds=900,
        error_detail="Demasiados intentos con esta cuenta. Espera 15 minutos y vuelve a intentar.",
    )

    user = db.query(User).filter(User.email == payload.email).first()
    if not user:
        raise HTTPException(status_code=404, detail="No existe una cuenta con ese correo.")

    if user.is_verified == "true":
        raise HTTPException(status_code=400, detail="Esta cuenta ya está verificada.")

    if not user.codigo_valido(payload.code):
        raise HTTPException(status_code=400, detail="El código es incorrecto o ya expiró.")

    user.is_verified = "true"
    user.verification_code = None
    user.verification_code_expires = None
    db.commit()
    db.refresh(user)

    token = create_access_token(subject=user.id)
    return TokenResponse(access_token=token, user=user)


@router.post(
    "/resend-code",
    response_model=RegisterResponse,
    dependencies=[Depends(rate_limit_ip(10, 3600, "resend"))],
)
def resend_code(payload: ResendCodeRequest, db: Session = Depends(get_db)):
    # Cooldown: un reenvío por minuto por correo (además del tope por hora).
    limiter.check(
        key=f"resend-cooldown:{payload.email.lower()}",
        max_hits=1,
        window_seconds=settings.resend_cooldown_seconds,
        error_detail="Acabamos de enviarte un código. Espera un minuto antes de pedir otro.",
    )
    _limitar_envio_correo(payload.email)

    user = db.query(User).filter(User.email == payload.email).first()
    if not user:
        raise HTTPException(status_code=404, detail="No existe una cuenta con ese correo.")

    if user.is_verified == "true":
        raise HTTPException(status_code=400, detail="Esta cuenta ya está verificada.")

    codigo = user.generar_codigo_verificacion()
    db.commit()

    enviar_codigo_verificacion(user.email, codigo)

    return RegisterResponse(message="Código reenviado.", email=user.email)


@router.post(
    "/login",
    response_model=TokenResponse,
    dependencies=[
        Depends(
            rate_limit_ip(
                max_hits=settings.login_max_per_5min_ip,
                window_seconds=300,
                scope="login",
                detail="Demasiados intentos de inicio de sesión. Espera unos minutos.",
            )
        )
    ],
)
def login(payload: UserLogin, request: Request, db: Session = Depends(get_db)):
    # Límite adicional por correo: frena fuerza bruta distribuida (muchas IPs
    # atacando la MISMA cuenta). Solo cuenta intentos FALLIDOS, para que un
    # maestro que sí se sabe su contraseña nunca se tope con este muro.
    email_key = f"login-fail:{payload.email.lower()}"
    if limiter.peek(email_key, settings.login_max_per_15min_email, 900):
        raise HTTPException(
            status_code=429,
            detail="Demasiados intentos fallidos con esta cuenta. Espera 15 minutos.",
        )

    user = db.query(User).filter(User.email == payload.email).first()
    if not user or not verify_password(payload.password, user.hashed_password):
        limiter.check(email_key, settings.login_max_per_15min_email, 900,
                      "Demasiados intentos fallidos con esta cuenta. Espera 15 minutos.")
        raise HTTPException(status_code=401, detail="Correo o contraseña incorrectos.")

    if user.is_verified != "true":
        raise HTTPException(
            status_code=403,
            detail="Todavía no verificas tu correo. Revisa tu bandeja de entrada.",
        )

    token = create_access_token(subject=user.id)
    return TokenResponse(access_token=token, user=user)


@router.get("/me", response_model=UserOut)
def obtener_perfil(user: User = Depends(get_current_user)):
    return user


@router.put("/me", response_model=UserOut)
def actualizar_perfil(
    payload: ProfileUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    user.nombre = payload.nombre
    user.apellido = payload.apellido
    user.rol = payload.rol
    user.tipo_escuela = payload.tipo_escuela
    user.genero = payload.genero
    user.grado_imparte = payload.grado_imparte
    user.nombre_escuela = payload.nombre_escuela
    user.estado = payload.estado

    db.commit()
    db.refresh(user)
    return user


@router.post(
    "/forgot-password",
    response_model=RegisterResponse,
    dependencies=[Depends(rate_limit_ip(10, 3600, "forgot"))],
)
def forgot_password(payload: ForgotPasswordRequest, db: Session = Depends(get_db)):
    _limitar_envio_correo(payload.email)

    user = db.query(User).filter(User.email == payload.email).first()

    # No revelamos si el correo existe o no — respondemos igual de "exitoso"
    # en ambos casos, para no darle a alguien una forma de listar correos.
    if user:
        token = user.generar_reset_token()
        db.commit()
        enviar_link_reseteo(user.email, token)

    return RegisterResponse(
        message="Si el correo existe en nuestro sistema, te llegará un link para restablecer tu contraseña.",
        email=payload.email,
    )


@router.post(
    "/reset-password",
    response_model=RegisterResponse,
    dependencies=[
        Depends(rate_limit_ip(10, 3600, "reset", "Demasiados intentos. Espera una hora."))
    ],
)
def reset_password(payload: ResetPasswordRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.reset_token == payload.token).first()

    if not user or not user.reset_token_valido(payload.token):
        raise HTTPException(status_code=400, detail="El link es inválido o ya expiró.")

    user.hashed_password = hash_password(payload.password)
    user.reset_token = None
    user.reset_token_expires = None
    db.commit()

    return RegisterResponse(message="Tu contraseña se actualizó correctamente.", email=user.email)