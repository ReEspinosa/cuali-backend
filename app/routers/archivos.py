import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile

from app.core.deps import get_current_user
from app.models.user import User
from app.services.extraccion_archivos import extraer_texto

router = APIRouter(prefix="/archivos", tags=["archivos"])

UPLOAD_DIR = Path("uploads")
UPLOAD_DIR.mkdir(exist_ok=True)

# TODO: en producción, guarda esto en almacenamiento real (S3, Google Cloud
# Storage, etc.) en vez del disco local del servidor.
EXTENSIONES_IMAGEN = {".png", ".jpg", ".jpeg"}
EXTENSIONES_DOCUMENTO = {".pdf", ".doc", ".docx"}
EXTENSIONES_PERMITIDAS = EXTENSIONES_IMAGEN | EXTENSIONES_DOCUMENTO

TAMANO_MAXIMO_MB = 15


@router.post("")
async def subir_archivo(file: UploadFile, user: User = Depends(get_current_user)):
    extension = Path(file.filename or "").suffix.lower()

    if extension not in EXTENSIONES_PERMITIDAS:
        raise HTTPException(
            status_code=400,
            detail="Tipo de archivo no permitido. Solo se aceptan PNG, JPEG, PDF y Word (.doc/.docx).",
        )

    contenido = await file.read()

    if len(contenido) > TAMANO_MAXIMO_MB * 1024 * 1024:
        raise HTTPException(
            status_code=400,
            detail=f"El archivo supera el límite de {TAMANO_MAXIMO_MB} MB.",
        )

    nombre_guardado = f"{uuid.uuid4()}{extension}"
    ruta = UPLOAD_DIR / nombre_guardado
    ruta.write_bytes(contenido)

    tipo = "imagen" if extension in EXTENSIONES_IMAGEN else "documento"
    texto_extraido = extraer_texto(ruta)

    return {
        "url": f"/uploads/{nombre_guardado}",
        "filename": file.filename,
        "tipo": tipo,
        "texto_extraido": texto_extraido,
    }