"""
Catalogo oficial de contenidos y PDA por grado (Programa Sintetico SEP).

El frontend ya no trae su propia copia del catalogo: lo pide aqui, asi hay
una sola fuente de verdad y agregar un grado no requiere tocar el frontend.
"""

from fastapi import APIRouter, Depends, HTTPException, Response

from app.core.deps import get_current_user
from app.models.user import User
from app.services.planeacion_nem.catalogo import catalogo_por_campo, grados_disponibles

router = APIRouter(prefix="/catalogo", tags=["catalogo"])

# El catalogo solo cambia con un despliegue: el navegador puede guardarlo.
_CACHE = "private, max-age=3600"


@router.get("")
def grados(response: Response, user: User = Depends(get_current_user)):
    response.headers["Cache-Control"] = _CACHE
    return {"grados": list(grados_disponibles())}


@router.get("/{grado}")
def catalogo_del_grado(grado: int, response: Response, user: User = Depends(get_current_user)):
    if grado not in grados_disponibles():
        raise HTTPException(status_code=404, detail="Todavía no hay catálogo para ese grado.")
    response.headers["Cache-Control"] = _CACHE
    return {"grado": grado, "campos_formativos": catalogo_por_campo(grado)}
