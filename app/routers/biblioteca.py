"""
Libros de texto SEP y documentos de docentes.

/libros            Catalogo de libros por grado (sale del indice del RAG).
/libros/buscar     Busqueda en los libros: regresa libro, paginas y fragmento.
                   No llama al LLM: solo embeddings + BM25 sobre el indice.
/documentos        Material que suben los docentes, privado o compartido.

SEGURIDAD:
  - Solo se puede registrar un archivo propio (mismo dueno en `archivos`).
  - Compartir con la comunidad exige aceptar que el material es propio o con
    permiso y que no trae datos de alumnos.
  - Editar o borrar un documento ajeno responde 404, no 403.
  - Los archivos compartidos se ven con URL firmada que caduca en una hora.

COMPLEJIDAD:
  - Catalogo: O(L) una vez por proceso (lru_cache), O(1) despues.
  - Listados: una consulta con indice + LIMIT, O(log n + k).
"""

import json
from functools import lru_cache

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from app.core.deps import get_current_user
from app.core.firmas import url_firmada
from app.core.rate_limit import limiter
from app.db.session import get_db
from app.models.archivo import Archivo
from app.models.documento import DocumentoDocente
from app.models.user import User
from app.routers.archivos import nombre_desde_url
from app.services.planeacion_nem.catalogo import CAMPOS_OFICIALES

router_libros = APIRouter(prefix="/libros", tags=["biblioteca"])
router_documentos = APIRouter(prefix="/documentos", tags=["biblioteca"])

BUSQUEDAS_POR_MINUTO = 30
MAX_FRAGMENTO = 600


def _escapar_like(texto: str) -> str:
    return texto.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


# --------------------------------------------------------------------------
# Libros SEP
# --------------------------------------------------------------------------

@lru_cache(maxsize=1)
def _catalogo_libros() -> list[dict]:
    from app.services.rag import config

    with open(config.CATALOG_PATH, encoding="utf-8") as f:
        datos = json.load(f)
    vistos: set[tuple[str, str]] = set()
    libros = []
    for info in datos.values():
        llave = (str(info.get("grado", "")), info.get("libro", ""))
        if llave in vistos or not llave[1]:
            continue
        vistos.add(llave)
        libros.append({
            "grado": int(llave[0]) if llave[0].isdigit() else None,
            "materia": info.get("materia", ""),
            "libro": info.get("libro", ""),
        })
    libros.sort(key=lambda l: (l["grado"] or 0, l["materia"]))
    return libros


@router_libros.get("")
def listar_libros(grado: int | None = Query(None, ge=1, le=6), user: User = Depends(get_current_user)):
    libros = _catalogo_libros()
    return [l for l in libros if grado is None or l["grado"] == grado]


def _buscar_en_indice(consulta: str, grado: str | None) -> list[dict]:
    # Import diferido: chromadb y el modelo de embeddings solo se cargan
    # cuando alguien busca, no al arrancar el servidor.
    from app.services.rag.retrieval import hybrid_search

    return hybrid_search(consulta, grado=grado)


@router_libros.get("/buscar")
def buscar_en_libros(
    q: str = Query(min_length=3, max_length=300),
    grado: int | None = Query(None, ge=1, le=6),
    user: User = Depends(get_current_user),
):
    # Los embeddings corren en CPU: se limita por docente para que nadie
    # sature el servidor con busquedas en bucle.
    limiter.check(
        key=f"libros:buscar:{user.id}",
        max_hits=BUSQUEDAS_POR_MINUTO,
        window_seconds=60,
        error_detail="Hiciste muchas búsquedas seguidas. Espera un momento.",
    )
    try:
        chunks = _buscar_en_indice(q.strip(), grado=str(grado) if grado else None)
    except Exception:
        raise HTTPException(status_code=503, detail="La búsqueda en los libros no está disponible en este momento.")

    resultados = []
    for c in chunks:
        ini, fin = c.get("page_start"), c.get("page_end")
        texto = " ".join(str(c.get("text", "")).split())
        resultados.append({
            "libro": c.get("libro", ""),
            "materia": c.get("materia", ""),
            "grado": int(c["grado"]) if str(c.get("grado", "")).isdigit() else None,
            "paginas": "" if ini is None else (f"{ini}" if fin in (None, ini) else f"{ini}-{fin}"),
            "seccion": c.get("section_title", ""),
            "fragmento": texto[:MAX_FRAGMENTO] + ("..." if len(texto) > MAX_FRAGMENTO else ""),
        })
    return resultados


# --------------------------------------------------------------------------
# Documentos de docentes
# --------------------------------------------------------------------------

class DocumentoCreate(BaseModel):
    url: str = Field(max_length=500)
    titulo: str = Field(min_length=3, max_length=120)
    campo: str | None = Field(default=None, max_length=80)
    grado: int | None = Field(default=None, ge=1, le=6)
    visibilidad: str = Field(default="privado", pattern="^(privado|comunidad)$")
    acepto_condiciones: bool = False


