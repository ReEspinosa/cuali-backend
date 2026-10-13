from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.core.config import settings
from app.core.llm_quota import llm_user_quota
from app.core.rate_limit import client_ip, limiter
from app.db.session import Base, engine
from app.models import archivo, comunidad, conversacion, planeacion, retroalimentacion, user  # noqa: F401
from app.routers import (
    archivos,
    auth,
    comunidad as comunidad_router,
    conversaciones,
    planeaciones,
    recursos,
    retroalimentacion as retroalimentacion_router,
)

Base.metadata.create_all(bind=engine)

# create_all crea tablas nuevas, pero NO agrega indices a tablas que ya
# existian en produccion. Estos CREATE INDEX IF NOT EXISTS son idempotentes
# (funcionan igual en Postgres y en SQLite) y cubren las consultas del feed
# de comunidad. Sin ellos, contar likes o comentarios de una publicacion
# recorre toda la tabla: O(n) en vez de O(log n).
_INDICES = (
    "CREATE INDEX IF NOT EXISTS ix_comentarios_publicacion_id ON comentarios (publicacion_id)",
    "CREATE INDEX IF NOT EXISTS ix_publicaciones_created_at ON publicaciones (created_at)",
    "CREATE INDEX IF NOT EXISTS ix_likes_usuario_id ON likes (usuario_id)",
)
with engine.begin() as _conn:
    for _sql in _INDICES:
        _conn.execute(text(_sql))

app = FastAPI(title="Cuali API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "https://dinamica1.fciencias.unam.mx",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def security_middleware(request: Request, call_next):
    """
    Dos defensas globales, antes de que la petición llegue a cualquier router:

    1) Rate limit por IP: techo generoso para uso humano normal, pero frena
       en seco a un bot en loop haciendo cientos de requests por minuto.

    2) Límite de tamaño de body: sin esto, alguien manda un JSON de 200 MB
       a /conversaciones y satura memoria/LLM. Los uploads reales van por
       /archivos y tienen su propio tope más alto.
    """
    # --- 1) Rate limit global por IP ---
    try:
        limiter.check(
            key=f"global:ip:{client_ip(request)}",
            max_hits=settings.global_ip_max_per_minute,
            window_seconds=60,
            error_detail="Demasiadas solicitudes. Espera un momento e intenta de nuevo.",
        )
    except HTTPException as exc:
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.detail},
            headers=exc.headers or {},
        )

    # --- 2) Límite de tamaño de body ---
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            size = int(content_length)
        except ValueError:
            size = 0
        max_allowed = (
            settings.max_upload_bytes
            if request.url.path.startswith("/archivos")
            else settings.max_body_bytes
        )
        if size > max_allowed:
            return JSONResponse(
                status_code=413,
                content={"detail": "El contenido enviado es demasiado grande."},
            )

    return await call_next(request)


app.include_router(auth.router)

# Los tres routers que disparan generaciones con el LLM llevan la cuota por
# usuario (solo cuenta POSTs; los GET de listar/descargar no consumen cuota).
app.include_router(planeaciones.router, dependencies=[Depends(llm_user_quota)])
app.include_router(conversaciones.router, dependencies=[Depends(llm_user_quota)])

# Los archivos ya NO se montan como carpeta estatica publica: antes,
# cualquiera con la URL (incluso sin sesion) podia descargar lo que subio un
# docente. Ahora pasan por app/routers/archivos.py, que valida sesion y
# dueno, o una URL firmada con caducidad.
app.include_router(archivos.router)
app.include_router(archivos.router_legacy)
app.include_router(retroalimentacion_router.router)
app.include_router(recursos.router, dependencies=[Depends(llm_user_quota)])
app.include_router(comunidad_router.router)


@app.get("/")
def health_check():
    return {"status": "ok", "service": "Cuali API"}