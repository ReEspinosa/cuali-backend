"""
Metodologias sociocriticas sugeridas por la SEP para cada campo formativo y
sus fases. La SEP las SUGIERE (no las impone), asi que la docente puede
elegir otra; por eso el campo solo define la metodologia por defecto.

Las fases salen de los libros de Proyectos de la NEM; el modelo no las
inventa ni las renombra: el codigo decide que fase corresponde a cada
sesion y el modelo solo redacta las actividades de esa fase.
"""

from dataclasses import dataclass
from datetime import date, timedelta

from app.services.planeacion_nem.catalogo import normalizar


@dataclass(frozen=True)
class Metodologia:
    id: str
    nombre: str
    fases: tuple[str, ...]


METODOLOGIAS: dict[str, Metodologia] = {
    "proyectos_comunitarios": Metodologia(
        id="proyectos_comunitarios",
        nombre="Aprendizaje basado en proyectos comunitarios",
        fases=(
            "Fase 1. Planeación: Identificación",
            "Fase 1. Planeación: Recuperación",
            "Fase 1. Planeación: Planificación",
            "Fase 2. Acción: Acercamiento",
            "Fase 2. Acción: Comprensión y producción",
            "Fase 2. Acción: Reconocimiento",
            "Fase 2. Acción: Concreción",
            "Fase 3. Intervención: Integración",
            "Fase 3. Intervención: Difusión",
            "Fase 3. Intervención: Consideraciones",
            "Fase 3. Intervención: Avances",
        ),
    ),
    "indagacion_steam": Metodologia(
        id="indagacion_steam",
        nombre="Aprendizaje basado en indagación (enfoque STEAM)",
        fases=(
            "Fase 1. Introducción al tema",
            "Fase 2. Diseño de la investigación",
            "Fase 3. Organizar y estructurar las respuestas",
            "Fase 4. Presentación de resultados",
            "Fase 5. Metacognición",
        ),
    ),
    "abp": Metodologia(
        id="abp",
        nombre="Aprendizaje basado en problemas",
        fases=(
            "Momento 1. Presentemos",
            "Momento 2. Recolectemos",
            "Momento 3. Formulemos el problema",
            "Momento 4. Organicemos la experiencia",
            "Momento 5. Vivamos la experiencia",
            "Momento 6. Resultados y análisis",
        ),
    ),
    "aprendizaje_servicio": Metodologia(
        id="aprendizaje_servicio",
        nombre="Aprendizaje servicio",
        fases=(
            "Etapa 1. Punto de partida",
            "Etapa 2. Lo que sé y lo que quiero saber",
            "Etapa 3. Organicemos las actividades",
            "Etapa 4. Creatividad en marcha",
            "Etapa 5. Compartimos y evaluamos lo aprendido",
        ),
    ),
}

METODOLOGIA_SUGERIDA_POR_CAMPO = {
    normalizar("Lenguajes"): "proyectos_comunitarios",
    normalizar("Saberes y Pensamiento Científico"): "indagacion_steam",
    normalizar("Ética, Naturaleza y Sociedades"): "abp",
    normalizar("De lo Humano y lo Comunitario"): "aprendizaje_servicio",
}

DIAS_SEMANA = ("Lunes", "Martes", "Miércoles", "Jueves", "Viernes")


def metodologia_sugerida(campo: str) -> str:
    return METODOLOGIA_SUGERIDA_POR_CAMPO.get(normalizar(campo), "proyectos_comunitarios")


def repartir_fases(metodologia_id: str, sesiones: int) -> list[list[str]]:
    """
    Reparte las F fases de la metodologia entre S sesiones, en orden y sin
    saltarse ninguna:
      - Si S >= F, cada fase recibe al menos una sesion y las sobrantes
        continuan la fase anterior.
      - Si S < F, una sesion agrupa varias fases consecutivas.

    La sesion i cubre las fases [floor(i*F/S), max(inicio+1, floor((i+1)*F/S))).
    Tiempo O(F + S) y memoria O(F + S).
    """
    fases = METODOLOGIAS[metodologia_id].fases
    f, s = len(fases), max(1, sesiones)
    reparto: list[list[str]] = []
    for i in range(s):
        inicio = (i * f) // s
        fin = max(inicio + 1, ((i + 1) * f) // s)
        reparto.append(list(fases[inicio:fin]))
    return reparto


def etiquetas_sesiones(sesiones: int, usar_dias: bool) -> list[str]:
    """Lunes..Viernes cuando son dias de una semana; 'Sesión n' en otro caso."""
    if usar_dias and sesiones <= len(DIAS_SEMANA):
        return list(DIAS_SEMANA[:sesiones])
    return [f"Sesión {i + 1}" for i in range(sesiones)]


def minutos_por_momento(minutos: int) -> tuple[int, int, int]:
    """Inicio 20 %, desarrollo 60 %, cierre 20 %, redondeado a 5 min."""
    inicio = max(5, round(minutos * 0.2 / 5) * 5)
    cierre = max(5, round(minutos * 0.2 / 5) * 5)
    return inicio, max(5, minutos - inicio - cierre), cierre


_MESES = (
    "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
)


def dias_habiles(inicio: date, sesiones: int) -> list[date]:
    """Una sesion por dia habil (lunes a viernes) desde `inicio`. Si la
    fecha cae en fin de semana se recorre al lunes. O(S)."""
    dias: list[date] = []
    actual = inicio
    while len(dias) < sesiones:
        if actual.weekday() < 5:
            dias.append(actual)
        actual += timedelta(days=1)
    return dias


def etiquetas_con_fechas(inicio: date, sesiones: int) -> tuple[list[str], str]:
    """
    Regresa ("Martes 13 oct", ...) y el periodo ("Del 13 al 16 de octubre
    de 2026"). Asi el dia de cada sesion siempre coincide con el calendario.
    """
    dias = dias_habiles(inicio, sesiones)
    etiquetas = [f"{DIAS_SEMANA[d.weekday()]} {d.day} {_MESES[d.month - 1][:3]}" for d in dias]
    a, b = dias[0], dias[-1]
    if a == b:
        periodo = f"{a.day} de {_MESES[a.month - 1]} de {a.year}"
    elif (a.month, a.year) == (b.month, b.year):
        periodo = f"Del {a.day} al {b.day} de {_MESES[a.month - 1]} de {a.year}"
    elif a.year == b.year:
        periodo = f"Del {a.day} de {_MESES[a.month - 1]} al {b.day} de {_MESES[b.month - 1]} de {a.year}"
    else:
        periodo = f"Del {a.day} de {_MESES[a.month - 1]} de {a.year} al {b.day} de {_MESES[b.month - 1]} de {b.year}"
    return etiquetas, periodo
