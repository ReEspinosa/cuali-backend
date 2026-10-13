"""
Subida y descarga de archivos de los docentes.

Reglas de acceso (se revisan en cada descarga, en este orden):
  1. URL firmada valida (ver app/core/firmas.py)  -> se sirve.
  2. Sesion y el usuario es el dueno               -> se sirve.
  3. Sesion y el archivo esta marcado "comunidad"  -> se sirve.
  4. Archivo antiguo sin registro de dueno (subido antes de esta version)
     y hay sesion                                   -> se sirve (transitorio).
  5. Cualquier otro caso                            -> 404.

Se responde 404 y no 403 cuando no hay permiso, para no confirmar a un
tercero que el archivo existe.

Complejidad:
  - Subida: O(s) en el tamano s del archivo (se lee una vez, por bloques).
  - Descarga: O(log n) para validar el permiso (busqueda por llave primaria)
    mas O(s) para enviar el archivo.
"""

import io
import logging
import re
import uuid
import zipfile
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.core.deps import get_current_user, get_current_user_opcional
from app.core.firmas import firma_valida, url_firmada
from app.db.session import get_db
from app.models.archivo import Archivo
from app.models.user import User
from app.services.extraccion_archivos import extraer_texto

logger = logging.getLogger("cuali.archivos")

router = APIRouter(prefix="/archivos", tags=["archivos"])
# Las URLs viejas guardadas en la base tienen la forma /uploads/<nombre>.
# Este router las atiende con exactamente las mismas reglas de acceso.
router_legacy = APIRouter(prefix="/uploads", tags=["archivos"])

UPLOAD_DIR = Path("uploads")
UPLOAD_DIR.mkdir(exist_ok=True)

EXTENSIONES_IMAGEN = {".png", ".jpg", ".jpeg"}
EXTENSIONES_DOCUMENTO = {".pdf", ".doc", ".docx"}
EXTENSIONES_PERMITIDAS = EXTENSIONES_IMAGEN | EXTENSIONES_DOCUMENTO

TAMANO_MAXIMO_MB = 15
_TAMANO_MAXIMO_BYTES = TAMANO_MAXIMO_MB * 1024 * 1024
_BLOQUE_LECTURA = 1024 * 1024

# Solo nombres que este servidor genera: uuid4 + extension permitida.
# Cualquier otra cosa (incluido "../") se rechaza antes de tocar el disco.
_NOMBRE_VALIDO = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\.(png|jpg|jpeg|pdf|doc|docx)$"
)

_MEDIA_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".pdf": "application/pdf",
    ".doc": "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}

