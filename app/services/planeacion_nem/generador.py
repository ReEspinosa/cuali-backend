"""
Generacion de la planeacion NEM.

Flujo (2 llamadas cortas al modelo, mas una de reparacion si hace falta):

  1. El CODIGO arma lo oficial: campo, contenido, PDA del catalogo,
     metodologia, fases de cada sesion y minutos de cada momento.
  2. El RAG trae fragmentos de los libros de la SEP; son las unicas paginas
     que el documento puede citar.
  3. Llamada A: encabezado (titulo, intencion, proposito, evaluacion,
     rubrica con una fila por cada rasgo del PDA).
  4. Llamada B: sesiones (inicio, desarrollo, cierre, recursos, tarea) con
     la fase que el codigo ya asigno.
  5. El CODIGO valida (cantidad de sesiones, campos vacios, restos de chat,
     emojis, paginas no consultadas). Si algo falla, UNA llamada de
     reparacion por parte con la lista exacta de problemas. Lo que siga mal
     se marca como pendiente para la docente; nunca se inventa.

Dos llamadas cortas en vez de una larga: gpt-oss razona antes de escribir y
en una sola respuesta larga se le acababan los tokens (respuesta vacia).

COMPLEJIDAD: todo el procesamiento propio es lineal en el tamano del texto
generado, O(L); las busquedas de paginas y citas usan sets, O(1) promedio.
"""

import json
import logging
import re
from datetime import date

from fastapi import HTTPException

from app.services.llm_client import chat_completion_json
from app.services.planeacion_nem.catalogo import (
    EJES_ARTICULADORES,
    buscar_contenido,
    normalizar,
    pdas_oficiales_elegidos,
    separar_pdas,
)
from app.services.planeacion_nem.integracion import MAX_VINCULOS, procesos_relevantes, raices, sugerir_vinculos
from app.services.planeacion_nem.metodologias import (
    METODOLOGIAS,
    etiquetas_con_fechas,
    etiquetas_sesiones,
    metodologia_sugerida,
    minutos_por_momento,
    repartir_fases,
)

logger = logging.getLogger("cuali.planeacion")

ESCENARIOS = {"aula": "Aula", "escuela": "Escuela", "comunidad": "Comunidad"}
MAX_FUENTES = 5

# --------------------------------------------------------------------------
# Limpieza y validacion (codigo, no modelo)
# --------------------------------------------------------------------------

_EMOJI = re.compile(
    "[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF️‍]"
)
# Restos tipicos de pegar una respuesta de chat en la planeacion.
_RESTOS_DE_CHAT = (
    "te gustaria", "¿te gustaria", "claro que si", "con gusto", "colega",
    "aqui tienes", "espero que te sirva", "como modelo de lenguaje",
)
_PAGINAS = re.compile(r"p[aá]gs?\.?\s*(\d{1,4})(?:\s*(?:-|a|y)\s*(\d{1,4}))?", re.IGNORECASE)


def limpiar(texto) -> str:
    """Quita emojis y espacios repetidos. O(L)."""
    if not isinstance(texto, str):
        return ""
    texto = _EMOJI.sub("", texto)
    # Error de concordancia frecuente del modelo: "cuánto agua" -> "cuánta agua".
    texto = re.sub(r"\b([Cc])uánto agua\b", r"\1uánta agua", texto)
    texto = re.sub(r"[ \t]+", " ", texto)
    return texto.strip()


_RELLENO = re.compile(r"[.…?¿{}\[\]_\-–—]{4,}")


def es_texto_util(texto: str, minimo_letras: int = 8) -> bool:
    """
    Detecta texto de relleno ("..", "Cal-? .. ...", "....??..}{") que a
    veces escribe el modelo cuando se queda sin ideas o sin tokens: pocas
    letras, mucha puntuacion seguida o puntos suspensivos al final. O(L).
    """
    if not texto:
        return False
    letras = sum(1 for c in texto if c.isalpha())
    if letras < minimo_letras or letras < 0.6 * len(texto.replace(" ", "")):
        return False
    if _RELLENO.search(texto) or texto.rstrip().endswith(("…", "...")):
        return False
    return True


def _lista_textos(valor, maximo: int) -> list[str]:
    if isinstance(valor, str):
        valor = [valor]
    if not isinstance(valor, list):
        return []
    return [t for t in (limpiar(v) for v in valor) if es_texto_util(t, 3)][:maximo]


def _sin_acentos(texto: str) -> str:
    tabla = str.maketrans("áéíóúÁÉÍÓÚ", "aeiouAEIOU")
    return texto.translate(tabla).lower()


def _tiene_restos_de_chat(texto: str) -> bool:
    t = _sin_acentos(texto)
    return any(r in t for r in _RESTOS_DE_CHAT)


def _paginas_mencionadas(texto: str) -> set[int]:
    paginas: set[int] = set()
    for m in _PAGINAS.finditer(texto):
        a = int(m.group(1))
        b = int(m.group(2)) if m.group(2) else a
        if b < a or b - a > 40:
            b = a
        paginas.update(range(a, b + 1))
    return paginas


_ID_FUENTE = re.compile(r"\[\s*(F\d{1,2})\s*\]")


def nombre_corto_libro(libro: str) -> str:
    """'Proyectos Escolares - 6° grado' -> 'Proyectos Escolares 6°'."""
    return re.sub(r"\s*-\s*(\d)\s*°\s*grado", r" \1°", libro).strip()


def resolver_citas(texto: str, fuentes: dict[str, dict]) -> str:
    """
    Cambia los ids internos ("[F1]") por el nombre del libro: la docente no
    sabe que es F1. Un id que no existe se quita. O(L).
    """
    def _sub(m):
        f = fuentes.get(m.group(1))
        return nombre_corto_libro(f["libro"]) if f else ""
    return re.sub(r"\s{2,}", " ", _ID_FUENTE.sub(_sub, texto)).strip()


def normalizar_recursos(recursos: list[str]) -> list[str]:
    """'CARTULINAS' -> 'Cartulinas' y sin repetidos (sin importar
    mayusculas). O(r)."""
    vistos: set[str] = set()
    salida = []
    for r in recursos:
        if r.isupper() and len(r) > 3:
            r = r.capitalize()
        elif r[:1].islower():
            r = r[:1].upper() + r[1:]
        llave = normalizar(r)
        if llave and llave not in vistos:
            vistos.add(llave)
            salida.append(r)
    return salida


# --------------------------------------------------------------------------
# RAG: unicas fuentes que se pueden citar
# --------------------------------------------------------------------------

