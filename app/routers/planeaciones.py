import json
import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import ValidationError
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.core.deps import get_current_user
from app.db.session import get_db
from app.models.planeacion import Mensaje, Planeacion
from app.models.user import User
from app.routers.archivos import validar_adjuntos_propios
from app.schemas.planeacion import (
    DocumentoPlaneacion,
    GenerarPlaneacionOut,
    MensajeCreate,
    MensajeOut,
    PlaneacionCreate,
    PlaneacionDetalle,
    PlaneacionOut,
)
from app.services.llm import generar_respuesta_chat
from app.services.planeacion_nem.catalogo import (
    CAMPOS_OFICIALES,
    EJES_ARTICULADORES,
    buscar_contenido,
    grado_tiene_catalogo,
    normalizar,
    pdas_oficiales_elegidos,
)
from app.services.planeacion_nem.documento_docx import guardar_docx
from app.services.planeacion_nem.generador import generar_documento
from app.services.planeacion_nem.integracion import sugerir_vinculos
from app.services.planeacion_nem.metodologias import metodologia_sugerida

router = APIRouter(prefix="/planeaciones", tags=["planeaciones"])
logger = logging.getLogger("cuali.planeaciones")

# Si un proceso murio a media generacion, el candado se libera solo despues
# de este tiempo para que la docente no se quede bloqueada para siempre.
CANDADO_GENERACION = timedelta(minutes=10)
_CAMPOS = {normalizar(c): c for c in CAMPOS_OFICIALES}


def _ahora() -> datetime:
    # Las columnas DateTime no guardan zona horaria (ni en SQLite ni en
    # Postgres sin timezone=True): se compara en UTC sin tzinfo.
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _get_planeacion_o_404(planeacion_id: str, user: User, db: Session) -> Planeacion:
    planeacion = (
        db.query(Planeacion)
        .filter(Planeacion.id == planeacion_id, Planeacion.owner_id == user.id)
        .first()
    )
    if not planeacion:
        raise HTTPException(status_code=404, detail="Planeación no encontrada.")
    return planeacion


