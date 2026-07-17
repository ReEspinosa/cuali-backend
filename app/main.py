from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
import os

from app.core.config import settings
from app.core.llm_quota import llm_user_quota
from app.core.rate_limit import client_ip, limiter
from app.db.session import Base, engine
from app.models import comunidad, conversacion, planeacion, user  # noqa: F401
from app.routers import archivos, auth, comunidad as comunidad_router, conversaciones, planeaciones, recursos

Base.metadata.create_all(bind=engine)

app = FastAPI(title="Cuali API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
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

os.makedirs("uploads", exist_ok=True)
app.mount("/uploads", StaticFiles(directory="uploads"), name="uploads")

app.include_router(archivos.router)
app.include_router(recursos.router, dependencies=[Depends(llm_user_quota)])
app.include_router(comunidad_router.router)


@app.get("/")
def health_check():
    return {"status": "ok", "service": "Cuali API"}