def buscar_fuentes(consulta: str, grado: int) -> tuple[list[dict], str]:
    """
    Regresa (fuentes, contexto_para_el_modelo). Si el indice no esta
    disponible se sigue sin fuentes: el documento sale sin citas en vez de
    con citas inventadas.
    """
    try:
        from app.services.rag.retrieval import hybrid_search

        chunks = hybrid_search(consulta, grado=str(grado))
    except Exception:
        logger.exception("No se pudo consultar el RAG para la planeacion")
        return [], ""

    fuentes, partes = [], []
    for i, c in enumerate(chunks[:MAX_FUENTES], start=1):
        ini, fin = c.get("page_start"), c.get("page_end")
        paginas = "" if ini is None else (f"{ini}" if ini == fin or fin is None else f"{ini}-{fin}")
        fuente = {
            "id": f"F{i}",
            "libro": limpiar(str(c.get("libro", "")))[:200],
            "paginas": paginas,
            "seccion": limpiar(str(c.get("section_title", "")))[:200],
        }
        fuentes.append(fuente)
        texto = limpiar(str(c.get("text", "")))[:900]
        partes.append(f"[{fuente['id']}] {fuente['libro']}, pág. {paginas}, {fuente['seccion']}\n{texto}")
    return fuentes, "\n\n".join(partes)


def _paginas_de(fuente: dict) -> set[int]:
    return _paginas_mencionadas(f"pág. {fuente['paginas']}") if fuente.get("paginas") else set()


def _paginas_permitidas(fuentes: list[dict]) -> set[int]:
    permitidas: set[int] = set()
    for f in fuentes:
        permitidas |= _paginas_mencionadas(f"pág. {f['paginas']}") if f["paginas"] else set()
    return permitidas


# --------------------------------------------------------------------------
# Datos fijos (los pone el codigo)
# --------------------------------------------------------------------------

def armar_base(planeacion, contexto: dict, docente: str, escuela: str) -> dict:
    oficial = buscar_contenido(planeacion.grado, planeacion.campo_formativo, planeacion.contenido)
    if oficial:
        pdas = pdas_oficiales_elegidos(oficial["pda"], planeacion.pda)
        contenido, fase = oficial["contenido"], oficial["fase"]
    else:
        # Grado sin catalogo todavia: se usa lo que escribio la docente.
        pdas = [p.strip() for p in re.split(r"\n\s*\n", planeacion.pda) if p.strip()] or [planeacion.pda]
        contenido, fase = planeacion.contenido, None

    metodologia_id = planeacion.metodologia or metodologia_sugerida(planeacion.campo_formativo)
    metodologia = METODOLOGIAS[metodologia_id]
    minutos = int(contexto.get("minutos_por_sesion") or 50)
    ini, des, cie = minutos_por_momento(minutos)
    periodo = contexto.get("periodo", "")
    fecha_inicio = contexto.get("fecha_inicio")
    if fecha_inicio:
        # Con fecha, el codigo calcula los dias reales: nunca "lunes" si la
        # semana empieza en martes.
        inicio = date.fromisoformat(fecha_inicio) if isinstance(fecha_inicio, str) else fecha_inicio
        etiquetas, periodo = etiquetas_con_fechas(inicio, planeacion.sesiones)
    else:
        etiquetas = etiquetas_sesiones(planeacion.sesiones, bool(contexto.get("usar_dias_semana", True)))
    fases = repartir_fases(metodologia_id, planeacion.sesiones)

    plural = "sesión" if planeacion.sesiones == 1 else "sesiones"
    return {
        "version": 1,
        "escuela": escuela,
        "docente": docente,
        "periodo": periodo,
        "grado": planeacion.grado,
        "grupo": planeacion.grupo or "",
        "fase": fase,
        "campo": planeacion.campo_formativo,
        "metodologia": metodologia_id,
        "metodologia_nombre": metodologia.nombre,
        "tiempo": f"{planeacion.sesiones} {plural} de {minutos} min",
        "escenario": ESCENARIOS.get(contexto.get("escenario", "aula"), "Aula"),
        "ejes": list(contexto.get("ejes_articuladores") or []),
        "contenido": contenido,
        "pdas": pdas,
        "ajustes_razonables": contexto.get("ajustes_razonables", ""),
        "_plan_sesiones": [
            {"etiqueta": e, "fases": f, "minutos": (ini, des, cie)} for e, f in zip(etiquetas, fases)
        ],
    }


def _datos_para_modelo(base: dict, planeacion, contexto: dict, indicaciones: list[str]) -> str:
    lineas = [
        f"Grado: {base['grado']}° de primaria" + (f", grupo {base['grupo']}" if base["grupo"] else ""),
        f"Campo formativo: {base['campo']}",
        f"Contenido (oficial, no lo cambies): {base['contenido']}",
        "PDA (oficial, no lo cambies):",
        *[f"  {i + 1}. {p}" for i, p in enumerate(base["pdas"])],
        f"Metodología: {base['metodologia_nombre']}",
        f"Escenario: {base['escenario']}",
        f"Tiempo: {base['tiempo']}",
    ]
    if base["ejes"]:
        lineas.append("Ejes articuladores: " + ", ".join(base["ejes"]))
    if planeacion.tema:
        lineas.append(f"Tema o situación que quiere atender la docente: {planeacion.tema}")
    if contexto.get("problematica"):
        lineas.append(f"Problemática del grupo o la comunidad: {contexto['problematica']}")
    if contexto.get("num_alumnos"):
        lineas.append(f"Número de alumnos: {contexto['num_alumnos']}")
    if contexto.get("recursos_disponibles"):
        lineas.append(f"Recursos con los que SÍ cuenta (no asumas otros): {contexto['recursos_disponibles']}")
    else:
        lineas.append("Recursos: no los indicó; usa solo libros de texto, cuaderno, pizarrón y materiales comunes del aula.")
    if contexto.get("ajustes_razonables"):
        lineas.append(f"Ajustes razonables a considerar: {contexto['ajustes_razonables']}")
    if indicaciones:
        lineas.append("Indicaciones que dio la docente en el chat:")
        lineas.extend(f"  - {t}" for t in indicaciones)
    return "\n".join(lineas)


# --------------------------------------------------------------------------
# Llamadas al modelo
# --------------------------------------------------------------------------

_REGLAS_COMUNES = """Reglas:
- Responde SOLO con un objeto JSON válido, sin texto antes ni después.
- Escribe en español de México con acentos y ortografía correctos.
- No uses emojis. No escribas frases de chat ("¿Te gustaría...?", "Claro que sí", "colega").
- No cambies el contenido ni el PDA: son oficiales.
- No inventes páginas de libros. Solo puedes mencionar páginas de las fuentes [F1], [F2]... que se te dan.
- En el texto NO escribas los ids [F1], [F2]: escribe el nombre del libro y la página ("Proyectos Escolares 6°, pág. 39").
- No inventes documentos, reglamentos, normativas, datos ni materiales que no estén en los datos que se te dan.
- No inventes eventos (reuniones con padres, ferias, visitas, concursos) ni personas o instancias (dirección, comités, autoridades) a quienes se entreguen productos, si la docente no los mencionó.
- Escribe oraciones completas; nunca dejes textos a medias ni con "..." como relleno."""

