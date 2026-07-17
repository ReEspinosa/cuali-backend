from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
import os

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

app.include_router(auth.router)
app.include_router(planeaciones.router)
app.include_router(conversaciones.router)
os.makedirs("uploads", exist_ok=True)
app.mount("/uploads", StaticFiles(directory="uploads"), name="uploads")

app.include_router(archivos.router)
app.include_router(recursos.router)
app.include_router(comunidad_router.router)


@app.get("/")
def health_check():
    return {"status": "ok", "service": "Cuali API"}