@router.post("", response_model=PlaneacionOut, status_code=201)
def crear_planeacion(
    payload: PlaneacionCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    campo = _CAMPOS.get(normalizar(payload.campo_formativo))
    if not campo:
        raise HTTPException(status_code=422, detail="Campo formativo no reconocido.")

    contenido, pda = payload.contenido.strip(), payload.pda.strip()
    if grado_tiene_catalogo(payload.grado):
        oficial = buscar_contenido(payload.grado, campo, contenido)
        if not oficial:
            raise HTTPException(status_code=422, detail="El contenido no pertenece a ese campo formativo y grado.")
        contenido = oficial["contenido"]
        pda = "\n\n".join(pdas_oficiales_elegidos(oficial["pda"], pda))

    contexto = payload.contexto.model_dump(mode="json")
    # Los contenidos para integrar deben ser oficiales y de OTRO campo; lo
    # demas se descarta aqui y no llega nunca al modelo. O(k), k <= 3.
    vinculos, campos_vistos = [], {normalizar(campo)}
    for v in contexto["integracion"]["contenidos"]:
        oficial = buscar_contenido(payload.grado, v["campo"], v["contenido"])
        if not oficial or normalizar(oficial["campo"]) in campos_vistos:
            raise HTTPException(status_code=422, detail="Uno de los contenidos para integrar no es válido.")
        campos_vistos.add(normalizar(oficial["campo"]))
        vinculos.append({"campo": oficial["campo"], "contenido": oficial["contenido"]})
    contexto["integracion"]["contenidos"] = vinculos

    planeacion = Planeacion(
        owner_id=user.id,
        grado=payload.grado,
        campo_formativo=campo,
        contenido=contenido,
        pda=pda,
        grupo=payload.grupo.strip(),
        sesiones=payload.sesiones,
        tema=payload.tema.strip(),
        metodologia=contexto.pop("metodologia") or metodologia_sugerida(campo),
        contexto_json=json.dumps(contexto, ensure_ascii=False),
    )
    db.add(planeacion)
    db.commit()
    db.refresh(planeacion)
    return planeacion


@router.get("/vinculos/sugerencias")
def sugerencias_de_integracion(
    grado: int = Query(ge=1, le=6),
    campo: str = Query(max_length=80),
    contenido: str = Query(max_length=600),
    tema: str = Query("", max_length=1_000),
    user: User = Depends(get_current_user),
):
    """Contenidos de otros campos que se pueden integrar. No llama al LLM."""
    campo_oficial = _CAMPOS.get(normalizar(campo))
    if not campo_oficial or not grado_tiene_catalogo(grado):
        return []
    oficial = buscar_contenido(grado, campo_oficial, contenido)
    texto = " ".join(filter(None, [contenido, oficial["pda"] if oficial else "", tema]))
    return sugerir_vinculos(grado, campo_oficial, texto)


@router.get("", response_model=list[PlaneacionOut])
def listar_planeaciones(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return (
        db.query(Planeacion)
        .filter(Planeacion.owner_id == user.id)
        .order_by(Planeacion.updated_at.desc())
        .all()
    )


@router.get("/{planeacion_id}", response_model=PlaneacionDetalle)
def obtener_planeacion(
    planeacion_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return _get_planeacion_o_404(planeacion_id, user, db)


@router.post("/{planeacion_id}/mensajes", response_model=MensajeOut)
def enviar_mensaje(
    planeacion_id: str,
    payload: MensajeCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    planeacion = _get_planeacion_o_404(planeacion_id, user, db)
    validar_adjuntos_propios([a.url for a in payload.adjuntos], user, db)

    mensaje_usuario = Mensaje(planeacion_id=planeacion.id, role="user", content=payload.content)
    mensaje_usuario.adjuntos = [a.model_dump() for a in payload.adjuntos]
    db.add(mensaje_usuario)
    db.commit()
    db.refresh(mensaje_usuario)

    try:
        respuesta = generar_respuesta_chat(
            planeacion=planeacion,
            historial=planeacion.mensajes,
            nuevo_mensaje=payload.content,
            adjuntos_nuevos=mensaje_usuario.adjuntos,
        )
    except HTTPException:
        # El LLM no respondio: se deshace el mensaje para reintentar limpio.
        db.delete(mensaje_usuario)
        db.commit()
        raise

    mensaje_asistente = Mensaje(planeacion_id=planeacion.id, role="assistant", content=respuesta)
    db.add(mensaje_asistente)
    db.commit()
    db.refresh(mensaje_asistente)

    return mensaje_asistente


def _tomar_candado(planeacion: Planeacion, db: Session) -> str:
    """
    Compare-and-set atomico en la base: solo una peticion puede pasar a
    "generando". Si la docente da doble clic o abre dos pestanas, la segunda
    recibe 409 en vez de lanzar otra generacion (doble costo en el LLM y dos
    escrituras del mismo Word). Funciona igual con varios workers porque la
    condicion la evalua la base, no la memoria del proceso.
    """
    estado_previo = planeacion.status
    ahora = _ahora()
    filas = (
        db.query(Planeacion)
        .filter(
            Planeacion.id == planeacion.id,
            or_(Planeacion.status != "generando", Planeacion.updated_at < ahora - CANDADO_GENERACION),
        )
        .update({"status": "generando", "updated_at": ahora}, synchronize_session=False)
    )
    db.commit()
    if filas == 0:
        raise HTTPException(status_code=409, detail="Cuali ya está generando esta planeación. Espera a que termine.")
    return estado_previo if estado_previo != "generando" else "borrador"


def _indicaciones_del_chat(planeacion: Planeacion) -> list[str]:
    """Ultimos mensajes de la docente (si uso el chat), recortados. O(m)."""
    propios = [m.content.strip() for m in planeacion.mensajes if m.role == "user" and m.content.strip()]
    return [t[:600] for t in propios[-6:]]


@router.post("/{planeacion_id}/generar", response_model=GenerarPlaneacionOut)
def generar_planeacion_docx(
    planeacion_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    planeacion = _get_planeacion_o_404(planeacion_id, user, db)
    estado_previo = _tomar_candado(planeacion, db)

    try:
        contexto = json.loads(planeacion.contexto_json) if planeacion.contexto_json else {}
        documento = generar_documento(
            planeacion,
            contexto,
            docente=f"{user.nombre} {user.apellido}".strip(),
            escuela=user.nombre_escuela or "",
            indicaciones=_indicaciones_del_chat(planeacion),
        )
        try:
            documento = DocumentoPlaneacion(**documento).model_dump()
        except ValidationError:
            # Red de seguridad: si algo del modelo excede el esquema, la
            # docente ve un mensaje claro (502) y no un 500 sin explicacion.
            logger.exception("Documento generado fuera del esquema (planeacion %s)", planeacion.id)
            raise HTTPException(status_code=502, detail="Cuali no pudo armar la planeación esta vez. Intenta de nuevo.")
        docx_path = guardar_docx(planeacion.id, documento)
    except Exception:
        db.rollback()
        db.query(Planeacion).filter(Planeacion.id == planeacion.id).update(
            {"status": estado_previo}, synchronize_session=False
        )
        db.commit()
        raise

    db.query(Planeacion).filter(Planeacion.id == planeacion.id).update(
        {
            "documento_json": json.dumps(documento, ensure_ascii=False),
            "docx_path": docx_path,
            "status": "finalizada",
            "updated_at": _ahora(),
        },
        synchronize_session=False,
    )
    db.commit()
    return {"docx_url": f"/planeaciones/{planeacion.id}/docx", "documento": documento}


@router.get("/{planeacion_id}/documento", response_model=DocumentoPlaneacion)
def obtener_documento(
    planeacion_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    planeacion = _get_planeacion_o_404(planeacion_id, user, db)
    if not planeacion.documento_json:
        raise HTTPException(status_code=404, detail="Esta planeación todavía no tiene un documento generado.")
    return json.loads(planeacion.documento_json)


@router.put("/{planeacion_id}/documento", response_model=DocumentoPlaneacion)
def editar_documento(
    planeacion_id: str,
    payload: DocumentoPlaneacion,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    La docente edita el texto y se vuelve a exportar el Word sin llamar al
    modelo. Lo oficial (campo, contenido, PDA, metodologia, fases, numero de
    sesiones y fuentes) se toma del documento guardado, no del cliente.
    """
    planeacion = _get_planeacion_o_404(planeacion_id, user, db)
    if not planeacion.documento_json:
        raise HTTPException(status_code=404, detail="Esta planeación todavía no tiene un documento generado.")
    if planeacion.status == "generando":
        raise HTTPException(status_code=409, detail="Cuali está generando esta planeación. Espera a que termine.")

    guardado = json.loads(planeacion.documento_json)
    if len(payload.sesiones) != len(guardado["sesiones"]):
        raise HTTPException(status_code=422, detail="No se puede cambiar el número de sesiones al editar.")

    nuevo = payload.model_dump()
    for clave in ("grado", "fase", "campo", "contenido", "pdas", "metodologia", "metodologia_nombre", "fuentes", "version"):
        nuevo[clave] = guardado.get(clave)
    ids_fuentes = {f["id"] for f in guardado.get("fuentes", [])}
    for s_nueva, s_guardada in zip(nuevo["sesiones"], guardado["sesiones"]):
        s_nueva["fases"] = s_guardada["fases"]
        s_nueva["vinculos"] = s_guardada.get("vinculos", [])
    # De la integracion solo se edita el texto de la actividad y la evidencia.
    guardados = guardado.get("integracion", [])
    if len(nuevo["integracion"]) != len(guardados):
        raise HTTPException(status_code=422, detail="No se pueden agregar o quitar vínculos al editar.")
    for v_nuevo, v_guardado in zip(nuevo["integracion"], guardados):
        for clave in ("campo", "contenido", "pdas", "sesion"):
            v_nuevo[clave] = v_guardado[clave]
        s_nueva["citas"] = [c for c in s_nueva["citas"] if c in ids_fuentes]
    elegidos = set(nuevo.get("ejes") or [])
    nuevo["ejes"] = [e for e in EJES_ARTICULADORES if e in elegidos]

    docx_path = guardar_docx(planeacion.id, nuevo)
    planeacion.documento_json = json.dumps(nuevo, ensure_ascii=False)
    planeacion.docx_path = docx_path
    db.commit()
    return nuevo


@router.get("/{planeacion_id}/docx")
def descargar_docx(
    planeacion_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    planeacion = _get_planeacion_o_404(planeacion_id, user, db)
    if not planeacion.docx_path:
        raise HTTPException(status_code=404, detail="Esta planeación todavía no tiene un documento generado.")

    return FileResponse(
        planeacion.docx_path,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        filename=f"planeacion_{planeacion.contenido[:30]}.docx",
    )

