"""
Catalogo oficial de contenidos y PDA (Programa Sintetico SEP).

Regla del proyecto: lo oficial lo pone el codigo, no el modelo. El frontend
manda el contenido elegido, pero el texto del PDA que llega al documento se
toma SIEMPRE de este catalogo; asi un cliente modificado no puede colar un
PDA inventado y el modelo nunca lo parafrasea.

GRADOS:
Cada archivo app/data/nem/*.json es una fase del Programa Sintetico. Un
contenido trae su PDA por grado en llaves "pda_1" ... "pda_6"; un grado
queda disponible en cuanto exista algun "pda_<grado>". Para agregar grados
basta con poner el JSON de la fase (ver scripts/extraer_programa_sintetico.py).

COMPLEJIDAD:
Los JSON se leen una sola vez por proceso (lru_cache) y se indexan en un
dict con llave (grado, campo, contenido). Cada consulta es O(1) promedio;
la construccion del indice es O(n) con n = total de contenidos.
"""

import json
import re
import unicodedata
from functools import lru_cache
from pathlib import Path

_DIR = Path(__file__).resolve().parents[2] / "data" / "nem"


CAMPOS_OFICIALES = (
    "Lenguajes",
    "Saberes y Pensamiento Científico",
    "Ética, Naturaleza y Sociedades",
    "De lo Humano y lo Comunitario",
)

EJES_ARTICULADORES = (
    "Inclusión",
    "Pensamiento crítico",
    "Interculturalidad crítica",
    "Igualdad de género",
    "Vida saludable",
    "Apropiación de las culturas a través de la lectura y la escritura",
    "Artes y experiencias estéticas",
)


def normalizar(texto: str) -> str:
    """Minusculas, sin acentos y con espacios colapsados. O(L)."""
    sin_acentos = unicodedata.normalize("NFD", texto or "")
    sin_acentos = "".join(c for c in sin_acentos if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", sin_acentos).strip().lower()


_GRADOS = range(1, 7)


@lru_cache(maxsize=1)
def _indice() -> dict[tuple[int, str, str], dict]:
    indice: dict[tuple[int, str, str], dict] = {}
    for archivo in sorted(_DIR.glob("*.json")):
        datos = json.loads(archivo.read_text(encoding="utf-8"))
        fase = datos.get("fase")
        for campo in datos.get("campos_formativos", []):
            nombre_campo = campo["campo_formativo"]
            for item in campo["contenidos"]:
                for grado in _GRADOS:
                    pda = (item.get(f"pda_{grado}") or "").strip()
                    if not pda:
                        continue
                    llave = (grado, normalizar(nombre_campo), normalizar(item["contenido"]))
                    # Si dos archivos traen el mismo grado, gana el primero.
                    indice.setdefault(llave, {
                        "fase": fase,
                        "campo": nombre_campo,
                        "contenido": item["contenido"],
                        "pda": pda,
                    })
    return indice


@lru_cache(maxsize=1)
def grados_disponibles() -> tuple[int, ...]:
    return tuple(sorted({g for (g, _, _) in _indice()}))


def grado_tiene_catalogo(grado: int) -> bool:
    return grado in grados_disponibles()


def buscar_contenido(grado: int, campo: str, contenido: str) -> dict | None:
    """Regresa el registro oficial o None si no existe. O(L) por normalizar."""
    return _indice().get((grado, normalizar(campo), normalizar(contenido)))


def separar_pdas(texto: str) -> list[str]:
    """
    El catalogo separa los procesos de un mismo contenido con lineas en
    blanco. Si no las hay (un solo bloque), se separa por oraciones para que
    cada rasgo del PDA pueda evaluarse por separado. O(L).
    """
    bloques = [b.strip() for b in re.split(r"\n\s*\n", texto or "") if b.strip()]
    if len(bloques) > 1:
        return bloques
    oraciones = re.split(r"(?<=[.;])\s+(?=[A-ZÁÉÍÓÚÑ])", (texto or "").strip())
    return [o.strip() for o in oraciones if o.strip()]


def pdas_oficiales_elegidos(pda_oficial: str, pda_enviado: str) -> list[str]:
    """
    La docente puede desmarcar procesos del PDA. Se conservan solo los
    procesos oficiales que aparecen en lo que mando el cliente; si el cliente
    mando algo que no coincide con ninguno, se usa el PDA oficial completo.

    Se compara contra un set de procesos normalizados: O(p + q) en vez de
    O(p * q).
    """
    oficiales = separar_pdas(pda_oficial)
    # Se parte lo enviado igual que lo oficial: si el oficial viene en
    # bloques, cada bloque enviado se compara entero; si no, por oraciones.
    por_bloques = len([b for b in re.split(r"\n\s*\n", pda_oficial or "") if b.strip()]) > 1
    if por_bloques:
        partes = re.split(r"\n\s*\n", pda_enviado or "")
    else:
        partes = separar_pdas(pda_enviado)
    enviados = {normalizar(p) for p in partes if p.strip()}
    elegidos = [p for p in oficiales if normalizar(p) in enviados]
    return elegidos or oficiales


@lru_cache(maxsize=8)
def contenidos_del_grado(grado: int) -> list[dict]:
    """Todos los contenidos oficiales de un grado, en el orden del programa.
    O(n) la primera vez y O(1) despues (cache por grado)."""
    return [v for (g, _, _), v in _indice().items() if g == grado]


def catalogo_por_campo(grado: int) -> list[dict]:
    """Forma que usa el frontend: campos con sus contenidos y PDA. O(n)."""
    campos: dict[str, list[dict]] = {}
    for v in contenidos_del_grado(grado):
        campos.setdefault(v["campo"], []).append({"contenido": v["contenido"], "pda": v["pda"]})
    return [
        {"campo_formativo": c, "contenidos": campos[c]}
        for c in CAMPOS_OFICIALES if c in campos
    ]