# Firma ("numero magico") con la que empieza cada formato.
_FIRMAS = {
    ".png": (b"\x89PNG\r\n\x1a\n",),
    ".jpg": (b"\xff\xd8\xff",),
    ".jpeg": (b"\xff\xd8\xff",),
    ".pdf": (b"%PDF-",),
    ".doc": (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1",),
    ".docx": (b"PK\x03\x04",),
}


def _contenido_coincide_con_extension(contenido: bytes, extension: str) -> bool:
    """
    Revisa que los bytes reales correspondan a la extension declarada.
    Sin esto, alguien puede subir un script renombrado como .pdf.
    Un .docx es un zip: ademas de la firma, debe traer el documento de Word.
    Costo: O(1) para la firma; O(e) en el numero e de entradas del zip.
    """
    if not any(contenido.startswith(f) for f in _FIRMAS[extension]):
        return False
    if extension == ".docx":
        try:
            with zipfile.ZipFile(io.BytesIO(contenido)) as z:
                nombres = set(z.namelist())
        except zipfile.BadZipFile:
            return False
        return "[Content_Types].xml" in nombres and "word/document.xml" in nombres
    return True


async def _leer_con_limite(file: UploadFile) -> bytes:
    """Lee por bloques y corta en cuanto se pasa del limite, sin cargar de
    mas en memoria. O(s) en el tamano del archivo."""
    partes: list[bytes] = []
    total = 0
    while True:
        bloque = await file.read(_BLOQUE_LECTURA)
        if not bloque:
            break
        total += len(bloque)
        if total > _TAMANO_MAXIMO_BYTES:
            raise HTTPException(
                status_code=400,
                detail=f"El archivo supera el límite de {TAMANO_MAXIMO_MB} MB.",
            )
        partes.append(bloque)
    return b"".join(partes)


@router.post("")
async def subir_archivo(
    file: UploadFile,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    extension = Path(file.filename or "").suffix.lower()
    if extension not in EXTENSIONES_PERMITIDAS:
        raise HTTPException(
            status_code=400,
            detail="Tipo de archivo no permitido. Solo se aceptan PNG, JPEG, PDF y Word (.doc/.docx).",
        )

    contenido = await _leer_con_limite(file)
    if not _contenido_coincide_con_extension(contenido, extension):
        raise HTTPException(
            status_code=400,
            detail="El contenido del archivo no corresponde a su extensión.",
        )

    nombre_guardado = f"{uuid.uuid4()}{extension}"
    ruta = UPLOAD_DIR / nombre_guardado
    ruta.write_bytes(contenido)

    tipo = "imagen" if extension in EXTENSIONES_IMAGEN else "documento"
    db.add(
        Archivo(
            nombre_guardado=nombre_guardado,
            owner_id=user.id,
            filename_original=(file.filename or nombre_guardado)[:255],
            tipo=tipo,
        )
    )
    db.commit()

    return {
        "url": f"/archivos/{nombre_guardado}",
        "url_firmada": url_firmada(nombre_guardado),
        "filename": file.filename,
        "tipo": tipo,
        "texto_extraido": extraer_texto(ruta),
    }


def _servir(
    nombre: str,
    exp: int | None,
    firma: str | None,
    user: User | None,
    db: Session,
) -> FileResponse:
    no_encontrado = HTTPException(status_code=404, detail="Archivo no encontrado.")

    if not _NOMBRE_VALIDO.match(nombre):
        raise no_encontrado
    ruta = UPLOAD_DIR / nombre
    if not ruta.is_file():
        raise no_encontrado

    registro = db.get(Archivo, nombre)

    permitido = firma_valida(nombre, exp, firma)
    if not permitido and user is not None:
        if registro is None:
            # Archivo anterior a esta version: no sabemos su dueno.
            logger.info("Acceso a archivo sin registro de dueno: %s", nombre)
            permitido = True
        else:
            permitido = registro.owner_id == user.id or registro.visibilidad == "comunidad"

    if not permitido:
        raise no_encontrado

    extension = ruta.suffix.lower()
    es_imagen = extension in EXTENSIONES_IMAGEN
    nombre_descarga = registro.filename_original if registro else nombre
    return FileResponse(
        ruta,
        media_type=_MEDIA_TYPES[extension],
        filename=None if es_imagen else nombre_descarga,
        headers={
            # El navegador no debe "adivinar" otro tipo de contenido.
            "X-Content-Type-Options": "nosniff",
            # Solo el navegador del usuario puede guardarlo en cache, no
            # proxies intermedios.
            "Cache-Control": "private, max-age=600",
        },
    )


@router.get("/{nombre}")
def descargar_archivo(
    nombre: str,
    exp: int | None = Query(None),
    firma: str | None = Query(None),
    user: User | None = Depends(get_current_user_opcional),
    db: Session = Depends(get_db),
):
    return _servir(nombre, exp, firma, user, db)


@router_legacy.get("/{nombre}")
def descargar_archivo_legacy(
    nombre: str,
    exp: int | None = Query(None),
    firma: str | None = Query(None),
    user: User | None = Depends(get_current_user_opcional),
    db: Session = Depends(get_db),
):
    return _servir(nombre, exp, firma, user, db)


def nombre_desde_url(url: str) -> str | None:
    """Extrae '<uuid>.<ext>' de '/archivos/<nombre>' o '/uploads/<nombre>'
    (con o sin query string). O(len(url))."""
    ruta = url.split("?", 1)[0]
    for prefijo in ("/archivos/", "/uploads/"):
        if ruta.startswith(prefijo):
            nombre = ruta[len(prefijo):]
            return nombre if _NOMBRE_VALIDO.match(nombre) else None
    return None


def compartir_imagenes_en_comunidad(
    urls: list[str],
    user: User,
    db: Session,
    ya_publicadas: set[str] | None = None,
) -> list[str]:
    """
    Valida las imagenes de una publicacion y las marca como visibles para la
    comunidad. Solo se pueden publicar imagenes propias: sin esta revision,
    alguien podria "publicar" la URL de un archivo privado de otro docente y
    asi volverlo visible.

    Devuelve las URLs normalizadas a /archivos/<nombre> (sin firma).
    Complejidad: O(m log n) para m imagenes (una consulta por lote).
    """
    ya_publicadas = ya_publicadas or set()
    nombres: list[str] = []
    for url in urls:
        nombre = nombre_desde_url(url)
        if nombre is None:
            raise HTTPException(status_code=400, detail="Una de las imágenes no es válida.")
        nombres.append(nombre)

    registros = {
        a.nombre_guardado: a
        for a in db.query(Archivo).filter(Archivo.nombre_guardado.in_(nombres)).all()
    } if nombres else {}

    for nombre in nombres:
        registro = registros.get(nombre)
        if registro is None:
            # Imagen antigua sin dueno conocido: solo se acepta si ya estaba
            # en esta misma publicacion (caso de edicion).
            if nombre not in ya_publicadas:
                raise HTTPException(status_code=400, detail="Una de las imágenes no es válida.")
            continue
        if registro.owner_id != user.id:
            raise HTTPException(status_code=400, detail="Una de las imágenes no es válida.")
        if registro.tipo != "imagen":
            raise HTTPException(status_code=400, detail="Solo se pueden publicar imágenes.")
        registro.visibilidad = "comunidad"

    return [f"/archivos/{n}" for n in nombres]


def validar_adjuntos_propios(urls: list[str], user: User, db: Session) -> None:
    """
    Un mensaje solo puede adjuntar archivos que subio el mismo docente.
    Sin esta revision, alguien podria escribir en su mensaje la URL de un
    archivo privado ajeno y quedaria guardada como si fuera suya.
    Complejidad: O(m log n) para m adjuntos, en una sola consulta.
    """
    if not urls:
        return
    nombres = []
    for url in urls:
        nombre = nombre_desde_url(url)
        if nombre is None:
            raise HTTPException(status_code=400, detail="Uno de los archivos adjuntos no es válido.")
        nombres.append(nombre)
    propios = {
        fila[0]
        for fila in db.query(Archivo.nombre_guardado)
        .filter(Archivo.nombre_guardado.in_(nombres), Archivo.owner_id == user.id)
        .all()
    }
    if any(n not in propios for n in nombres):
        raise HTTPException(status_code=400, detail="Uno de los archivos adjuntos no es válido.")