_PROMPT_ENCABEZADO = """Eres Cuali, asistente de planeación didáctica de la Nueva Escuela Mexicana.
Redacta el encabezado de una planeación a partir de los datos fijos.

""" + _REGLAS_COMUNES + """
- "intencion_didactica": para la docente. Se define a partir del PDA: habilidades, actitudes y valores que se desarrollan y el beneficio para la comunidad. 3 a 5 oraciones.
- "proposito": dirigido a las y los alumnos, en primera persona del plural ("Leeremos...", "Investigaremos..."). Debe decir qué harán, con quiénes, cómo beneficiará a su entorno y qué aprenderán. 3 a 5 oraciones.
- "rubrica": EXACTAMENTE una fila por cada proceso del PDA, en el mismo orden.
- "ejes": solo si se te pide; de 1 a 3 ejes copiados TAL CUAL de la lista oficial que se te da.

JSON:
{
  "titulo_proyecto": "string breve y atractivo, distinto del contenido",
  "ejes": ["solo si se te pide"],
  "intencion_didactica": "string",
  "proposito": "string",
  "evaluacion": {
    "criterios": ["un criterio observable por cada proceso del PDA"],
    "evidencias": ["productos concretos que entregarán"],
    "instrumentos": ["rúbrica", "lista de cotejo", "..."]
  },
  "rubrica": [
    {"indicador": "string", "sobresaliente": "string", "satisfactorio": "string", "en_proceso": "string"}
  ]
}"""

_PROMPT_SESIONES = """Eres Cuali, asistente de planeación didáctica de la Nueva Escuela Mexicana.
Redacta las actividades de cada sesión. La fase de la metodología de cada sesión YA está decidida: respétala y haz que las actividades correspondan a esa fase.

""" + _REGLAS_COMUNES + """
- Redacta en primera persona como la docente que dirige la clase ("Les pediré...", "Leeremos...", "Organizaré comunidades de cuatro..."). Quienes analizan, practican y producen son las y los alumnos; tú guías ("Guiaré a cada comunidad para que analice...", no "analizaré las tablas").
- Si formas equipos o comunidades, que el número cuadre con el número de alumnos.
- Cada sesión tiene inicio, desarrollo y cierre; cada uno con actividades concretas (qué se hace, cómo y con qué), no objetivos generales.
- Las sesiones deben avanzar hacia el producto del proyecto; no repitas la misma actividad.
- Al menos una sesión con una actividad lúdica.
- Las sesiones son días seguidos: cada una parte de lo que ya se hizo en las anteriores. No hables de datos "de la semana" antes de que existan ni de lo que "harán durante la semana" en la última sesión.
- "recursos": solo los que la docente dijo tener, libros de texto, cuaderno y materiales comunes.
- "citas": ids de las fuentes [F1]... que usaste en esa sesión (puede ser lista vacía).
- Debe haber EXACTAMENTE {n} objetos en "sesiones", en el mismo orden.
- Si se te dan vínculos con otros campos formativos, incluye cada vínculo en el desarrollo de la sesión indicada, con tus propias palabras (no copies el texto del vínculo).
- Si hay ajustes razonables, en al menos una sesión escribe "Ajuste razonable:" seguido de qué harás concretamente en esa actividad.

JSON:
{
  "sesiones": [
    {"inicio": "string", "desarrollo": "string", "cierre": "string", "recursos": ["string"], "tarea": "string", "citas": ["F1"]}
  ]
}"""


_PROMPT_INTEGRACION = """Eres Cuali, asistente de planeación didáctica de la Nueva Escuela Mexicana.
El proyecto se integra con contenidos de otros campos formativos. Para cada vínculo, en el MISMO orden en que se te dan, redacta cómo se trabaja dentro del proyecto.

""" + _REGLAS_COMUNES + """
- "sesion": copia TAL CUAL una de las etiquetas de sesión que se te dan; elige la que mejor encaje con la fase.
- La actividad debe trabajar el PDA de ESE vínculo (si el PDA habla de reglamentos, la actividad produce un reglamento; no otra cosa).
- Cada vínculo deja un producto distinto, propio de su campo (por ejemplo, Saberes: tabla o gráfica de datos; Lenguajes: un texto). No repitas el producto de otro vínculo.
- El "indicador" de la rúbrica nombra la habilidad concreta del PDA ("Organiza datos de consumo en una tabla"), nunca "cumplimiento del PDA".
- "actividad": 2 o 3 oraciones como la docente que dirige al grupo ("Les pediré que...", "Organizaremos..."), concreta y conectada con el producto del proyecto (no una clase aparte). Quien hace la actividad son las y los alumnos.
- "evidencia": el producto que muestra ese aprendizaje.
- "rubrica": una fila para evaluar el PDA de ese vínculo.
- Debe haber EXACTAMENTE {n} objetos en "vinculos".

JSON:
{
  "vinculos": [
    {"campo": "copia TAL CUAL el campo formativo del vínculo", "sesion": "string", "actividad": "string", "evidencia": "string",
     "rubrica": {"indicador": "string", "sobresaliente": "string", "satisfactorio": "string", "en_proceso": "string"}}
  ]
}"""


# --------------------------------------------------------------------------
# Esquemas JSON (response_format json_schema): el servidor obliga al modelo a
# respetar la forma y, cuando lo soporta, el numero exacto de elementos.
# --------------------------------------------------------------------------

_TEXTO = {"type": "string"}


def _objeto(propiedades: dict) -> dict:
    return {"type": "object", "properties": propiedades, "required": list(propiedades), "additionalProperties": False}


def _arreglo(items: dict, minimo: int | None = None, maximo: int | None = None) -> dict:
    esquema = {"type": "array", "items": items}
    if minimo is not None:
        esquema["minItems"] = minimo
    if maximo is not None:
        esquema["maxItems"] = maximo
    return esquema


_FILA_RUBRICA = _objeto({k: _TEXTO for k in ("indicador", "sobresaliente", "satisfactorio", "en_proceso")})


def esquema_encabezado(n_pdas: int, pedir_ejes: bool) -> dict:
    propiedades = {
        "titulo_proyecto": _TEXTO,
        "intencion_didactica": _TEXTO,
        "proposito": _TEXTO,
        "evaluacion": _objeto({
            "criterios": _arreglo(_TEXTO, 1, 10),
            "evidencias": _arreglo(_TEXTO, 1, 10),
            "instrumentos": _arreglo(_TEXTO, 1, 6),
        }),
        "rubrica": _arreglo(_FILA_RUBRICA, n_pdas, n_pdas),
    }
    if pedir_ejes:
        propiedades["ejes"] = _arreglo({"type": "string", "enum": list(EJES_ARTICULADORES)}, 1, 3)
    return _objeto(propiedades)


