"""
Rate limiting en memoria (ventana deslizante).

No requiere Redis ni dependencias extra: Cuali corre en UN solo proceso
uvicorn en el servidor de la UNAM, así que un limitador en memoria con un
lock es suficiente y correcto. Si algun dia se escala a varios workers
(uvicorn --workers N o varios contenedores), este modulo debe migrarse a
Redis, porque cada worker tendria su propio contador.

Uso:

    # Como dependencia de FastAPI (limite por IP):
    @router.post("/register", dependencies=[Depends(rate_limit_ip(5, 3600, "register"))])

    # Manual dentro de un endpoint (limite por email, por usuario, etc.):
    limiter.check(f"verify:{email}", max_hits=5, window_seconds=900,
                  error_detail="Demasiados intentos. Espera unos minutos.")
"""

import threading
import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request

from app.core.config import settings


class SlidingWindowLimiter:
    """Limitador de ventana deslizante: permite max_hits por window_seconds."""

    # Cada cuanto se purgan llaves inactivas para que la memoria no crezca.
    _CLEANUP_INTERVAL = 600  # 10 minutos
    # Una llave se considera inactiva si su ultimo hit fue hace mas de esto.
    _KEY_TTL = 7200  # 2 horas (cubre las ventanas mas largas que usamos)

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._last_cleanup = time.monotonic()

    def check(
        self,
        key: str,
        max_hits: int,
        window_seconds: int,
        error_detail: str = "Demasiadas solicitudes. Intenta de nuevo en unos minutos.",
    ) -> None:
        """Registra un hit para `key`. Lanza HTTP 429 si se excede el limite."""
        if not settings.rate_limit_enabled:
            return

        now = time.monotonic()
        with self._lock:
            self._maybe_cleanup(now)

            hits = self._hits[key]
            while hits and now - hits[0] > window_seconds:
                hits.popleft()

            if len(hits) >= max_hits:
                retry_after = max(1, int(window_seconds - (now - hits[0])) + 1)
                raise HTTPException(
                    status_code=429,
                    detail=error_detail,
                    headers={"Retry-After": str(retry_after)},
                )

            hits.append(now)

    def peek(self, key: str, max_hits: int, window_seconds: int) -> bool:
        """True si la llave YA excedio el limite (sin registrar un hit nuevo)."""
        if not settings.rate_limit_enabled:
            return False
        now = time.monotonic()
        with self._lock:
            hits = self._hits.get(key)
            if not hits:
                return False
            while hits and now - hits[0] > window_seconds:
                hits.popleft()
            return len(hits) >= max_hits

    def _maybe_cleanup(self, now: float) -> None:
        if now - self._last_cleanup < self._CLEANUP_INTERVAL:
            return
        self._last_cleanup = now
        stale = [k for k, q in self._hits.items() if not q or now - q[-1] > self._KEY_TTL]
        for k in stale:
            del self._hits[k]


# Instancia global compartida por toda la app.
limiter = SlidingWindowLimiter()


def client_ip(request: Request) -> str:
    """
    IP real del cliente. Si el backend esta detras de nginx/Apache en la UNAM,
    la IP directa seria siempre la del proxy; por eso se respeta
    X-Forwarded-For (el proxy DEBE sobreescribir ese header, no confiarlo
    del cliente — ver nota de despliegue en el resumen de cambios).
    """
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def rate_limit_ip(max_hits: int, window_seconds: int, scope: str, detail: str | None = None):
    """Fabrica una dependencia de FastAPI que limita por IP dentro de un scope."""

    def dependency(request: Request) -> None:
        limiter.check(
            key=f"{scope}:ip:{client_ip(request)}",
            max_hits=max_hits,
            window_seconds=window_seconds,
            error_detail=detail
            or "Demasiadas solicitudes desde tu conexión. Intenta de nuevo en unos minutos.",
        )

    return dependency