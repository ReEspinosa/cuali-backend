import json
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.core.deps import get_current_user
from app.db.session import get_db
from app.models.comunidad import Comentario, Like, Publicacion
from app.models.user import User

router = APIRouter(prefix="/comunidad", tags=["comunidad"])

CATEGORIAS_VALIDAS = {"Recursos", "Ideas", "Preguntas", "Celebraciones"}


def _autor_dict(autor: User) -> dict:
    return {
        "id": autor.id,
        "nombre": f"{autor.nombre} {autor.apellido}",
        "escuela": autor.nombre_escuela,
    }


def _publicacion_dict(pub: Publicacion, db: Session, user_id: str) -> dict:
    total_likes = db.query(Like).filter(Like.publicacion_id == pub.id).count()
    le_gusta = db.query(Like).filter(Like.publicacion_id == pub.id, Like.usuario_id == user_id).first() is not None
    total_comentarios = db.query(Comentario).filter(Comentario.publicacion_id == pub.id).count()

    return {
        "id": pub.id,
        "autor": _autor_dict(pub.autor),
        "contenido": pub.contenido,
        "categoria": pub.categoria,
        "visibilidad": pub.visibilidad,
        "imagenes": pub.imagenes,
        "total_likes": total_likes,
        "le_gusta": le_gusta,
        "total_comentarios": total_comentarios,
        "created_at": pub.created_at,
        "es_autor": pub.autor_id == user_id,
    }


def _construir_arbol_comentarios(comentarios: list[Comentario], user_id: str) -> list[dict]:
    por_id = {
        c.id: {
            "id": c.id,
            "autor": _autor_dict(c.autor),
            "contenido": c.contenido,
            "created_at": c.created_at,
            "es_autor": c.autor_id == user_id,
            "respuestas": [],
        }
        for c in comentarios
    }
    raiz = []
    for c in comentarios:
        nodo = por_id[c.id]
        if c.parent_id and c.parent_id in por_id:
            por_id[c.parent_id]["respuestas"].append(nodo)
        else:
            raiz.append(nodo)
    return raiz