def esquema_sesiones(n: int) -> dict:
    sesion = _objeto({
        "inicio": _TEXTO,
        "desarrollo": _TEXTO,
        "cierre": _TEXTO,
        "recursos": _arreglo(_TEXTO, 1, 12),
        "tarea": _TEXTO,
        "citas": _arreglo(_TEXTO, 0, 8),
    })
    return _objeto({"sesiones": _arreglo(sesion, n, n)})


def esquema_integracion(n: int, etiquetas: list[str]) -> dict:
    vinculo = _objeto({
        "campo": _TEXTO,
        "sesion": {"type": "string", "enum": etiquetas},
        "actividad": _TEXTO,
        "evidencia": _TEXTO,
        "rubrica": _FILA_RUBRICA,
    })
    return _objeto({"vinculos": _arreglo(vinculo, n, n)})


def _parsear_json(texto: str) -> dict:
    try:
        datos = json.loads(texto)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", texto or "", re.DOTALL)
        if not m:
            raise ValueError("sin JSON")
        datos = json.loads(m.group(0))
    if not isinstance(datos, dict):
        raise ValueError("JSON no es objeto")
    return datos


def _llamar(system: str, user: str, max_tokens: int, schema: dict | None = None, nombre: str = "respuesta") -> dict:
    crudo = chat_completion_json(
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        temperature=0.3,
        max_tokens=max_tokens,
        schema=schema,
        nombre=nombre,
    )
    try:
        return _parsear_json(crudo)
    except (ValueError, json.JSONDecodeError):
        return {}


def _reparar(
    system: str, user: str, previo: dict, problemas: list[str], max_tokens: int,
    schema: dict | None = None, nombre: str = "respuesta",
) -> dict:
    """Una sola llamada de reparacion con la lista exacta de problemas."""
    correccion = (
        user
        + "\n\nTu respuesta anterior fue:\n"
        + json.dumps(previo, ensure_ascii=False)[:6000]
        + "\n\nCorrige SOLO estos problemas y regresa el JSON completo corregido:\n"
        + "\n".join(f"- {p}" for p in problemas)
    )
    return _llamar(system, correccion, max_tokens, schema, nombre)


# --------------------------------------------------------------------------
# Normalizacion + validacion de cada parte
# --------------------------------------------------------------------------

# Topes iguales a los de DocumentoPlaneacion (app/schemas/planeacion.py):
# si el modelo escribe de mas, se recorta aqui y nunca truena la validacion.
_TOPES_RUBRICA = {"indicador": 300, "sobresaliente": 600, "satisfactorio": 600, "en_proceso": 600}


def _recortar(texto: str, tope: int) -> str:
    """Recorta en el ultimo espacio antes del tope para no partir palabras. O(L)."""
    if len(texto) <= tope:
        return texto
    corte = texto[:tope].rsplit(" ", 1)[0]
    return corte.rstrip(",;:") or texto[:tope]


def _indicador_corto(texto: str, tope: int = 300) -> str:
    """
    Un indicador largo suele ser el PDA copiado completo. Se deja su primera
    oracion (y no un corte a media frase como "a lo largo de la"). O(L).
    """
    texto = texto.strip()
    if len(texto) <= 160:
        return texto
    primera = re.split(r"(?<=\.)\s+", texto, maxsplit=1)[0].strip()
    if 20 <= len(primera) <= tope:
        return primera
    return _recortar(texto, tope)


def _fila_rubrica(fila: dict) -> dict:
    fila = {k: _recortar(limpiar(fila.get(k)), tope) for k, tope in _TOPES_RUBRICA.items()}
    fila["indicador"] = _indicador_corto(fila["indicador"])
    return fila


def normalizar_encabezado(
    datos: dict, n_pdas: int, permitidas: set[int], pedir_ejes: bool = False
) -> tuple[dict, list[str]]:
    problemas: list[str] = []
    ev = datos.get("evaluacion") if isinstance(datos.get("evaluacion"), dict) else {}
    enc = {
        "titulo_proyecto": limpiar(datos.get("titulo_proyecto"))[:300],
        "intencion_didactica": limpiar(datos.get("intencion_didactica"))[:3000],
        "proposito": limpiar(datos.get("proposito"))[:3000],
        "evaluacion": {
            "criterios": _lista_textos(ev.get("criterios"), 10),
            "evidencias": _lista_textos(ev.get("evidencias"), 10),
            "instrumentos": _lista_textos(ev.get("instrumentos"), 6),
        },
        "rubrica": [],
    }
    if pedir_ejes:
        # Solo ejes de la lista oficial, sin repetir, en el orden oficial. O(7).
        propuestos = {normalizar(e) for e in _lista_textos(datos.get("ejes"), 7)}
        enc["ejes"] = [e for e in EJES_ARTICULADORES if normalizar(e) in propuestos][:3]
        if not enc["ejes"]:
            problemas.append("Faltan \"ejes\": elige de 1 a 3 de la lista oficial, copiados tal cual.")
    for fila in datos.get("rubrica") or []:
        if isinstance(fila, dict):
            enc["rubrica"].append(_fila_rubrica(fila))

    if not enc["titulo_proyecto"]:
        problemas.append("Falta \"titulo_proyecto\".")
    for campo in ("intencion_didactica", "proposito"):
        if len(enc[campo]) < 120 or not es_texto_util(enc[campo], 80):
            problemas.append(f"\"{campo}\" está vacío o es demasiado corto (mínimo 3 oraciones).")
        elif _tiene_restos_de_chat(enc[campo]):
            problemas.append(f"\"{campo}\" tiene frases de chat; redáctalo como documento.")
    if re.search(r"\b\w{2,}ré\b", enc["proposito"]):
        problemas.append("\"proposito\" debe ir en primera persona del plural (\"propondremos\", no \"propondré\").")
    if not enc["evaluacion"]["criterios"]:
        problemas.append("Faltan criterios de evaluación.")
    if len(enc["rubrica"]) != n_pdas:
        problemas.append(f"La rúbrica debe tener exactamente {n_pdas} filas, una por proceso del PDA; tiene {len(enc['rubrica'])}.")
    if any(not all(es_texto_util(v, 6) for v in f.values()) for f in enc["rubrica"]):
        problemas.append("Hay filas de la rúbrica con niveles vacíos o incompletos.")
    texto = enc["intencion_didactica"] + " " + enc["proposito"]
    if _paginas_mencionadas(texto) - permitidas:
        problemas.append("Mencionas páginas que no están en las fuentes; quítalas.")
    return enc, problemas


