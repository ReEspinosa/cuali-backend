from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.services.planeacion_nem.catalogo import EJES_ARTICULADORES
from app.services.planeacion_nem.metodologias import METODOLOGIAS


class VinculoIn(BaseModel):
    campo: str = Field(max_length=80)
    contenido: str = Field(max_length=600)


class IntegracionIn(BaseModel):
    """
    "ninguna": proyecto de un solo campo.
    "sugerida": Cuali propone contenidos de otros campos (si la docente no
                confirmo ninguno, se calculan al generar).
    "elegida": la docente eligio los contenidos.
    Los contenidos se validan contra el catalogo oficial en el router.
    """

    modo: Literal["ninguna", "sugerida", "elegida"] = "ninguna"
    contenidos: list[VinculoIn] = Field(default=[], max_length=3)


class ContextoIn(BaseModel):
    """
    Lo que la docente sabe de su grupo y que el modelo no puede adivinar.
    Todo es opcional para no estorbar a quien solo quiere un borrador, pero
    cada dato que llega se usa en el documento.
    """

    metodologia: str | None = Field(default=None, max_length=40)
    escenario: Literal["aula", "escuela", "comunidad"] = "aula"
    minutos_por_sesion: int = Field(default=50, ge=15, le=180)
    num_alumnos: int | None = Field(default=None, ge=1, le=60)
    problematica: str = Field(default="", max_length=2_000)
    recursos_disponibles: str = Field(default="", max_length=1_000)
    ajustes_razonables: str = Field(default="", max_length=1_000)
    ejes_articuladores: list[str] = Field(default=[], max_length=7)
    periodo: str = Field(default="", max_length=80)
    usar_dias_semana: bool = True
    # Con fecha de inicio, el codigo calcula el dia real de cada sesion
    # (lunes a viernes) y el periodo; ya no se escribe a mano.
    fecha_inicio: date | None = None
    integracion: IntegracionIn = IntegracionIn()

    @field_validator("metodologia")
    @classmethod
    def _metodologia_valida(cls, v: str | None) -> str | None:
        if v is not None and v not in METODOLOGIAS:
            raise ValueError("Metodología no reconocida.")
        return v

    @field_validator("ejes_articuladores")
    @classmethod
    def _ejes_validos(cls, v: list[str]) -> list[str]:
        validos = set(EJES_ARTICULADORES)
        desconocidos = [e for e in v if e not in validos]
        if desconocidos:
            raise ValueError("Eje articulador no reconocido.")
        # Sin repetidos y en el orden oficial. O(7).
        elegidos = set(v)
        return [e for e in EJES_ARTICULADORES if e in elegidos]


class PlaneacionCreate(BaseModel):
    grado: int = Field(ge=1, le=6)
    campo_formativo: str = Field(max_length=80)
    contenido: str = Field(max_length=600)
    pda: str = Field(max_length=4_000)
    grupo: str = Field(default="", max_length=20)
    sesiones: int = Field(ge=1, le=20)
    tema: str = Field(default="", max_length=1_000)
    contexto: ContextoIn = ContextoIn()


class PlaneacionOut(BaseModel):
    id: str
    grado: int
    campo_formativo: str
    contenido: str
    grupo: str
    sesiones: int
    tema: str
    status: str
    metodologia: str | None = None
    pdf_path: str | None
    docx_path: str | None
    created_at: datetime

    class Config:
        from_attributes = True


class AdjuntoIn(BaseModel):
    url: str = Field(max_length=500)
    filename: str = Field(max_length=255)
    tipo: str = Field(max_length=50)
    # Antes este campo no existia aqui y Pydantic lo descartaba en silencio:
    # el texto de los PDF/Word adjuntos nunca llegaba al LLM en el chat de
    # planeacion. Mismo tope que en el chat general.
    texto_extraido: str | None = Field(default=None, max_length=60_000)


class MensajeCreate(BaseModel):
    content: str = Field(min_length=1, max_length=8_000)
    adjuntos: list[AdjuntoIn] = Field(default=[], max_length=5)


