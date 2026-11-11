"""
Integracion curricular: sugerir contenidos de OTROS campos formativos que se
pueden vincular con el proyecto.

La SEP plantea la integracion como algo deseable, no obligatorio; por eso
aqui solo se SUGIERE y la docente decide. Las sugerencias salen siempre del
catalogo oficial: el modelo nunca inventa un contenido ni un PDA.

Como se ordenan:
  1. Si el indice de embeddings del RAG esta disponible, por similitud
     semantica (coseno) entre el proyecto y cada contenido del grado.
  2. Si no, por coincidencia de palabras ponderada con IDF (las palabras que
     aparecen en pocos contenidos pesan mas que "comunidad" o "alumnos").
En ambos casos se muestran las palabras en comun como razon.

COMPLEJIDAD: n contenidos del grado (~85) y t palabras por contenido.
  - Indices (IDF, raices y vectores del catalogo): O(n*t) una vez por proceso.
  - Cada sugerencia: O(n*t) lexico u O(n*d) semantico, y O(n log n) al ordenar.
"""

import logging
import math
import re
from functools import lru_cache

from app.services.planeacion_nem.catalogo import contenidos_del_grado, normalizar

logger = logging.getLogger("cuali.integracion")

MAX_VINCULOS = 3

_VACIAS = {
    "para", "como", "sobre", "entre", "desde", "hasta", "donde", "cuando", "tambien", "otros", "otras",
    "este", "esta", "estos", "estas", "esos", "esas", "cada", "todo", "toda", "todos", "todas", "mismo",
    "manera", "forma", "formas", "partir", "traves", "acuerdo", "algunos", "algunas", "diversos", "diversas",
    "distintos", "distintas", "diferentes", "propone", "identifica", "reconoce", "analiza", "describe",
    "explica", "valora", "reflexiona", "comprende", "utiliza", "emplea", "realiza", "participa", "comparte",
    "personas", "vida", "ante", "contenido", "proceso", "procesos", "situaciones", "cual", "cuales", "sus", "con",
}
# Puentes de vocabulario entre como habla la docente y como habla el programa.
_SINONIMOS = {
    "basura": "residuos desechos contaminacion medioambientales",
    "reciclar": "residuos materiales",
    "reciclaje": "residuos materiales",
    "limpieza": "salud higiene",
    "limpio": "salud higiene",
    "grafica": "datos",
    "encuesta": "datos",
    "cartel": "texto informativo",
    "carteles": "texto informativo",
    "campana": "texto informativo prevencion",
    "agua": "agua consumo",
    "plantas": "biodiversidad",
    "animales": "biodiversidad",
    "dinero": "economia",
    "pelea": "conflictos",
    "peleas": "conflictos",
}


def raices(texto: str) -> dict[str, str]:
    """Palabras de 4+ letras sin acentos, recortadas a 6 letras como raiz
    sencilla ('residuos' y 'residuo' coinciden). Regresa raiz -> palabra
    completa (para mostrarla como razon). O(L)."""
    t = normalizar(texto)
    palabras = re.findall(r"[a-zñ]{4,}", t)
    extra = " ".join(_SINONIMOS.get(p, "") for p in palabras)
    palabras += re.findall(r"[a-zñ]{4,}", extra)
    resultado: dict[str, str] = {}
    for p in palabras:
        if p not in _VACIAS:
            resultado.setdefault(p[:6], p)
    return resultado


@lru_cache(maxsize=8)
def _indice_lexico(grado: int) -> tuple[list[dict], list[dict[str, str]], dict[str, float]]:
    docs = contenidos_del_grado(grado)
    por_doc = [raices(d["contenido"] + " " + d["pda"]) for d in docs]
    df: dict[str, int] = {}
    for r in por_doc:
        for s in r:
            df[s] = df.get(s, 0) + 1
    n = max(1, len(docs))
    idf = {s: math.log((n + 1) / (c + 0.5)) for s, c in df.items()}
    return docs, por_doc, idf


@lru_cache(maxsize=8)
def _vectores_catalogo(grado: int) -> list[list[float]]:
    from app.services.rag.embeddings import embed_texts

    docs = contenidos_del_grado(grado)
    return embed_texts([d["contenido"] + " " + d["pda"] for d in docs], is_query=False)


def _puntajes_semanticos(grado: int, consulta: str) -> list[float]:
    """Coseno entre la consulta y cada contenido. Los vectores ya vienen
    normalizados, asi que el coseno es el producto punto. O(n*d)."""
    from app.services.rag.embeddings import embed_texts

    vectores = _vectores_catalogo(grado)
    q = embed_texts([consulta], is_query=True)[0]
    return [sum(a * b for a, b in zip(q, v)) for v in vectores]


def sugerir_vinculos(
    grado: int,
    campo_principal: str,
    texto_proyecto: str,
    excluir: set[str] | None = None,
    maximo: int = MAX_VINCULOS,
) -> list[dict]:
    """
    Regresa hasta `maximo` contenidos oficiales de campos distintos al
    principal, a lo mas uno por campo, del mas al menos relacionado.
    """
    docs, por_doc, idf = _indice_lexico(grado)
    if not docs:
        return []
    campo_n = normalizar(campo_principal)
    excluir = {normalizar(e) for e in (excluir or set())}
    consulta = raices(texto_proyecto)

    lexicos = []
    for r in por_doc:
        comunes = consulta.keys() & r.keys()
        lexicos.append((sum(idf[s] for s in comunes) / math.sqrt(len(r) or 1), comunes))

    try:
        semanticos = _puntajes_semanticos(grado, texto_proyecto)
    except Exception:
        logger.info("Sin embeddings para sugerir vinculos; se usa coincidencia de palabras.")
        semanticos = None

    candidatos = []
    for i, d in enumerate(docs):
        if normalizar(d["campo"]) == campo_n or normalizar(d["contenido"]) in excluir:
            continue
        lex, comunes = lexicos[i]
        puntaje = semanticos[i] if semanticos is not None else lex
        if semanticos is None and not comunes:
            continue
        candidatos.append((puntaje, lex, i, comunes))

    candidatos.sort(key=lambda c: (c[0], c[1]), reverse=True)

    elegidos, campos_usados = [], set()
    for puntaje, _, i, comunes in candidatos:
        d = docs[i]
        if d["campo"] in campos_usados:
            continue
        campos_usados.add(d["campo"])
        palabras = [por_doc[i][s] for s in sorted(comunes, key=lambda s: idf.get(s, 0), reverse=True)[:4]]
        elegidos.append({
            "campo": d["campo"],
            "contenido": d["contenido"],
            "razon": ("Coincide en: " + ", ".join(palabras)) if palabras else "",
        })
        if len(elegidos) >= maximo:
            break
    return elegidos


def procesos_relevantes(pdas: list[str], texto_proyecto: str, maximo: int = 2) -> list[str]:
    """
    De los procesos del PDA de un contenido vinculado, deja solo los que
    tienen que ver con el proyecto (mas palabras en comun), en su orden
    original. Si ninguno coincide, se deja el primero. O(p * t).
    """
    if len(pdas) <= maximo:
        return pdas
    consulta = raices(texto_proyecto)
    puntajes = [(len(consulta.keys() & raices(p).keys()), i) for i, p in enumerate(pdas)]
    mejores = sorted((x for x in puntajes if x[0] > 0), key=lambda x: (-x[0], x[1]))[:maximo]
    if not mejores:
        return pdas[:1]
    return [pdas[i] for _, i in sorted(mejores, key=lambda x: x[1])]