def normalizar_sesiones(
    datos: dict, plan: list[dict], ids_fuentes: set[str], permitidas: set[int], exigir_ajustes: str = "",
    dicho_por_la_docente: str = "", vinculos: list[dict] | None = None,
) -> tuple[list[dict], list[str]]:
    problemas: list[str] = []
    crudas = datos.get("sesiones") if isinstance(datos.get("sesiones"), list) else []
    if len(crudas) != len(plan):
        problemas.append(f"Debe haber exactamente {len(plan)} sesiones; hay {len(crudas)}.")

    sesiones = []
    for i, p in enumerate(plan):
        s = crudas[i] if i < len(crudas) and isinstance(crudas[i], dict) else {}
        ini, des, cie = p["minutos"]
        sesion = {
            "etiqueta": p["etiqueta"],
            "fases": p["fases"],
            "inicio": {"minutos": ini, "texto": limpiar(s.get("inicio"))[:3000]},
            "desarrollo": {"minutos": des, "texto": limpiar(s.get("desarrollo"))[:3000]},
            "cierre": {"minutos": cie, "texto": limpiar(s.get("cierre"))[:3000]},
            "recursos": _lista_textos(s.get("recursos"), 12),
            "tarea": limpiar(s.get("tarea"))[:600],
            "citas": [c for c in (limpiar(x) for x in (s.get("citas") if isinstance(s.get("citas"), list) else [s.get("citas")])[:8]) if c in ids_fuentes],
        }
        if sesion["tarea"] and not es_texto_util(sesion["tarea"], 10):
            sesion["tarea"] = ""
        for momento in ("inicio", "desarrollo", "cierre"):
            texto = sesion[momento]["texto"]
            if not es_texto_util(texto, 30):
                # Relleno o texto cortado: se trata como vacio.
                sesion[momento]["texto"] = ""
                problemas.append(f"{p['etiqueta']}: \"{momento}\" está vacío, incompleto o cortado.")
            elif _tiene_restos_de_chat(texto):
                problemas.append(f"{p['etiqueta']}: \"{momento}\" tiene frases de chat.")
            for inventada in instancias_inventadas(texto, dicho_por_la_docente):
                problemas.append(f"{p['etiqueta']}: menciona {inventada}, que la docente no indicó; quítalo o usa al grupo o las familias.")
            if _DOCENTE_HACE_EL_TRABAJO.search(texto):
                problemas.append(f"{p['etiqueta']}: \"{momento}\" pone a la docente a hacer el producto; que lo hagan las y los alumnos.")
            if _TERCERA_PERSONA.search(texto):
                problemas.append(f"{p['etiqueta']}: \"{momento}\" habla de la docente en tercera persona; escribe en primera persona (\"Recopilaré...\").")
            if _paginas_mencionadas(texto) - permitidas:
                problemas.append(f"{p['etiqueta']}: menciona páginas que no están en las fuentes.")
        if not sesion["recursos"]:
            problemas.append(f"{p['etiqueta']}: faltan recursos.")
        sesiones.append(sesion)

    # Cada vinculo debe trabajarse de verdad en su sesion, no solo aparecer en
    # la tabla. Basta con que la sesion comparta el 30 % de sus palabras
    # clave con la actividad del vinculo. O(k * L).
    # Solo cuentan las palabras distintivas del vinculo: se descartan las que
    # aparecen en mas de la mitad de las sesiones ("agua", "comunidades",
    # "experiencia"), porque esas no prueban que el vinculo este ahi.
    por_etiqueta = {s["etiqueta"]: s for s in sesiones}
    raices_por_sesion = {
        s["etiqueta"]: set(raices(" ".join(s[m]["texto"] for m in ("inicio", "desarrollo", "cierre")))) for s in sesiones
    }
    frecuencia: dict[str, int] = {}
    for r in raices_por_sesion.values():
        for x in r:
            frecuencia[x] = frecuencia.get(x, 0) + 1
    comunes = {x for x, n in frecuencia.items() if n > len(sesiones) / 2} | _GENERICAS
    for v in vinculos or []:
        if v["sesion"] not in por_etiqueta or v["actividad"].startswith("[Completar"):
            continue
        distintivas = set(raices(v["actividad"] + " " + v.get("evidencia", ""))) - comunes
        if len(distintivas) < 2:
            continue
        if len(distintivas & raices_por_sesion[v["sesion"]]) < max(2, 0.4 * len(distintivas)):
            problemas.append(f"{v['sesion']}: no incluye el vínculo con {v['campo']} ({v['actividad'][:120]}).")

    if exigir_ajustes and not ajustes_aplicados(sesiones, exigir_ajustes):
        problemas.append('Ninguna sesión aplica los ajustes razonables: escribe "Ajuste razonable:" y qué harás en una actividad.')

    # Una sesion que copia el inicio o el desarrollo de otra = actividad
    # repetida. Se compara el principio normalizado con un set: O(S).
    for momento in ("inicio", "desarrollo"):
        vistos: set[str] = set()
        for s in sesiones:
            clave = re.sub(r"\W+", " ", _sin_acentos(s[momento]["texto"]))[:80]
            if clave and clave in vistos:
                problemas.append(f"{s['etiqueta']}: repite el {momento} de otra sesión.")
            vistos.add(clave)
    return sesiones, problemas


def resolver_vinculos(planeacion, contexto: dict, base: dict) -> list[dict]:
    """
    Contenidos de otros campos con los que se integra el proyecto. Lo que
    elija la docente se valida contra el catalogo (un contenido inventado o
    del mismo campo se descarta); en modo "sugerida" sin eleccion, los
    propone el codigo. Nunca los inventa el modelo. O(k) con k <= 3.
    """
    integracion = contexto.get("integracion") or {}
    modo = integracion.get("modo", "ninguna")
    if modo == "ninguna":
        return []
    elegidos = integracion.get("contenidos") or []
    if modo == "sugerida" and not elegidos:
        texto = " ".join(filter(None, [base["contenido"], " ".join(base["pdas"]), planeacion.tema,
                                        contexto.get("problematica", "")]))
        elegidos = sugerir_vinculos(planeacion.grado, base["campo"], texto)

    texto_proyecto = " ".join(filter(None, [base["contenido"], planeacion.tema, contexto.get("problematica", "")]))
    vinculos, campos = [], {normalizar(base["campo"])}
    for v in elegidos[:MAX_VINCULOS]:
        oficial = buscar_contenido(planeacion.grado, v.get("campo", ""), v.get("contenido", ""))
        if not oficial or normalizar(oficial["campo"]) in campos:
            continue
        campos.add(normalizar(oficial["campo"]))
        # Solo los procesos del PDA que se trabajan en este proyecto, no los
        # cinco del contenido (p. ej. el efecto invernadero en un proyecto
        # de ahorro de agua).
        pdas = procesos_relevantes(separar_pdas(oficial["pda"]), texto_proyecto)
        vinculos.append({"campo": oficial["campo"], "contenido": oficial["contenido"], "pdas": pdas})
    return vinculos