class DocumentoUpdate(BaseModel):
    titulo: str | None = Field(default=None, min_length=3, max_length=120)
    visibilidad: str | None = Field(default=None, pattern="^(privado|comunidad)$")
    acepto_condiciones: bool = False


def _validar_campo(campo: str | None) -> str | None:
    if campo is None or campo == "":
        return None
    if campo not in CAMPOS_OFICIALES:
        raise HTTPException(status_code=422, detail="Campo formativo no reconocido.")
    return campo


def _exigir_condiciones(visibilidad: str | None, acepto: bool) -> None:
    if visibilidad == "comunidad" and not acepto:
        raise HTTPException(
            status_code=422,
            detail="Para compartir con la comunidad confirma que el material es tuyo o tienes permiso, y que no incluye datos de tus alumnos.",
        )


def _salida(doc: DocumentoDocente, user_id: str) -> dict:
    archivo = doc.archivo
    return {
        "id": doc.id,
        "titulo": doc.titulo,
        "campo": doc.campo,
        "grado": doc.grado,
        "visibilidad": doc.visibilidad,
        "tipo": archivo.tipo if archivo else "documento",
        "filename": archivo.filename_original if archivo else "",
        "url_firmada": url_firmada(doc.archivo_nombre),
        "autor": f"{doc.owner.nombre} {doc.owner.apellido}".strip() if doc.owner else "",
        "es_mio": doc.owner_id == user_id,
        "created_at": doc.created_at,
    }


def _propio_o_404(documento_id: str, user: User, db: Session) -> DocumentoDocente:
    doc = (
        db.query(DocumentoDocente)
        .filter(DocumentoDocente.id == documento_id, DocumentoDocente.owner_id == user.id)
        .first()
    )
    if not doc:
        raise HTTPException(status_code=404, detail="Documento no encontrado.")
    return doc


@router_documentos.post("", status_code=201)
def crear_documento(payload: DocumentoCreate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    _exigir_condiciones(payload.visibilidad, payload.acepto_condiciones)
    campo = _validar_campo(payload.campo)

    nombre = nombre_desde_url(payload.url)
    archivo = db.get(Archivo, nombre) if nombre else None
    if archivo is None or archivo.owner_id != user.id:
        # Mismo mensaje si no existe o es ajeno: no se confirma nada a terceros.
        raise HTTPException(status_code=400, detail="El archivo no es válido.")

    doc = DocumentoDocente(
        owner_id=user.id,
        archivo_nombre=nombre,
        titulo=payload.titulo.strip(),
        campo=campo,
        grado=payload.grado,
        visibilidad=payload.visibilidad,
    )
    db.add(doc)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Ese archivo ya está en tu biblioteca.")
    db.refresh(doc)
    return _salida(doc, user.id)


@router_documentos.get("/mios")
def mis_documentos(
    limite: int = Query(50, ge=1, le=100),
    desplazamiento: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    docs = (
        db.query(DocumentoDocente)
        .options(joinedload(DocumentoDocente.archivo), joinedload(DocumentoDocente.owner))
        .filter(DocumentoDocente.owner_id == user.id)
        .order_by(DocumentoDocente.created_at.desc())
        .offset(desplazamiento)
        .limit(limite)
        .all()
    )
    return [_salida(d, user.id) for d in docs]


@router_documentos.get("/comunidad")
def documentos_de_la_comunidad(
    campo: str | None = Query(None, max_length=80),
    grado: int | None = Query(None, ge=1, le=6),
    q: str | None = Query(None, max_length=100),
    limite: int = Query(30, ge=1, le=100),
    desplazamiento: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    # joinedload trae archivo y autor en la misma consulta: sin N+1.
    consulta = (
        db.query(DocumentoDocente)
        .options(joinedload(DocumentoDocente.archivo), joinedload(DocumentoDocente.owner))
        .filter(DocumentoDocente.visibilidad == "comunidad", DocumentoDocente.owner_id != user.id)
    )
    if campo:
        consulta = consulta.filter(DocumentoDocente.campo == _validar_campo(campo))
    if grado:
        consulta = consulta.filter(DocumentoDocente.grado == grado)
    if q and q.strip():
        consulta = consulta.filter(DocumentoDocente.titulo.ilike(f"%{_escapar_like(q.strip())}%", escape="\\"))
    docs = consulta.order_by(DocumentoDocente.created_at.desc()).offset(desplazamiento).limit(limite).all()
    return [_salida(d, user.id) for d in docs]


@router_documentos.patch("/{documento_id}")
def editar_documento(
    documento_id: str,
    payload: DocumentoUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    doc = _propio_o_404(documento_id, user, db)
    _exigir_condiciones(payload.visibilidad, payload.acepto_condiciones)
    if payload.titulo is not None:
        doc.titulo = payload.titulo.strip()
    if payload.visibilidad is not None:
        doc.visibilidad = payload.visibilidad
    db.commit()
    db.refresh(doc)
    return _salida(doc, user.id)


@router_documentos.delete("/{documento_id}", status_code=204)
def borrar_documento(documento_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    doc = _propio_o_404(documento_id, user, db)
    db.delete(doc)
    db.commit()