@router.get("/publicaciones")
def listar_publicaciones(
    busqueda: str | None = Query(None),
    categoria: str | None = Query(None),
    alcance: str = Query("todo"),  # "todo" | "escuela"
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    query = db.query(Publicacion)

    if alcance == "escuela" and user.nombre_escuela:
        query = query.join(User, Publicacion.autor_id == User.id).filter(
            or_(Publicacion.visibilidad == "publico", User.nombre_escuela == user.nombre_escuela)
        )
    else:
        query = query.filter(
            or_(Publicacion.visibilidad == "publico", Publicacion.autor_id == user.id)
        )

    if categoria and categoria in CATEGORIAS_VALIDAS:
        query = query.filter(Publicacion.categoria == categoria)

    if busqueda:
        query = query.filter(Publicacion.contenido.ilike(f"%{busqueda}%"))

    publicaciones = query.order_by(Publicacion.created_at.desc()).limit(100).all()
    return [_publicacion_dict(p, db, user.id) for p in publicaciones]


class PublicacionCreate(BaseModel):
    contenido: str
    categoria: str | None = None
    visibilidad: str = "publico"
    imagenes: list[str] = []


@router.post("/publicaciones")
def crear_publicacion(
    payload: PublicacionCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if payload.visibilidad not in ("publico", "escuela"):
        raise HTTPException(status_code=400, detail="Visibilidad inválida.")
    if payload.categoria and payload.categoria not in CATEGORIAS_VALIDAS:
        raise HTTPException(status_code=400, detail=f"Categoría inválida. Opciones: {', '.join(CATEGORIAS_VALIDAS)}")
    if payload.visibilidad == "escuela" and not user.nombre_escuela:
        raise HTTPException(status_code=400, detail="Necesitas tener una escuela registrada en tu perfil para publicar solo a tu escuela.")

    pub = Publicacion(
        autor_id=user.id,
        contenido=payload.contenido,
        categoria=payload.categoria,
        visibilidad=payload.visibilidad,
    )
    pub.imagenes = payload.imagenes
    db.add(pub)
    db.commit()
    db.refresh(pub)
    return _publicacion_dict(pub, db, user.id)


class PublicacionUpdate(BaseModel):
    contenido: str | None = None
    categoria: str | None = None
    visibilidad: str | None = None
    imagenes: list[str] | None = None


def _get_publicacion_propia_o_404(publicacion_id: str, user: User, db: Session) -> Publicacion:
    pub = db.query(Publicacion).filter(Publicacion.id == publicacion_id).first()
    if not pub:
        raise HTTPException(status_code=404, detail="Publicación no encontrada.")
    if pub.autor_id != user.id:
        raise HTTPException(status_code=403, detail="Solo puedes editar o borrar tus propias publicaciones.")
    return pub


@router.patch("/publicaciones/{publicacion_id}")
def editar_publicacion(
    publicacion_id: str,
    payload: PublicacionUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    pub = _get_publicacion_propia_o_404(publicacion_id, user, db)

    if payload.contenido is not None:
        pub.contenido = payload.contenido
    if payload.categoria is not None:
        if payload.categoria and payload.categoria not in CATEGORIAS_VALIDAS:
            raise HTTPException(status_code=400, detail=f"Categoría inválida. Opciones: {', '.join(CATEGORIAS_VALIDAS)}")
        pub.categoria = payload.categoria or None
    if payload.visibilidad is not None:
        if payload.visibilidad not in ("publico", "escuela"):
            raise HTTPException(status_code=400, detail="Visibilidad inválida.")
        pub.visibilidad = payload.visibilidad
    if payload.imagenes is not None:
        pub.imagenes = payload.imagenes

    db.commit()
    db.refresh(pub)
    return _publicacion_dict(pub, db, user.id)


@router.delete("/publicaciones/{publicacion_id}")
def borrar_publicacion(
    publicacion_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    pub = _get_publicacion_propia_o_404(publicacion_id, user, db)
    db.delete(pub)
    db.commit()
    return {"ok": True}


@router.post("/publicaciones/{publicacion_id}/like")
def toggle_like(
    publicacion_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    pub = db.query(Publicacion).filter(Publicacion.id == publicacion_id).first()
    if not pub:
        raise HTTPException(status_code=404, detail="Publicación no encontrada.")

    like = db.query(Like).filter(Like.publicacion_id == publicacion_id, Like.usuario_id == user.id).first()
    if like:
        db.delete(like)
        db.commit()
        dio_like = False
    else:
        db.add(Like(publicacion_id=publicacion_id, usuario_id=user.id))
        db.commit()
        dio_like = True

    total = db.query(Like).filter(Like.publicacion_id == publicacion_id).count()
    return {"le_gusta": dio_like, "total_likes": total}


@router.get("/publicaciones/{publicacion_id}/comentarios")
def listar_comentarios(
    publicacion_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    comentarios = (
        db.query(Comentario)
        .filter(Comentario.publicacion_id == publicacion_id)
        .order_by(Comentario.created_at.asc())
        .all()
    )
    return _construir_arbol_comentarios(comentarios, user.id)


class ComentarioCreate(BaseModel):
    contenido: str
    parent_id: str | None = None


@router.post("/publicaciones/{publicacion_id}/comentarios")
def crear_comentario(
    publicacion_id: str,
    payload: ComentarioCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    pub = db.query(Publicacion).filter(Publicacion.id == publicacion_id).first()
    if not pub:
        raise HTTPException(status_code=404, detail="Publicación no encontrada.")

    if payload.parent_id:
        padre = db.query(Comentario).filter(Comentario.id == payload.parent_id).first()
        if not padre or padre.publicacion_id != publicacion_id:
            raise HTTPException(status_code=400, detail="El comentario al que respondes no existe.")

    comentario = Comentario(
        publicacion_id=publicacion_id,
        autor_id=user.id,
        parent_id=payload.parent_id,
        contenido=payload.contenido,
    )
    db.add(comentario)
    db.commit()
    db.refresh(comentario)

    return {
        "id": comentario.id,
        "autor": _autor_dict(comentario.autor),
        "contenido": comentario.contenido,
        "created_at": comentario.created_at,
        "es_autor": True,
        "respuestas": [],
    }


@router.patch("/comentarios/{comentario_id}")
def editar_comentario(
    comentario_id: str,
    payload: ComentarioCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    comentario = db.query(Comentario).filter(Comentario.id == comentario_id).first()
    if not comentario:
        raise HTTPException(status_code=404, detail="Comentario no encontrado.")
    if comentario.autor_id != user.id:
        raise HTTPException(status_code=403, detail="Solo puedes editar tus propios comentarios.")

    comentario.contenido = payload.contenido
    db.commit()
    return {"ok": True}


@router.delete("/comentarios/{comentario_id}")
def borrar_comentario(
    comentario_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    comentario = db.query(Comentario).filter(Comentario.id == comentario_id).first()
    if not comentario:
        raise HTTPException(status_code=404, detail="Comentario no encontrado.")
    if comentario.autor_id != user.id:
        raise HTTPException(status_code=403, detail="Solo puedes borrar tus propios comentarios.")

    db.delete(comentario)
    db.commit()
    return {"ok": True}