def normalizar_integracion(datos: dict, vinculos: list[dict], etiquetas: list[str]) -> tuple[list[dict], list[dict], list[str]]:
    """Regresa (vinculos con actividad, filas de rubrica, problemas). Una
    sesion que no existe se reasigna repartiendo los vinculos a lo largo del
    proyecto. O(k + S)."""
    problemas: list[str] = []
    crudos = datos.get("vinculos") if isinstance(datos.get("vinculos"), list) else []
    crudos = [c for c in crudos if isinstance(c, dict)]
    if len(crudos) != len(vinculos):
        problemas.append(f"Debe haber exactamente {len(vinculos)} vínculos; hay {len(crudos)}.")
    # Se emparejan por campo formativo, no por posicion: el modelo a veces
    # devuelve los vinculos en otro orden y la actividad de Lenguajes
    # terminaba asignada a Saberes. Sin campo reconocible, se usa la posicion.
    por_campo = {normalizar(limpiar(c.get("campo"))): c for c in crudos if c.get("campo")}
    validas = {normalizar(e): e for e in etiquetas}
    salida, rubrica = [], []
    for i, v in enumerate(vinculos):
        c = por_campo.get(normalizar(v["campo"])) or (crudos[i] if i < len(crudos) else {})
        sesion = validas.get(normalizar(limpiar(c.get("sesion"))))
        if sesion is None:
            # Reparto uniforme en las sesiones centrales del proyecto.
            sesion = etiquetas[min(len(etiquetas) - 1, ((i + 1) * len(etiquetas)) // (len(vinculos) + 1))]
        actividad = limpiar(c.get("actividad"))[:1500]
        if not es_texto_util(actividad, 30):
            actividad = ""
        if len(actividad) < 40:
            problemas.append(f"Vínculo {i + 1} ({v['campo']}): falta la actividad.")
        elif _tiene_restos_de_chat(actividad):
            problemas.append(f"Vínculo {i + 1}: tiene frases de chat.")
        evidencia = limpiar(c.get("evidencia"))[:600]
        salida.append({**v, "sesion": sesion, "actividad": actividad,
                       "evidencia": evidencia if es_texto_util(evidencia, 4) else ""})
        fila = c.get("rubrica") if isinstance(c.get("rubrica"), dict) else {}
        fila = _fila_rubrica(fila)
        fila = {k: (t if es_texto_util(t, 6) else "[Completar]") for k, t in fila.items()}
        if not fila["indicador"]:
            fila["indicador"] = f"{v['campo']}: {v['contenido'][:120]}"
        rubrica.append(fila)
    # La actividad debe parecerse mas a SU PDA que al de otro vinculo; si no,
    # probablemente quedaron cruzados. O(k^2 * L).
    pda_raices = [set(raices(" ".join(v["pdas"]) + " " + v["contenido"])) for v in vinculos]
    for i, v in enumerate(salida):
        propia = set(raices(v["actividad"] + " " + v["evidencia"]))
        mia = len(propia & pda_raices[i])
        for j in range(len(salida)):
            if j != i and len(propia & pda_raices[j]) > mia + 1:
                problemas.append(
                    f"Vínculo {i + 1} ({v['campo']}): la actividad corresponde al PDA de {salida[j]['campo']}; trabaja el PDA de {v['campo']}."
                )
                break

    # Cada vinculo debe dejar su propio producto: si dos evidencias son casi
    # iguales (p. ej. ambos "reglamento"), uno esta haciendo el trabajo del
    # otro. Comparacion por pares, k <= 3: O(k^2 * L).
    for i in range(len(salida)):
        for j in range(i + 1, len(salida)):
            a, b = set(raices(salida[i]["evidencia"])), set(raices(salida[j]["evidencia"]))
            if a and b and len(a & b) >= 0.5 * min(len(a), len(b)):
                problemas.append(
                    f"Los vínculos {i + 1} y {j + 1} producen lo mismo; cada uno debe trabajar su propio PDA con un producto distinto."
                )
    for i, fila in enumerate(rubrica):
        if "cumplimiento del pda" in _sin_acentos(fila["indicador"]):
            problemas.append(f"Vínculo {i + 1}: el indicador de la rúbrica debe nombrar la habilidad concreta, no \"cumplimiento del PDA\".")
    return salida, rubrica, problemas


# Personas, instancias o eventos que el modelo tiende a inventar ("se
# presentara a la direccion escolar", "comite de convivencia"). Solo se
# aceptan si la docente los menciono en lo que escribio.
_INSTANCIAS = {
    "la dirección escolar": r"\bdirecci[oó]n(?:\s+escolar)?\b|\bdirector(?:a)?\b",
    "un comité": r"\bcomit[eé]s?\b",
    "una reunión con padres o familias": r"\b(?:reuni[oó]n|junta)\s+(?:de|con)\s+(?:padres|familias|madres)",
    "una feria o concurso": r"\bferias?\b|\bconcursos?\b",
    "autoridades": r"\bautoridades\b|\bpresidencia municipal\b|\bayuntamiento\b",
    "una normativa existente": r"\bnormativas?\b|\bnormatividad\b|\bley(?:es)? vigentes?\b",
}
_INSTANCIAS_RE = {nombre: re.compile(patron, re.IGNORECASE) for nombre, patron in _INSTANCIAS.items()}


def instancias_inventadas(texto: str, dicho_por_la_docente: str) -> list[str]:
    """Instancias que aparecen en el texto pero no en lo que escribio la
    docente. O(L) por patron, con 5 patrones fijos."""
    return [
        nombre for nombre, patron in _INSTANCIAS_RE.items()
        if patron.search(texto) and not patron.search(dicho_por_la_docente)
    ]


# Palabras de aula que aparecen en cualquier actividad y no distinguen un
# vinculo de otro.
_GENERICAS = {r[:6] for r in (
    "estudiantes", "alumnos", "alumnas", "grupos", "comunidades", "organizaremos", "actividad", "experiencias",
    "pediré", "guiaré", "clase", "sesion", "trabajo", "basandose", "propuestas", "acciones", "escuela",
)}

# Verbos en los que la docente hace el producto que deberian hacer los
# alumnos ("redactaré las reglas"). Guiar, explicar o pedir si van.
_DOCENTE_HACE_EL_TRABAJO = re.compile(
    r"\b(?:redactaré|elaboraré|escribiré|analizaré|diseñaré|resolveré|calcularé|completaré|mediré|registraré|clasificaré|investigaré)\b",
    re.IGNORECASE
)

_TERCERA_PERSONA = re.compile(r"\b(?:el|la)\s+(?:profesor|profesora|docente|maestro|maestra)\b", re.IGNORECASE)


def ajustes_aplicados(sesiones: list[dict], ajustes: str) -> bool:
    """
    True si alguna sesion aplica los ajustes razonables: con la etiqueta
    "Ajuste razonable:" o mencionando al menos dos palabras clave de lo que
    escribio la docente (p. ej. "lentes" y "pasos"). O(L).
    """
    textos = [s[m]["texto"] for s in sesiones for m in ("inicio", "desarrollo", "cierre")]
    if any("ajuste razonable" in _sin_acentos(t) for t in textos):
        return True
    clave = set(raices(ajustes))
    if not clave:
        return False
    necesarias = min(2, len(clave))
    return any(len(clave & set(raices(t))) >= necesarias for t in textos)


def _marcar_pendientes(enc: dict, sesiones: list[dict], n_pdas: int) -> list[str]:
    """Lo que siguio mal tras reparar se marca para la docente. O(S)."""
    pendientes: list[str] = []
    if "ejes" in enc and not enc["ejes"]:
        pendientes.append("Ejes articuladores")
    if not enc["titulo_proyecto"]:
        enc["titulo_proyecto"] = "[Completar: título del proyecto]"
        pendientes.append("Título del proyecto")
    for campo, nombre in (("intencion_didactica", "Intención didáctica"), ("proposito", "Propósito")):
        if len(enc[campo]) < 40:
            enc[campo] = f"[Completar: {nombre.lower()}]"
            pendientes.append(nombre)
    while len(enc["rubrica"]) < n_pdas:
        enc["rubrica"].append({"indicador": "[Completar]", "sobresaliente": "", "satisfactorio": "", "en_proceso": ""})
        pendientes.append("Fila de la rúbrica")
    enc["rubrica"] = enc["rubrica"][:n_pdas]
    for s in sesiones:
        for momento in ("inicio", "desarrollo", "cierre"):
            if not es_texto_util(s[momento]["texto"], 30):
                s[momento]["texto"] = f"[Completar: {momento} de {s['etiqueta']}]"
                pendientes.append(f"{s['etiqueta']}: {momento}")
    return pendientes


# --------------------------------------------------------------------------
# Punto de entrada
# --------------------------------------------------------------------------

def generar_documento(planeacion, contexto: dict, docente: str, escuela: str, indicaciones: list[str]) -> dict:
    base = armar_base(planeacion, contexto, docente, escuela)

    # Metricas de calidad de esta generacion (para la evaluacion de la tesis):
    # cuantas llamadas y reparaciones hicieron falta y que detecto el codigo.
    metricas = {"llamadas": 0, "reparaciones": 0, "problemas_detectados": [], "problemas_finales": []}

    def llamar(*args, **kwargs):
        metricas["llamadas"] += 1
        return _llamar(*args, **kwargs)

    def reparar(*args, **kwargs):
        metricas["llamadas"] += 1
        metricas["reparaciones"] += 1
        return _reparar(*args, **kwargs)

    dicho_por_la_docente = " ".join(filter(None, [
        planeacion.tema, contexto.get("problematica", ""), contexto.get("recursos_disponibles", ""), *indicaciones,
    ]))
    plan = base.pop("_plan_sesiones")
    n_pdas = len(base["pdas"])

    consulta = " ".join(filter(None, [base["contenido"], planeacion.tema, contexto.get("problematica", "")]))[:600]
    fuentes, contexto_rag = buscar_fuentes(consulta, planeacion.grado)
    permitidas = _paginas_permitidas(fuentes)
    ids_fuentes = {f["id"] for f in fuentes}

    datos = _datos_para_modelo(base, planeacion, contexto, indicaciones)
    bloque_fuentes = (
        "\n\nFuentes de los libros de la SEP (únicas que puedes citar):\n" + contexto_rag
        if contexto_rag else "\n\nNo hay fuentes de libros disponibles: no menciones páginas."
    )

    # --- Llamada A: encabezado
    pedir_ejes = not base["ejes"]
    user_a = "Datos fijos:\n" + datos + bloque_fuentes
    if pedir_ejes:
        user_a += "\n\nLa docente no eligió ejes articuladores. Propón de 1 a 3 de esta lista oficial: " + "; ".join(EJES_ARTICULADORES)
    schema_a = esquema_encabezado(n_pdas, pedir_ejes)
    enc, problemas = normalizar_encabezado(
        llamar(_PROMPT_ENCABEZADO, user_a, 3000, schema_a, "encabezado"), n_pdas, permitidas, pedir_ejes
    )
    metricas["problemas_detectados"] += problemas
    if problemas:
        reparado = reparar(_PROMPT_ENCABEZADO, user_a, enc, problemas, 3000, schema_a, "encabezado")
        enc_r, problemas_r = normalizar_encabezado(reparado, n_pdas, permitidas, pedir_ejes)
        if len(problemas_r) < len(problemas):
            enc, problemas = enc_r, problemas_r

    # Si ni siquiera hay titulo ni intencion, el modelo no respondio nada util.
    if not enc["titulo_proyecto"] and not enc["intencion_didactica"]:
        raise HTTPException(status_code=502, detail="Cuali no pudo armar la planeación esta vez. Intenta de nuevo.")

    plan_texto = "\n".join(
        f"{i + 1}. {p['etiqueta']}: fase(s) {', '.join(p['fases'])}. Inicio {p['minutos'][0]} min, "
        f"desarrollo {p['minutos'][1]} min, cierre {p['minutos'][2]} min."
        for i, p in enumerate(plan)
    )
    etiquetas = [p["etiqueta"] for p in plan]

    # --- Llamada C (opcional): integracion con otros campos
    vinculos = resolver_vinculos(planeacion, contexto, base)
    rubrica_vinculos: list[dict] = []
    bloque_vinculos = ""
    if vinculos:
        lista = "\n".join(
            f"{i + 1}. {v['campo']}. Contenido: {v['contenido']} PDA: {' '.join(v['pdas'])}"
            for i, v in enumerate(vinculos)
        )
        user_c = (
            f"Proyecto: {enc['titulo_proyecto']}\nCampo principal: {base['campo']}\nPropósito: {enc['proposito']}"
            + "\n\nSesiones (etiqueta y fase):\n" + plan_texto
            + "\n\nVínculos a integrar (oficiales, no los cambies):\n" + lista
        )
        system_c = _PROMPT_INTEGRACION.replace("{n}", str(len(vinculos)))
        schema_c = esquema_integracion(len(vinculos), etiquetas)
        vinculos, rubrica_vinculos, problemas_c = normalizar_integracion(
            llamar(system_c, user_c, 2500, schema_c, "integracion"), vinculos, etiquetas
        )
        metricas["problemas_detectados"] += problemas_c
        if problemas_c:
            reparado = reparar(system_c, user_c, {"vinculos": vinculos}, problemas_c, 2500, schema_c, "integracion")
            v_r, r_r, p_r = normalizar_integracion(reparado, [
                {k: v[k] for k in ("campo", "contenido", "pdas")} for v in vinculos
            ], etiquetas)
            if len(p_r) < len(problemas_c):
                vinculos, rubrica_vinculos = v_r, r_r
        for v in vinculos:
            for inventada in instancias_inventadas(v["actividad"] + " " + v["evidencia"], dicho_por_la_docente):
                metricas["problemas_finales"].append(f"Vínculo {v['campo']}: menciona {inventada}")
            if len(v["actividad"]) < 40:
                v["actividad"] = f"[Completar: actividad de vínculo con {v['campo']}]"
        bloque_vinculos = "\n\nVínculos con otros campos (inclúyelos en la sesión indicada):\n" + "\n".join(
            f"- {v['sesion']} ({v['campo']}): {v['actividad']}" for v in vinculos
        )

    # --- Llamada B: sesiones
    user_b = (
        "Datos fijos:\n" + datos
        + f"\n\nTítulo del proyecto: {enc['titulo_proyecto']}\nPropósito: {enc['proposito']}"
        + "\nEvidencias esperadas: " + "; ".join(enc["evaluacion"]["evidencias"])
        + "\n\nSesiones y fase que le toca a cada una:\n" + plan_texto
        + bloque_vinculos
        + bloque_fuentes
    )
    system_b = _PROMPT_SESIONES.replace("{n}", str(len(plan)))
    tokens_b = min(8000, 900 * len(plan) + 1500)
    schema_b = esquema_sesiones(len(plan))
    exigir_ajustes = (contexto.get("ajustes_razonables") or "").strip()
    sesiones, problemas_s = normalizar_sesiones(
        llamar(system_b, user_b, tokens_b, schema_b, "sesiones"), plan, ids_fuentes, permitidas, exigir_ajustes, dicho_por_la_docente, vinculos
    )
    metricas["problemas_detectados"] += problemas_s
    if problemas_s:
        previo = {"sesiones": [
            {"inicio": s["inicio"]["texto"], "desarrollo": s["desarrollo"]["texto"], "cierre": s["cierre"]["texto"],
             "recursos": s["recursos"], "tarea": s["tarea"], "citas": s["citas"]} for s in sesiones
        ]}
        reparado = reparar(system_b, user_b, previo, problemas_s, tokens_b, schema_b, "sesiones")
        sesiones_r, problemas_r = normalizar_sesiones(reparado, plan, ids_fuentes, permitidas, exigir_ajustes, dicho_por_la_docente, vinculos)
        if len(problemas_r) < len(problemas_s):
            sesiones, problemas_s = sesiones_r, problemas_r
    metricas["problemas_finales"] += problemas + problemas_s

    # Una pagina no consultada se quita del texto en vez de dejarla. O(L).
    def _quitar_paginas_ajenas(texto: str) -> str:
        def _sub(m):
            return m.group(0) if _paginas_mencionadas(m.group(0)) <= permitidas else "[Confirmar página]"
        return _PAGINAS.sub(_sub, texto)

    por_id = {f["id"]: f for f in fuentes}
    campos_por_sesion: dict[str, list[str]] = {}
    for v in vinculos:
        campos_por_sesion.setdefault(v["sesion"], []).append(v["campo"])
    for s in sesiones:
        for momento in ("inicio", "desarrollo", "cierre"):
            texto = s[momento]["texto"]
            # Un id citado en el texto cuenta como cita aunque el modelo no
            # lo haya puesto en "citas".
            for fid in _ID_FUENTE.findall(texto):
                if fid in por_id and fid not in s["citas"]:
                    s["citas"].append(fid)
            s[momento]["texto"] = _quitar_paginas_ajenas(resolver_citas(texto, por_id))
        s["tarea"] = resolver_citas(s["tarea"], por_id)
        s["recursos"] = normalizar_recursos(s["recursos"])
        s["vinculos"] = campos_por_sesion.get(s["etiqueta"], [])
    for v in vinculos:
        v["actividad"] = resolver_citas(v["actividad"], por_id)
    # Si algun momento de la sesion ya describe el vinculo (aunque con otras
    # palabras), no se repite debajo de ella; sigue en la tabla de
    # integracion. Se mide cuantas raices de la actividad aparecen en el
    # momento: O(k * L).
    momentos_por_sesion = {
        s["etiqueta"]: [set(raices(s[m]["texto"])) for m in ("inicio", "desarrollo", "cierre")] for s in sesiones
    }
    for v in vinculos:
        de_la_actividad = set(raices(v["actividad"]))
        v["en_sesion"] = bool(de_la_actividad) and any(
            len(de_la_actividad & momento) >= 0.6 * len(de_la_actividad)
            for momento in momentos_por_sesion.get(v["sesion"], [])
        )
    for campo in ("intencion_didactica", "proposito"):
        enc[campo] = resolver_citas(enc[campo], por_id)

    pendientes = _marcar_pendientes(enc, sesiones, n_pdas)
    for s in sesiones:
        texto = " ".join(s[m]["texto"] for m in ("inicio", "desarrollo", "cierre"))
        for inventada in instancias_inventadas(texto, dicho_por_la_docente):
            pendientes.append(f"{s['etiqueta']}: revisar la mención a {inventada}")
    if exigir_ajustes and not ajustes_aplicados(sesiones, exigir_ajustes):
        # El modelo no lo aplico ni tras reparar: se deja marcado para la
        # docente en la sesion central, en vez de dar por hecho que se cubrio.
        central = sesiones[len(sesiones) // 2]
        central["desarrollo"]["texto"] += " Ajuste razonable: [Completar: cómo aplicarás los ajustes razonables en esta actividad]"
        pendientes.append("Ajustes razonables en las sesiones")
    enc["rubrica"] = enc["rubrica"] + rubrica_vinculos
    # La evaluacion tambien cubre lo integrado: un criterio y una evidencia
    # por cada vinculo, sin repetir. O(k).
    # Se deja espacio (tope de 10 en el esquema) para los de los vinculos.
    for clave in ("criterios", "evidencias"):
        enc["evaluacion"][clave] = enc["evaluacion"][clave][: 10 - len(vinculos)]
    for v, fila in zip(vinculos, rubrica_vinculos):
        criterio = fila["indicador"] if fila["indicador"] != "[Completar]" else f"{v['campo']}: {v['contenido']}"
        if criterio not in enc["evaluacion"]["criterios"]:
            enc["evaluacion"]["criterios"].append(_indicador_corto(criterio))
        if v.get("evidencia") and v["evidencia"] not in enc["evaluacion"]["evidencias"]:
            enc["evaluacion"]["evidencias"].append(v["evidencia"])
    usadas = {c for s in sesiones for c in s["citas"]}
    # Una pagina citada en el texto ("pág. 195") tambien cuenta como fuente
    # usada aunque el modelo no haya puesto el id. O(L).
    paginas_citadas = set()
    for s in sesiones:
        for m in ("inicio", "desarrollo", "cierre"):
            paginas_citadas |= _paginas_mencionadas(s[m]["texto"])
    return {
        **base,
        **enc,
        "sesiones": sesiones,
        "integracion": vinculos,
        "metricas": {
            **metricas,
            "problemas_detectados": metricas["problemas_detectados"][:40],
            "problemas_finales": metricas["problemas_finales"][:40],
        },
        "fuentes": [f for f in fuentes if f["id"] in usadas or (_paginas_de(f) & paginas_citadas)] or fuentes,
        "pendientes": pendientes,
    }
