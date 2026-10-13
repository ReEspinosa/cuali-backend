"""
Comunidad docente: publicaciones, comentarios y "me gusta".

Complejidad del feed (listar_publicaciones), para k publicaciones por pagina,
n publicaciones totales, L likes y C comentarios:

  Antes: 1 + 3k consultas (tres conteos por cada publicacion) mas k consultas
  para cargar al autor. Con k = 100 eran ~400 viajes a la base de datos.

  Ahora: 4 consultas fijas, sin importar k:
    1. Publicaciones de la pagina con su autor (JOIN):  O(log n + k)
       gracias al indice en created_at.
    2. Conteo de likes de esas k publicaciones:         O(k log L + l)
    3. Conteo de comentarios de esas k publicaciones:   O(k log C + c)
    4. Cuales de esas k le gustan al usuario:            O(k log L)
  donde l y c son los likes y comentarios que pertenecen a la pagina. Los
  pasos 2 a 4 usan los indices de publicacion_id. El armado en Python es
  O(k) con diccionarios.

  La busqueda de texto (ILIKE '%texto%') no puede usar un indice B-tree, asi
  que en el peor caso recorre las n publicaciones: O(n). Si la comunidad
  crece mucho, el siguiente paso es un indice trigram (pg_trgm) en Postgres.

Seguridad entre paginas: toda accion sobre una publicacion (verla,
comentarla, darle like, leer sus comentarios) pasa por
_publicacion_visible_o_404, que aplica la misma regla de visibilidad que el
feed. Antes, una publicacion "solo mi escuela" de otra escuela no aparecia
en el feed pero si se podia comentar o leer conociendo su id.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from app.core.deps import get_current_user
from app.core.firmas import url_firmada
from app.db.session import get_db
from app.models.comunidad import Comentario, Like, Publicacion
from app.models.user import User
from app.routers.archivos import compartir_imagenes_en_comunidad, nombre_desde_url

router = APIRouter(prefix="/comunidad", tags=["comunidad"])

CATEGORIAS_VALIDAS = {"Recursos", "Ideas", "Preguntas", "Celebraciones"}
VISIBILIDADES_VALIDAS = {"publico", "escuela"}

MAX_CONTENIDO_PUBLICACION = 5000
MAX_CONTENIDO_COMENTARIO = 2000
MAX_IMAGENES = 6
MAX_BUSQUEDA = 100
TAMANO_PAGINA_MAXIMO = 100


def _autor_dict(autor: User) -> dict:
    return {
        "id": autor.id,
        "nombre": f"{autor.nombre} {autor.apellido}",
        "escuela": autor.nombre_escuela,
    }


def _imagenes_firmadas(pub: Publicacion) -> list[str]:
    """Las imagenes se entregan con URL firmada para que <img src> funcione
    sin header de sesion. O(m) en el numero de imagenes."""
    firmadas = []
    for url in pub.imagenes:
        nombre = nombre_desde_url(url)
        if nombre:
            firmadas.append(url_firmada(nombre))
    return firmadas


def _publicacion_dict(
    pub: Publicacion,
    user_id: str,
    total_likes: int,
    le_gusta: bool,
    total_comentarios: int,
) -> dict:
    return {
        "id": pub.id,
        "autor": _autor_dict(pub.autor),
        "contenido": pub.contenido,
        "categoria": pub.categoria,
        "visibilidad": pub.visibilidad,
        "imagenes": _imagenes_firmadas(pub),
        "total_likes": total_likes,
        "le_gusta": le_gusta,
        "total_comentarios": total_comentarios,
        "created_at": pub.created_at,
        "es_autor": pub.autor_id == user_id,
    }


def _puede_ver(pub: Publicacion, user: User) -> bool:
    if pub.autor_id == user.id or pub.visibilidad == "publico":
        return True
    return (
        pub.visibilidad == "escuela"
        and bool(user.nombre_escuela)
        and pub.autor.nombre_escuela == user.nombre_escuela
    )


def _publicacion_visible_o_404(publicacion_id: str, user: User, db: Session) -> Publicacion:
    pub = (
        db.query(Publicacion)
        .options(joinedload(Publicacion.autor))
        .filter(Publicacion.id == publicacion_id)
        .first()
    )
    # 404 tambien cuando existe pero no la puede ver: no se confirma que exista.
    if not pub or not _puede_ver(pub, user):
        raise HTTPException(status_code=404, detail="Publicación no encontrada.")
    return pub


def _detalle_de_una(pub: Publicacion, user: User, db: Session) -> dict:
    """Mismo formato que el feed para una sola publicacion (3 consultas O(log n))."""
    total_likes = db.query(func.count(Like.id)).filter(Like.publicacion_id == pub.id).scalar() or 0
    le_gusta = (
        db.query(Like.id).filter(Like.publicacion_id == pub.id, Like.usuario_id == user.id).first()
        is not None
    )
    total_comentarios = (
        db.query(func.count(Comentario.id)).filter(Comentario.publicacion_id == pub.id).scalar() or 0
    )
    return _publicacion_dict(pub, user.id, total_likes, le_gusta, total_comentarios)


def _escapar_like(texto: str) -> str:
    """Escapa los comodines de LIKE para que '%' o '_' escritos por el
    usuario se busquen literalmente."""
    return texto.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _construir_arbol_comentarios(comentarios: list[Comentario], user_id: str) -> list[dict]:
    """Arma el arbol de respuestas en O(c): un diccionario por id y una sola
    pasada para colgar cada comentario de su padre."""
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
    busqueda: str | None = Query(None, max_length=MAX_BUSQUEDA),
    categoria: str | None = Query(None),
    alcance: str = Query("todo"),  # "todo" | "escuela"
    limite: int = Query(50, ge=1, le=TAMANO_PAGINA_MAXIMO),
    desplazamiento: int = Query(0, ge=0, le=10_000),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    query = (
        db.query(Publicacion)
        .join(User, Publicacion.autor_id == User.id)
        .options(joinedload(Publicacion.autor))
    )

    if alcance == "escuela" and user.nombre_escuela:
        query = query.filter(
            or_(Publicacion.visibilidad == "publico", User.nombre_escuela == user.nombre_escuela)
        )
    else:
        condiciones = [Publicacion.visibilidad == "publico", Publicacion.autor_id == user.id]
        if user.nombre_escuela:
            condiciones.append(
                (Publicacion.visibilidad == "escuela") & (User.nombre_escuela == user.nombre_escuela)
            )
        query = query.filter(or_(*condiciones))

    if categoria and categoria in CATEGORIAS_VALIDAS:
        query = query.filter(Publicacion.categoria == categoria)

    if busqueda:
        query = query.filter(Publicacion.contenido.ilike(f"%{_escapar_like(busqueda)}%", escape="\\"))

    publicaciones = (
        query.order_by(Publicacion.created_at.desc())
        .offset(desplazamiento)
        .limit(limite)
        .all()
    )
    if not publicaciones:
        return []

    ids = [p.id for p in publicaciones]

    likes_por_pub = dict(
        db.query(Like.publicacion_id, func.count(Like.id))
        .filter(Like.publicacion_id.in_(ids))
        .group_by(Like.publicacion_id)
        .all()
    )
    comentarios_por_pub = dict(
        db.query(Comentario.publicacion_id, func.count(Comentario.id))
        .filter(Comentario.publicacion_id.in_(ids))
        .group_by(Comentario.publicacion_id)
        .all()
    )
    mis_likes = {
        fila[0]
        for fila in db.query(Like.publicacion_id)
        .filter(Like.publicacion_id.in_(ids), Like.usuario_id == user.id)
        .all()
    }

    return [
        _publicacion_dict(
            p,
            user.id,
            total_likes=likes_por_pub.get(p.id, 0),
            le_gusta=p.id in mis_likes,
            total_comentarios=comentarios_por_pub.get(p.id, 0),
        )
        for p in publicaciones
    ]


class PublicacionCreate(BaseModel):
    contenido: str = Field(min_length=1, max_length=MAX_CONTENIDO_PUBLICACION)
    categoria: str | None = None
    visibilidad: str = "publico"
    imagenes: list[str] = Field(default_factory=list, max_length=MAX_IMAGENES)


@router.post("/publicaciones")
def crear_publicacion(
    payload: PublicacionCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if payload.visibilidad not in VISIBILIDADES_VALIDAS:
        raise HTTPException(status_code=400, detail="Visibilidad inválida.")
    if payload.categoria and payload.categoria not in CATEGORIAS_VALIDAS:
        raise HTTPException(status_code=400, detail=f"Categoría inválida. Opciones: {', '.join(CATEGORIAS_VALIDAS)}")
    if payload.visibilidad == "escuela" and not user.nombre_escuela:
        raise HTTPException(status_code=400, detail="Necesitas tener una escuela registrada en tu perfil para publicar solo a tu escuela.")

    imagenes = compartir_imagenes_en_comunidad(payload.imagenes, user, db)

    pub = Publicacion(
        autor_id=user.id,
        contenido=payload.contenido,
        categoria=payload.categoria,
        visibilidad=payload.visibilidad,
    )
    pub.imagenes = imagenes
    db.add(pub)
    db.commit()
    db.refresh(pub)
    return _publicacion_dict(pub, user.id, total_likes=0, le_gusta=False, total_comentarios=0)


class PublicacionUpdate(BaseModel):
    contenido: str | None = Field(None, min_length=1, max_length=MAX_CONTENIDO_PUBLICACION)
    categoria: str | None = None
    visibilidad: str | None = None
    imagenes: list[str] | None = Field(None, max_length=MAX_IMAGENES)


def _get_publicacion_propia_o_404(publicacion_id: str, user: User, db: Session) -> Publicacion:
    pub = _publicacion_visible_o_404(publicacion_id, user, db)
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
        if payload.visibilidad not in VISIBILIDADES_VALIDAS:
            raise HTTPException(status_code=400, detail="Visibilidad inválida.")
        if payload.visibilidad == "escuela" and not user.nombre_escuela:
            raise HTTPException(status_code=400, detail="Necesitas tener una escuela registrada en tu perfil para publicar solo a tu escuela.")
        pub.visibilidad = payload.visibilidad
    if payload.imagenes is not None:
        ya_publicadas = {n for n in (nombre_desde_url(u) for u in pub.imagenes) if n}
        pub.imagenes = compartir_imagenes_en_comunidad(payload.imagenes, user, db, ya_publicadas)

    db.commit()
    db.refresh(pub)
    return _detalle_de_una(pub, user, db)


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
    """
    Alterna el "me gusta" de forma segura ante peticiones simultaneas.

    Si llegan dos peticiones a la vez (doble clic), ambas pueden ver que no
    hay like e intentar insertarlo. La restriccion UNIQUE de la tabla hace
    que solo una inserte; la otra recibe IntegrityError y aqui se interpreta
    como "ya existe", en lugar de responder un error 500. El borrado es un
    DELETE directo, que es idempotente por si mismo.
    """
    _publicacion_visible_o_404(publicacion_id, user, db)

    borrados = (
        db.query(Like)
        .filter(Like.publicacion_id == publicacion_id, Like.usuario_id == user.id)
        .delete(synchronize_session=False)
    )
    if borrados:
        db.commit()
        dio_like = False
    else:
        db.add(Like(publicacion_id=publicacion_id, usuario_id=user.id))
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
        dio_like = True

    total = db.query(func.count(Like.id)).filter(Like.publicacion_id == publicacion_id).scalar() or 0
    return {"le_gusta": dio_like, "total_likes": total}


@router.get("/publicaciones/{publicacion_id}/comentarios")
def listar_comentarios(
    publicacion_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _publicacion_visible_o_404(publicacion_id, user, db)
    comentarios = (
        db.query(Comentario)
        .options(joinedload(Comentario.autor))
        .filter(Comentario.publicacion_id == publicacion_id)
        .order_by(Comentario.created_at.asc())
        .all()
    )
    return _construir_arbol_comentarios(comentarios, user.id)


class ComentarioCreate(BaseModel):
    contenido: str = Field(min_length=1, max_length=MAX_CONTENIDO_COMENTARIO)
    parent_id: str | None = None


@router.post("/publicaciones/{publicacion_id}/comentarios")
def crear_comentario(
    publicacion_id: str,
    payload: ComentarioCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _publicacion_visible_o_404(publicacion_id, user, db)

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


class ComentarioUpdate(BaseModel):
    contenido: str = Field(min_length=1, max_length=MAX_CONTENIDO_COMENTARIO)


@router.patch("/comentarios/{comentario_id}")
def editar_comentario(
    comentario_id: str,
    payload: ComentarioUpdate,
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
