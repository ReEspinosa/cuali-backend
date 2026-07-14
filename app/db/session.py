from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

from app.core.config import settings

# TODO: si tu backend existente ya tiene su propio `engine`/`Base`/`get_db`,
# usa esos en vez de este archivo — así todas las tablas viven en el mismo
# metadata y Alembic las detecta juntas.

connect_args = {"check_same_thread": False} if "sqlite" in settings.database_url else {}

engine = create_engine(settings.database_url, connect_args=connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()