class MensajeOut(BaseModel):
    id: str
    role: str
    content: str
    adjuntos: list[AdjuntoIn] = []
    created_at: datetime

    class Config:
        from_attributes = True


class PlaneacionDetalle(PlaneacionOut):
    mensajes: list[MensajeOut] = []


# --------------------------------------------------------------------------
# Documento estructurado de la planeacion (lo que se ve y se edita)
# --------------------------------------------------------------------------

_TXT = 3_000


class MomentoDoc(BaseModel):
    minutos: int = Field(ge=0, le=180)
    texto: str = Field(max_length=_TXT)


class SesionDoc(BaseModel):
    etiqueta: str = Field(max_length=40)
    fases: list[str] = Field(max_length=11)
    inicio: MomentoDoc
    desarrollo: MomentoDoc
    cierre: MomentoDoc
    recursos: list[str] = Field(default=[], max_length=12)
    tarea: str = Field(default="", max_length=600)
    citas: list[str] = Field(default=[], max_length=8)
    # Campos formativos con los que se vincula esta sesion (para marcarla).
    vinculos: list[str] = Field(default=[], max_length=3)


class VinculoDoc(BaseModel):
    campo: str = Field(max_length=80)
    contenido: str = Field(max_length=600)
    pdas: list[str] = Field(max_length=12)
    sesion: str = Field(max_length=40)
    actividad: str = Field(max_length=1_500)
    evidencia: str = Field(default="", max_length=600)
    # True si la sesion ya describe el vinculo con las mismas palabras: no
    # se repite debajo de ella, solo aparece en la tabla de integracion.
    en_sesion: bool = False


class RubricaFila(BaseModel):
    indicador: str = Field(max_length=300)
    sobresaliente: str = Field(max_length=600)
    satisfactorio: str = Field(max_length=600)
    en_proceso: str = Field(max_length=600)


class EvaluacionDoc(BaseModel):
    criterios: list[str] = Field(default=[], max_length=10)
    evidencias: list[str] = Field(default=[], max_length=10)
    instrumentos: list[str] = Field(default=[], max_length=6)


class FuenteDoc(BaseModel):
    id: str = Field(max_length=8)
    libro: str = Field(max_length=200)
    paginas: str = Field(default="", max_length=40)
    seccion: str = Field(default="", max_length=200)


class DocumentoPlaneacion(BaseModel):
    """
    Los campos oficiales (campo, contenido, PDA, metodologia y fases) no se
    aceptan del cliente al editar: el router los vuelve a poner desde la
    base de datos. Aqui solo se valida forma y tamano.
    """

    version: int = 1
    escuela: str = Field(default="", max_length=160)
    docente: str = Field(default="", max_length=160)
    periodo: str = Field(default="", max_length=80)
    grado: int
    grupo: str = Field(default="", max_length=20)
    fase: int | None = None
    campo: str = Field(max_length=80)
    metodologia: str = Field(max_length=40)
    metodologia_nombre: str = Field(max_length=120)
    tiempo: str = Field(max_length=120)
    escenario: str = Field(max_length=40)
    ejes: list[str] = Field(default=[], max_length=7)
    titulo_proyecto: str = Field(max_length=300)
    contenido: str = Field(max_length=600)
    pdas: list[str] = Field(max_length=12)
    intencion_didactica: str = Field(max_length=_TXT)
    proposito: str = Field(max_length=_TXT)
    sesiones: list[SesionDoc] = Field(max_length=20)
    evaluacion: EvaluacionDoc
    rubrica: list[RubricaFila] = Field(default=[], max_length=12)
    fuentes: list[FuenteDoc] = Field(default=[], max_length=8)
    ajustes_razonables: str = Field(default="", max_length=1_000)
    integracion: list[VinculoDoc] = Field(default=[], max_length=3)
    # Llamadas, reparaciones y problemas que detecto el codigo al generar.
    # Sirve para medir la calidad; no se muestra a la docente.
    metricas: dict = {}
    pendientes: list[str] = Field(default=[], max_length=40)


class GenerarPlaneacionOut(BaseModel):
    docx_url: str
    documento: DocumentoPlaneacion
