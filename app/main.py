from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.db.session import Base, engine
from app.models import planeacion, user  # noqa: F401 — necesario para que Base los registre
from app.routers import auth, planeaciones

# TODO: en tu backend real, esta creación de tablas la maneja Alembic
# (alembic upgrade head), no create_all(). La dejamos aquí solo para
# poder correr el proyecto de inmediato sin configurar migraciones todavía.
Base.metadata.create_all(bind=engine)

app = FastAPI(title="Cuali API", version="0.1.0")

# TODO: restringir origins a tu dominio real en producción.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(planeaciones.router)


@app.get("/")
def health_check():
    return {"status": "ok", "service": "Cuali API"}