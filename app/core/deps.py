from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from app.core.security import decode_access_token
from app.db.session import get_db
from app.models.user import User

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="auth/login")
# Misma lectura del header, pero sin lanzar 401 si no viene: se usa en
# endpoints que aceptan sesion O una URL firmada (ver app/core/firmas.py).
oauth2_scheme_opcional = OAuth2PasswordBearer(tokenUrl="auth/login", auto_error=False)


def _usuario_desde_token(token: str | None, db: Session) -> User | None:
    if not token:
        return None
    user_id = decode_access_token(token)
    if user_id is None:
        return None
    # Busqueda por llave primaria: O(log n).
    return db.query(User).filter(User.id == user_id).first()


def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> User:
    user = _usuario_desde_token(token, db)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="No se pudo validar la sesión",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


def get_current_user_opcional(
    token: str | None = Depends(oauth2_scheme_opcional),
    db: Session = Depends(get_db),
) -> User | None:
    return _usuario_desde_token(token, db)
