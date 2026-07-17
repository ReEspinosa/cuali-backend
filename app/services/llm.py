"""
Funciones de LLM del proyecto.

generar_respuesta_chat() y generar_respuesta_general() usan el RAG real de
la SEP (retrieval + reglas anti-alucinación) para armar el contexto, y
delegan la llamada al modelo al cliente ya probado en
app.services.llm_client (el que sabe manejar el HTTP Basic Auth del
servidor de la UNAM) -- no se crea ningún cliente nuevo aquí.

extraer_planeacion_estructurada() no requiere RAG (no busca contenido nuevo
de la SEP, solo estructura la conversación ya ocurrida).

El resto de las funciones (diapositivas, cuestionarios, mapas mentales,
juegos, laboratorio, etc.) tampoco usan el RAG -- generan contenido a partir
de lo que el maestro pide y, si aplica, del texto de un adjunto.
"""
import json
import logging
import re

from app.models.planeacion import Mensaje, Planeacion
from app.services.llm_client import chat_completion, chat_completion_json

logger = logging.getLogger(__name__)

try:
    from app.services.rag.rag_chat import ask as rag_ask
except Exception:
    logger.exception("No se pudo importar el módulo de RAG -- revisa dependencias/config.")
    rag_ask = None


_GRADO_WORDS = {
    "primero": "1", "primer": "1",
    "segundo": "2",
    "tercero": "3", "tercer": "3",
    "cuarto": "4",
    "quinto": "5",
    "sexto": "6",
}


def _detectar_grado_y_limpiar(texto: str) -> tuple[str | None, str]:
    """
    Detecta menciones de grado en texto libre (ej. "para tercer grado",
    "3er grado", "grado 4", "segundo de primaria") para poder filtrar la
    búsqueda del RAG por ese grado, incluso en el chat libre donde no hay
    un campo de grado explícito en la base de datos.

    Además de detectarlo, lo QUITA del texto que se va a usar para la
    búsqueda semántica (regresa un texto "limpio" aparte). Esto es
    importante: como el grado ya se aplica como filtro exacto de metadata,
    dejar palabras como "sexto"/"6" en el texto de búsqueda no ayuda -- solo
    agrega ruido al embedding, y puede hacer que la misma pregunta escrita
    de dos formas distintas ("sexto grado" vs "6 grado") traiga resultados
    diferentes. Quitarlo hace que ambas formas busquen exactamente lo mismo.

    Regresa (grado, texto_para_busqueda). Si no detecta nada, regresa
    (None, texto original).
    """
    patrones = [
        r"\d\s*[°º]?\s*(?:er|do|to|ro|vo)?\s*(?:de\s*)?grado",
        r"grado\s*(?:de\s*)?\d",
    ]
    for patron in patrones:
        match = re.search(patron, texto, flags=re.IGNORECASE)
        if match:
            grado = re.search(r"\d", match.group(0)).group(0)
            limpio = texto[:match.start()] + texto[match.end():]
            limpio = re.sub(r"\s+", " ", limpio).strip(" ,.")
            return grado, limpio

    for palabra, grado in _GRADO_WORDS.items():
        # también consume "de primaria" o "grado" si sigue justo después,
        # para no dejar residuos colgando ("... de grado" suelto)
        match = re.search(rf"\b{palabra}\b(\s+de\s+primaria|\s+grado)?", texto, flags=re.IGNORECASE)
        if match:
            limpio = texto[:match.start()] + texto[match.end():]
            limpio = re.sub(r"\s+", " ", limpio).strip(" ,.")
            return grado, limpio

    return None, texto


def _chat_fn(messages: list[dict]) -> str:
    """Puente hacia el cliente LLM ya probado del equipo -- así el RAG usa
    la misma conexión (con Basic Auth) que ya confirmamos que funciona."""
    return chat_completion(messages=messages, temperature=0.2, max_tokens=2200)


def _contenido_con_adjuntos(content: str, adjuntos: list[dict] | None) -> str:
    """
    Si el mensaje trae adjuntos con texto ya extraído (docx/pdf), lo agrega
    como contexto legible para el modelo. Si el adjunto no se pudo leer
    (imagen, .doc viejo, etc.), lo indica en vez de fingir que lo leyó.
    """
    if not adjuntos:
        return content

    bloques = [content] if content.strip() else []
    for a in adjuntos:
        nombre = a.get("filename", "archivo adjunto")
        texto = a.get("texto_extraido")
        if texto:
            bloques.append(f'--- Contenido del archivo adjunto "{nombre}" ---\n{texto}\n--- Fin del archivo ---')
        else:
            bloques.append(
                f'(El maestro adjuntó el archivo "{nombre}", pero no fue posible extraer su texto '
                f"automáticamente. Si es relevante, pídele que te copie el contenido o te lo describa.)"
            )
    return "\n\n".join(bloques)


def _system_prompt(planeacion: Planeacion) -> str:
    return f"""Eres Cuali, asistente pedagógico para maestros de primaria en México,
alineado a la Nueva Escuela Mexicana (NEM).

Estás ayudando a planear una clase con estos datos:
- Grado: {planeacion.grado}° de primaria, grupo "{planeacion.grupo}"
- Campo formativo: {planeacion.campo_formativo}
- Contenido NEM: {planeacion.contenido}
- PDA (Proceso de Desarrollo de Aprendizaje) completo:
{planeacion.pda}
- Número de sesiones: {planeacion.sesiones}
- Tema/objetivo que quiere abordar el maestro: {planeacion.tema}

Tu papel en esta conversación es GUIAR al maestro con preguntas para reunir
la información necesaria para su planeación, no darle la planeación completa
en el chat. La planeación final se genera aparte con un botón. Cuando
comentes o sugieras algo, aunque sea brevemente, apóyate en los fragmentos
de los libros de la SEP que se te den como contexto, no en conocimiento
general.

No uses emojis."""


def _directiva_de_fase(num_mensajes_maestro: int) -> str:
    """
    Controla el flujo guiado: bienvenida + 2 preguntas, luego rondas de
    retroalimentación + 2 preguntas, y al llegar a suficiente información,
    invitación a presionar el botón "Generar planeación".
    """
    if num_mensajes_maestro <= 1:
        return """Instrucción para este turno: este es el PRIMER mensaje del maestro
(el resumen de su formulario). Dale una bienvenida breve y entusiasta,
reconoce el tema que eligió, y hazle EXACTAMENTE 2 preguntas concretas que
te ayuden a personalizar su planeación (por ejemplo: qué materiales tiene
disponibles, cómo es su grupo, qué actividades le han funcionado antes, si
quiere incluir alguna dinámica específica). Nada más: no des actividades
todavía, no hagas más de 2 preguntas."""
    if num_mensajes_maestro <= 4:
        return f"""Instrucción para este turno: el maestro va {num_mensajes_maestro - 1} de 4
respuestas. Comenta brevemente lo que te acaba de responder (1 o 2 oraciones,
mostrando que lo tomaste en cuenta) y hazle EXACTAMENTE 2 preguntas nuevas
que aún no hayas hecho, para seguir afinando la planeación. No repitas
preguntas anteriores. No des la planeación todavía."""
    return """Instrucción para este turno: el maestro ya respondió suficientes
preguntas. NO hagas más preguntas. Agradécele, resume en 2 o 3 oraciones lo
más importante que te contó, y dile explícitamente algo como: "Ya tengo la
información necesaria para armar tu planeación. Te invito a presionar el
botón 'Generar planeación' que está arriba para descargarla." """


def _to_history_messages(historial: list[Mensaje]) -> list[dict]:
    return [
        {"role": mensaje.role, "content": _contenido_con_adjuntos(mensaje.content, mensaje.adjuntos)}
        for mensaje in historial
    ]


def _normalizar_fuentes(sources: list[dict]) -> list[dict]:
    fuentes = []
    for source in sources or []:
        if not isinstance(source, dict):
            continue
        fuentes.append({
            "documento": source.get("libro") or source.get("documento") or "",
            "campo": source.get("materia") or source.get("campo"),
            "pagina": source.get("paginas") or source.get("pagina"),
        })
    return fuentes


class RagNoDisponible(Exception):
    """Se lanza cuando el RAG no pudo responder -- por config faltante o
    por una falla real -- para que quien llame decida qué mostrar, en vez
    de recibir silenciosamente un string vacío indistinguible de otros
    casos."""


def _generar_respuesta_con_rag(
    question: str,
    grado=None,
    historial=None,
    extra_context=None,
    retrieval_query=None,
    allow_markdown=False,
) -> tuple[str, list[dict]]:
    if rag_ask is None:
        raise RagNoDisponible("El módulo de RAG no se pudo cargar (ver log al iniciar el backend).")

    try:
        resultado = rag_ask(
            question=question,
            # OJO: NO se filtra por materia aquí -- "materia" en el RAG es
            # el tipo de libro (Proyectos de Aula, Múltiples Lenguajes...),
            # no el campo formativo NEM. Filtrar por campo_formativo daría
            # cero resultados casi siempre porque no hace match exacto.
            grado=str(grado) if grado is not None else None,
            history=historial,
            extra_context=extra_context,
            chat_fn=_chat_fn,
            retrieval_query=retrieval_query,
            allow_markdown=allow_markdown,
        )
    except Exception:
        logger.exception("Falla al llamar al RAG para la pregunta: %r", question)
        raise RagNoDisponible("Ocurrió un error al consultar el RAG.")

    answer = resultado.get("answer") or ""
    sources = _normalizar_fuentes(resultado.get("sources") or [])
    return answer, sources


def generar_respuesta_chat(
    planeacion: Planeacion,
    historial: list[Mensaje],
    nuevo_mensaje: str,
    adjuntos_nuevos: list[dict] | None = None,
) -> str:
    num_mensajes_maestro = sum(1 for m in historial if m.role == "user") + 1
    extra_context = _system_prompt(planeacion) + "\n\n" + _directiva_de_fase(num_mensajes_maestro)
    pregunta_con_adjuntos = _contenido_con_adjuntos(nuevo_mensaje, adjuntos_nuevos)

    try:
        respuesta, _ = _generar_respuesta_con_rag(
            question=pregunta_con_adjuntos,
            grado=planeacion.grado,
            historial=_to_history_messages(historial),
            extra_context=extra_context,
            # Búsqueda con el mensaje original, sin el texto del adjunto
            # pegado -- un adjunto largo diluiría la búsqueda semántica.
            retrieval_query=nuevo_mensaje,
        )
        return respuesta
    except RagNoDisponible:
        # Fallback EXPLÍCITO -- nunca simula una respuesta real. El docente
        # necesita saber que esto no vino de los libros de la SEP.
        return (
            "No pude conectarme con la base de libros de la SEP en este momento, "
            "así que no puedo darte una respuesta fundamentada ahora mismo. "
            "Intenta de nuevo en unos segundos; si el problema persiste, avísale "
            "al equipo técnico."
        )


_EXTRACCION_SYSTEM_PROMPT = """Eres un asistente que convierte una conversación entre
un maestro de primaria en México y "Cuali" (un asistente pedagógico) en un
documento de planeación didáctica estructurado, alineado a la Nueva Escuela
Mexicana (NEM).

Reglas estrictas:
- Responde ÚNICAMENTE con un objeto JSON válido. Sin texto antes ni después.
  Sin backticks, sin markdown, sin explicaciones.
- No inventes ni reescribas el PDA que se te da como dato fijo; úsalo como
  ancla para redactar "intencion_didactica", "proposito" y las actividades.
- Redacta "intencion_didactica", "proposito" y las "actividades" de cada
  sesión en primera persona, como si fueras la maestra o el maestro dirigiendo
  la clase (ejemplo: "Les pediré que...", "Leeremos...", "Organizaré equipos
  para...").
- El número de objetos dentro de "dias" debe ser EXACTAMENTE igual al número
  de sesiones indicado.
- Al menos una sesión debe incluir una actividad lúdica explícita.
- Usa la información real que el maestro haya dado en la conversación
  (materiales que mencionó, ideas que propuso, fechas o días de la semana si
  los dijo). Si el maestro no dio suficiente detalle para alguna sesión,
  redacta algo razonable y coherente con el PDA, no lo dejes vacío.
- No uses emojis.

Responde con este JSON exacto (mismas llaves, mismo orden no importa):
{
  "metodologia": "string, por ejemplo 'Aprendizaje Basado en Proyectos' o 'Aprendizaje Servicio', la que mejor calce con la conversación",
  "ejes_articuladores": ["string", "..."],
  "titulo_proyecto": "string",
  "intencion_didactica": "string",
  "proposito": "string",
  "dias": [
    {
      "etapa": "string, ej. 'Punto de partida', 'Planificación', 'Acción', 'Comunicamos', 'Reflexionamos'",
      "sesion": "string, ej. 'Sesión 1' o el día de la semana si el maestro lo mencionó",
      "actividades": "string",
      "recursos": "string",
      "tarea": "string"
    }
  ],
  "evaluacion": "string"
}"""


def _extraer_json(texto: str) -> dict:
    """
    Intenta parsear el texto como JSON directo; si el modelo lo envolvió en
    markdown (```json ... ```) o le agregó texto alrededor, se extrae el
    primer bloque {...} balanceado y se reintenta.
    """
    try:
        return json.loads(texto)
    except json.JSONDecodeError:
        pass

    match = re.search(r"\{.*\}", texto, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass

    raise ValueError(f"El modelo no regresó un JSON válido. Respuesta cruda:\n{texto[:2000]}")


def extraer_planeacion_estructurada(planeacion: Planeacion, historial: list[Mensaje]) -> dict:
    conversacion_texto = "\n".join(f"{m.role}: {m.content}" for m in historial)

    contexto = f"""Datos fijos de la planeación (no los inventes, úsalos tal cual):
- Grado: {planeacion.grado}° de primaria, grupo "{planeacion.grupo}"
- Campo formativo: {planeacion.campo_formativo}
- Contenido NEM: {planeacion.contenido}
- Número de sesiones: {planeacion.sesiones}
- PDA (Proceso de Desarrollo de Aprendizaje):
{planeacion.pda}
- Tema/objetivo que quiere abordar el maestro: {planeacion.tema}

Conversación completa entre el maestro y Cuali:
{conversacion_texto}
"""

    messages = [
        {"role": "system", "content": _EXTRACCION_SYSTEM_PROMPT},
        {"role": "user", "content": contexto},
    ]

    respuesta_cruda = chat_completion_json(messages=messages, temperature=0.2, max_tokens=3000)
    datos = _extraer_json(respuesta_cruda)

    # Aseguramos que el número de días coincida con planeacion.sesiones,
    # por si el modelo no respetó la instrucción.
    dias = datos.get("dias", [])
    if len(dias) < planeacion.sesiones:
        faltantes = planeacion.sesiones - len(dias)
        for i in range(faltantes):
            dias.append({
                "etapa": "",
                "sesion": f"Sesión {len(dias) + i + 1}",
                "actividades": "(Pendiente de definir — platica más con Cuali para completar esta sesión.)",
                "recursos": "",
                "tarea": "",
            })
    datos["dias"] = dias[: planeacion.sesiones]

    return {
        "grado": planeacion.grado,
        "grupo": planeacion.grupo,
        "campo_formativo": planeacion.campo_formativo,
        "contenido": planeacion.contenido,
        "pda": planeacion.pda,
        "tema": planeacion.tema,
        "metodologia": datos.get("metodologia", ""),
        "ejes_articuladores": datos.get("ejes_articuladores", []),
        "titulo_proyecto": datos.get("titulo_proyecto", ""),
        "intencion_didactica": datos.get("intencion_didactica", ""),
        "proposito": datos.get("proposito", ""),
        "dias": datos["dias"],
        "evaluacion": datos.get("evaluacion", ""),
    }


def generar_respuesta_general(
    historial: list,
    nuevo_mensaje: str,
    adjuntos_nuevos: list[dict] | None = None,
) -> tuple[str, list[dict]]:
    grado_detectado, texto_busqueda = _detectar_grado_y_limpiar(nuevo_mensaje)
    pregunta_con_adjuntos = _contenido_con_adjuntos(nuevo_mensaje, adjuntos_nuevos)
    try:
        return _generar_respuesta_con_rag(
            question=pregunta_con_adjuntos,
            grado=grado_detectado,
            historial=_to_history_messages(historial),
            retrieval_query=texto_busqueda,
            allow_markdown=True,
        )
    except RagNoDisponible:
        return (
            "No pude conectarme con la base de libros de la SEP en este momento. "
            "Intenta de nuevo en unos segundos.",
            [],
        )


_DIAPOSITIVAS_SYSTEM_PROMPT = """Eres un asistente que genera el contenido de una
presentación infantil para alumnos de primaria en México, alineada a la
Nueva Escuela Mexicana cuando aplique.

Reglas estrictas:
- Responde ÚNICAMENTE con un objeto JSON válido. Sin texto antes ni después,
  sin backticks, sin markdown.
- El número de objetos en "diapositivas" debe ser EXACTAMENTE el solicitado.
- Cada diapositiva lleva: un título corto (máximo 8 palabras), entre 2 y 4
  puntos de contenido (frases cortas y claras, lenguaje apropiado para niños
  de primaria), y un emoji que ilustre el tema de esa diapositiva.
- La última diapositiva debe ser de cierre: repaso, pregunta al grupo o
  actividad rápida.
- Si se te proporciona el contenido de un documento adjunto, basa las
  diapositivas en ese material.
- No repitas el mismo emoji en todas las diapositivas.

Responde con este JSON exacto:
{
  "diapositivas": [
    { "titulo": "string", "puntos": ["string", "string"], "emoji": "un solo emoji" }
  ]
}"""


def generar_contenido_diapositivas(
    titulo: str,
    descripcion: str,
    num_diapositivas: int,
    texto_adjunto: str | None = None,
) -> dict:
    contexto = f"""Genera el contenido para una presentación de {num_diapositivas} diapositivas.

Título / tema: {titulo}
Lo que debe incluir según el maestro: {descripcion}
"""
    if texto_adjunto:
        contexto += f"""
--- Material de referencia adjuntado por el maestro ---
{texto_adjunto}
--- Fin del material ---
"""

    messages = [
        {"role": "system", "content": _DIAPOSITIVAS_SYSTEM_PROMPT},
        {"role": "user", "content": contexto},
    ]

    respuesta_cruda = chat_completion_json(messages=messages, temperature=0.4, max_tokens=2500)
    datos = _extraer_json(respuesta_cruda)

    diapositivas = datos.get("diapositivas", [])
    # Ajuste defensivo por si el modelo no respetó el número exacto
    if len(diapositivas) > num_diapositivas:
        diapositivas = diapositivas[:num_diapositivas]
    while len(diapositivas) < num_diapositivas:
        diapositivas.append({
            "titulo": "Para reflexionar",
            "puntos": ["¿Qué fue lo que más te gustó del tema?", "Coméntalo con tu grupo."],
            "emoji": "💭",
        })

    return {"diapositivas": diapositivas}


_CUESTIONARIO_SYSTEM_PROMPT = """Eres un asistente que redacta cuestionarios de
evaluación para primaria en México.

Reglas estrictas:
- Responde ÚNICAMENTE con un objeto JSON válido. Sin texto antes ni después,
  sin backticks, sin markdown.
- El número de objetos en "preguntas" debe ser EXACTAMENTE el solicitado.
- Cada pregunta tiene un campo "tipo": "opcion_multiple", "verdadero_falso"
  o "abierta".
- Si "tipo_preguntas" solicitado es "mixto", combina los tres tipos de forma
  equilibrada entre las preguntas. Si es uno específico, TODAS las preguntas
  deben ser de ese tipo.
- Para "opcion_multiple": incluye "opciones" (lista de 4 opciones como
  strings, sin prefijo de letra) y "respuesta_correcta" (el texto exacto de
  la opción correcta, tal cual aparece en "opciones").
- Para "verdadero_falso": "opciones" debe ser ["Verdadero", "Falso"] y
  "respuesta_correcta" es "Verdadero" o "Falso".
- Para "abierta": no incluyas "opciones"; en su lugar incluye
  "respuesta_sugerida" (una respuesta modelo breve, para guía del maestro,
  no para el alumno).
- Si se te proporciona el contenido de un documento adjunto, basa las
  preguntas en ese material.
- Lenguaje claro y apropiado para primaria. No uses emojis.

Responde con este JSON exacto:
{
  "preguntas": [
    {
      "pregunta": "string",
      "tipo": "opcion_multiple | verdadero_falso | abierta",
      "opciones": ["string", "..."],
      "respuesta_correcta": "string",
      "respuesta_sugerida": "string (solo si tipo es abierta)"
    }
  ]
}"""


def generar_contenido_cuestionario(
    titulo: str,
    descripcion: str,
    tipo_preguntas: str,
    num_preguntas: int,
    texto_adjunto: str | None = None,
) -> dict:
    contexto = f"""Genera un cuestionario de {num_preguntas} preguntas.

Título / tema: {titulo}
Qué se quiere evaluar, según el maestro: {descripcion}
Tipo de preguntas solicitado: {tipo_preguntas}
"""
    if texto_adjunto:
        contexto += f"""
--- Material de referencia adjuntado por el maestro ---
{texto_adjunto}
--- Fin del material ---
"""

    messages = [
        {"role": "system", "content": _CUESTIONARIO_SYSTEM_PROMPT},
        {"role": "user", "content": contexto},
    ]

    respuesta_cruda = chat_completion_json(messages=messages, temperature=0.4, max_tokens=3000)
    datos = _extraer_json(respuesta_cruda)
    return _normalizar_preguntas(datos, num_preguntas)


def ajustar_contenido_cuestionario(
    titulo: str,
    tipo_preguntas: str,
    num_preguntas: int,
    preguntas_actuales: list[dict],
    instrucciones: str,
) -> dict:
    contexto = f"""Este es el cuestionario actual (título: "{titulo}", tipo: {tipo_preguntas}, {num_preguntas} preguntas):

{json.dumps({"preguntas": preguntas_actuales}, ensure_ascii=False, indent=2)}

El maestro pidió este cambio: {instrucciones}

Regenera el cuestionario completo aplicando ese cambio. Mantén el mismo
número de preguntas ({num_preguntas}) salvo que el maestro haya pedido
explícitamente cambiar la cantidad."""

    messages = [
        {"role": "system", "content": _CUESTIONARIO_SYSTEM_PROMPT},
        {"role": "user", "content": contexto},
    ]

    respuesta_cruda = chat_completion_json(messages=messages, temperature=0.4, max_tokens=3000)
    datos = _extraer_json(respuesta_cruda)
    return _normalizar_preguntas(datos, num_preguntas)


def _normalizar_preguntas(datos: dict, num_preguntas: int) -> dict:
    preguntas = datos.get("preguntas", [])
    if len(preguntas) > num_preguntas:
        preguntas = preguntas[:num_preguntas]
    while len(preguntas) < num_preguntas:
        preguntas.append({
            "pregunta": "Pregunta pendiente de completar.",
            "tipo": "abierta",
            "respuesta_sugerida": "",
        })
    return {"preguntas": preguntas}


_MAPA_MENTAL_SYSTEM_PROMPT = """Eres un asistente que organiza un tema en la
estructura de un mapa mental para alumnos de primaria en México.

Reglas estrictas:
- Responde ÚNICAMENTE con un objeto JSON válido. Sin texto antes ni después,
  sin backticks, sin markdown.
- "tema_central" es el nodo del centro: 1 a 4 palabras.
- Genera entre 4 y 6 ramas principales. Cada "titulo" de rama debe ser corto
  (máximo 4 palabras).
- Cada rama tiene entre 2 y 4 "subpuntos", cada uno corto (máximo 6 palabras).
- Si se te da un documento adjunto, basa el mapa en su contenido real.
- Lenguaje claro y apropiado para primaria. No uses emojis.

Responde con este JSON exacto:
{
  "tema_central": "string",
  "ramas": [
    { "titulo": "string", "subpuntos": ["string", "string"] }
  ]
}"""


def generar_contenido_mapa_mental(
    tema: str,
    resumen: str,
    texto_adjunto: str | None = None,
) -> dict:
    contexto = f"""Tema: {tema}
Resumen o enfoque que quiere el maestro: {resumen}
"""
    if texto_adjunto:
        contexto += f"""
--- Material de referencia adjuntado por el maestro ---
{texto_adjunto}
--- Fin del material ---
"""

    messages = [
        {"role": "system", "content": _MAPA_MENTAL_SYSTEM_PROMPT},
        {"role": "user", "content": contexto},
    ]

    respuesta_cruda = chat_completion_json(messages=messages, temperature=0.5, max_tokens=1500)
    datos = _extraer_json(respuesta_cruda)

    if not datos.get("tema_central"):
        datos["tema_central"] = tema
    if not datos.get("ramas"):
        datos["ramas"] = []

    return datos


_MEMORAMA_SYSTEM_PROMPT = """Eres un asistente que crea el contenido de un juego
de memorama educativo para primaria en México.

Reglas estrictas:
- Responde ÚNICAMENTE con un objeto JSON válido. Sin texto antes ni después,
  sin backticks, sin markdown.
- Genera EXACTAMENTE el número de pares solicitado.
- Cada par tiene "concepto" (una palabra o frase muy corta, máximo 3
  palabras) y "definicion" (una explicación breve, máximo 10 palabras) que
  correspondan entre sí.
- No repitas conceptos. Cada par debe ser claramente distinto de los demás
  para que emparejarlos no sea ambiguo.
- Si se te da un documento adjunto, basa los pares en su contenido real.
- Lenguaje claro y apropiado para primaria. No uses emojis.

Responde con este JSON exacto:
{
  "pares": [
    { "concepto": "string", "definicion": "string" }
  ]
}"""


def generar_contenido_memorama(
    tema: str,
    num_pares: int,
    texto_adjunto: str | None = None,
) -> dict:
    contexto = f"""Tema: {tema}
Número de pares a generar: {num_pares}
"""
    if texto_adjunto:
        contexto += f"""
--- Material de referencia adjuntado por el maestro ---
{texto_adjunto}
--- Fin del material ---
"""

    messages = [
        {"role": "system", "content": _MEMORAMA_SYSTEM_PROMPT},
        {"role": "user", "content": contexto},
    ]

    respuesta_cruda = chat_completion_json(messages=messages, temperature=0.5, max_tokens=1800)
    datos = _extraer_json(respuesta_cruda)

    pares = datos.get("pares", [])
    if len(pares) > num_pares:
        pares = pares[:num_pares]

    return {"pares": pares}


_SOPA_LETRAS_SYSTEM_PROMPT = """Eres un asistente que elige palabras para una
sopa de letras educativa de primaria en México.

Reglas estrictas:
- Responde ÚNICAMENTE con un objeto JSON válido. Sin texto antes ni después,
  sin backticks, sin markdown.
- Genera EXACTAMENTE el número de palabras solicitado.
- Cada palabra debe ser UNA sola palabra (sin espacios), en español, entre 3
  y 12 letras, relacionada directamente con el tema.
- No repitas palabras. No uses siglas ni abreviaturas.
- Si se te da un documento adjunto, elige las palabras de ahí.

Responde con este JSON exacto:
{ "palabras": ["string", "string"] }"""


def generar_contenido_sopa_letras(
    tema: str,
    num_palabras: int,
    texto_adjunto: str | None = None,
) -> dict:
    contexto = f"Tema: {tema}\nNúmero de palabras a generar: {num_palabras}\n"
    if texto_adjunto:
        contexto += f"""
--- Material de referencia adjuntado por el maestro ---
{texto_adjunto}
--- Fin del material ---
"""

    messages = [
        {"role": "system", "content": _SOPA_LETRAS_SYSTEM_PROMPT},
        {"role": "user", "content": contexto},
    ]

    respuesta_cruda = chat_completion_json(messages=messages, temperature=0.5, max_tokens=800)
    datos = _extraer_json(respuesta_cruda)

    palabras = datos.get("palabras", [])
    if len(palabras) > num_palabras:
        palabras = palabras[:num_palabras]
    return {"palabras": palabras}


_RULETA_SYSTEM_PROMPT = """Eres un asistente que redacta preguntas de trivia
educativa para primaria en México, para una dinámica de ruleta en clase.

Reglas estrictas:
- Responde ÚNICAMENTE con un objeto JSON válido. Sin texto antes ni después,
  sin backticks, sin markdown.
- Genera EXACTAMENTE el número de preguntas solicitado.
- Cada pregunta es corta y clara, con una "respuesta" breve (máximo 8
  palabras).
- No repitas preguntas ni hagas dos preguntas sobre lo mismo.
- Si se te da un documento adjunto, basa las preguntas en su contenido real.
- Lenguaje apropiado para primaria. No uses emojis.

Responde con este JSON exacto:
{ "preguntas": [ { "pregunta": "string", "respuesta": "string" } ] }"""


def generar_contenido_ruleta(
    tema: str,
    num_preguntas: int,
    texto_adjunto: str | None = None,
) -> dict:
    contexto = f"Tema: {tema}\nNúmero de preguntas a generar: {num_preguntas}\n"
    if texto_adjunto:
        contexto += f"""
--- Material de referencia adjuntado por el maestro ---
{texto_adjunto}
--- Fin del material ---
"""

    messages = [
        {"role": "system", "content": _RULETA_SYSTEM_PROMPT},
        {"role": "user", "content": contexto},
    ]

    respuesta_cruda = chat_completion_json(messages=messages, temperature=0.5, max_tokens=1500)
    datos = _extraer_json(respuesta_cruda)

    preguntas = datos.get("preguntas", [])
    if len(preguntas) > num_preguntas:
        preguntas = preguntas[:num_preguntas]
    return {"preguntas": preguntas}


_CRUCIGRAMA_SYSTEM_PROMPT = """Eres un asistente que elige palabras y pistas
para un crucigrama educativo de primaria en México.

Reglas estrictas:
- Responde ÚNICAMENTE con un objeto JSON válido. Sin texto antes ni después,
  sin backticks, sin markdown.
- Genera EXACTAMENTE el número de palabras solicitado.
- Cada palabra debe ser UNA sola palabra (sin espacios), en español, entre 3
  y 10 letras, relacionada con el tema.
- Elige palabras que compartan varias letras entre sí (por ejemplo que varias
  contengan la misma vocal en distintas posiciones), para que puedan cruzarse
  bien en un crucigrama. Evita que todas empiecen con la misma letra.
- Cada palabra lleva una "pista": una definición o descripción breve (máximo
  12 palabras) que NO debe contener la palabra misma.
- No repitas palabras.
- Si se te da un documento adjunto, elige las palabras y pistas de ahí.

Responde con este JSON exacto:
{ "palabras": [ { "palabra": "string", "pista": "string" } ] }"""


def generar_contenido_crucigrama(
    tema: str,
    num_palabras: int,
    texto_adjunto: str | None = None,
) -> dict:
    contexto = f"Tema: {tema}\nNúmero de palabras a generar: {num_palabras}\n"
    if texto_adjunto:
        contexto += f"""
--- Material de referencia adjuntado por el maestro ---
{texto_adjunto}
--- Fin del material ---
"""

    messages = [
        {"role": "system", "content": _CRUCIGRAMA_SYSTEM_PROMPT},
        {"role": "user", "content": contexto},
    ]

    respuesta_cruda = chat_completion_json(messages=messages, temperature=0.5, max_tokens=1500)
    datos = _extraer_json(respuesta_cruda)

    palabras = datos.get("palabras", [])
    if len(palabras) > num_palabras:
        palabras = palabras[:num_palabras]
    return {"palabras": palabras}


_AHORCADO_SYSTEM_PROMPT = """Eres un asistente que elige palabras para un
juego de ahorcado educativo de primaria en México.

Reglas estrictas:
- Responde ÚNICAMENTE con un objeto JSON válido. Sin texto antes ni después,
  sin backticks, sin markdown.
- Genera EXACTAMENTE el número de palabras solicitado.
- Cada palabra debe ser UNA sola palabra (sin espacios), en español, entre 4
  y 12 letras, relacionada con el tema.
- Cada palabra lleva una "pista": una definición breve (máximo 12 palabras)
  que NO debe contener la palabra misma ni ninguna de sus letras consecutivas
  formando parte visible de la respuesta.
- No repitas palabras.
- Si se te da un documento adjunto, elige las palabras y pistas de ahí.

Responde con este JSON exacto:
{ "palabras": [ { "palabra": "string", "pista": "string" } ] }"""


def generar_contenido_ahorcado(
    tema: str,
    num_palabras: int,
    texto_adjunto: str | None = None,
) -> dict:
    contexto = f"Tema: {tema}\nNúmero de palabras a generar: {num_palabras}\n"
    if texto_adjunto:
        contexto += f"""
--- Material de referencia adjuntado por el maestro ---
{texto_adjunto}
--- Fin del material ---
"""

    messages = [
        {"role": "system", "content": _AHORCADO_SYSTEM_PROMPT},
        {"role": "user", "content": contexto},
    ]

    respuesta_cruda = chat_completion_json(messages=messages, temperature=0.5, max_tokens=1200)
    datos = _extraer_json(respuesta_cruda)

    palabras = datos.get("palabras", [])
    if len(palabras) > num_palabras:
        palabras = palabras[:num_palabras]
    return {"palabras": palabras}


_VERDADERO_FALSO_SYSTEM_PROMPT = """Eres un asistente que redacta afirmaciones
de verdadero/falso para primaria en México, para un juego contrarreloj.

Reglas estrictas:
- Responde ÚNICAMENTE con un objeto JSON válido. Sin texto antes ni después,
  sin backticks, sin markdown.
- Genera EXACTAMENTE el número de afirmaciones solicitado.
- Cada afirmación es una oración corta y clara (máximo 20 palabras),
  claramente verdadera o claramente falsa según el tema (nada ambiguo).
- Aproximadamente la mitad deben ser verdaderas y la mitad falsas, en orden
  aleatorio.
- Si se te da un documento adjunto, basa las afirmaciones en su contenido.
- Lenguaje apropiado para primaria. No uses emojis.

Responde con este JSON exacto:
{ "afirmaciones": [ { "afirmacion": "string", "correcta": true } ] }"""


def generar_contenido_verdadero_falso(
    tema: str,
    num_afirmaciones: int,
    texto_adjunto: str | None = None,
) -> dict:
    contexto = f"Tema: {tema}\nNúmero de afirmaciones a generar: {num_afirmaciones}\n"
    if texto_adjunto:
        contexto += f"""
--- Material de referencia adjuntado por el maestro ---
{texto_adjunto}
--- Fin del material ---
"""

    messages = [
        {"role": "system", "content": _VERDADERO_FALSO_SYSTEM_PROMPT},
        {"role": "user", "content": contexto},
    ]

    respuesta_cruda = chat_completion_json(messages=messages, temperature=0.5, max_tokens=1500)
    datos = _extraer_json(respuesta_cruda)

    afirmaciones = datos.get("afirmaciones", [])
    if len(afirmaciones) > num_afirmaciones:
        afirmaciones = afirmaciones[:num_afirmaciones]
    return {"afirmaciones": afirmaciones}


_CARTEL_SYSTEM_PROMPT = """Eres un asistente que redacta el contenido de un
cartel o infografía educativa para primaria en México.

Reglas estrictas:
- Responde ÚNICAMENTE con un objeto JSON válido. Sin texto antes ni después,
  sin backticks, sin markdown.
- "titulo": corto y llamativo (máximo 6 palabras).
- "subtitulo": una frase breve que complemente el título (máximo 12 palabras),
  o cadena vacía si no aplica.
- "puntos": entre 3 y 6 frases muy cortas (máximo 8 palabras cada una) con la
  información clave del cartel.
- "emoji": un solo emoji que ilustre el tema.
- Lenguaje claro, apropiado para primaria. No uses emojis fuera del campo
  "emoji".

Responde con este JSON exacto:
{ "titulo": "string", "subtitulo": "string", "puntos": ["string"], "emoji": "un solo emoji" }"""


def generar_contenido_cartel(
    tema: str,
    descripcion: str,
    texto_adjunto: str | None = None,
) -> dict:
    contexto = f"Tema del cartel: {tema}\nQué debe transmitir: {descripcion}\n"
    if texto_adjunto:
        contexto += f"""
--- Material de referencia adjuntado por el maestro ---
{texto_adjunto}
--- Fin del material ---
"""

    messages = [
        {"role": "system", "content": _CARTEL_SYSTEM_PROMPT},
        {"role": "user", "content": contexto},
    ]

    respuesta_cruda = chat_completion_json(messages=messages, temperature=0.5, max_tokens=800)
    datos = _extraer_json(respuesta_cruda)
    return {
        "titulo": datos.get("titulo", tema),
        "subtitulo": datos.get("subtitulo", ""),
        "puntos": datos.get("puntos", []),
        "emoji": datos.get("emoji", "⭐"),
    }


_LABORATORIO_SYSTEM_PROMPT = """Eres Cuali, asistente pedagógico para maestros
de primaria en México. Estás en el "Laboratorio": un espacio libre donde el
maestro puede pedirte que le ayudes a crear cualquier recurso didáctico que
no encaje en las categorías ya existentes (diapositivas, cuestionarios, mapas
mentales, juegos, carteles).

Ayuda de forma concreta y práctica. Haz preguntas breves si necesitas más
contexto, pero no abuses de las preguntas — prioriza proponer contenido
usable. Cuando el maestro parezca satisfecho con una idea, ofrécete a
resumirla para que la pueda descargar como documento.

No uses markdown (nada de asteriscos, tablas con barras, encabezados con #).
Usa texto plano en párrafos cortos, y guiones (-) si necesitas enumerar.
No uses emojis."""


def generar_respuesta_laboratorio(historial: list[dict], nuevo_mensaje: str) -> str:
    messages = [{"role": "system", "content": _LABORATORIO_SYSTEM_PROMPT}]
    for m in historial:
        messages.append({"role": m["role"], "content": m["content"]})
    messages.append({"role": "user", "content": nuevo_mensaje})

    return chat_completion(messages=messages, temperature=0.6, max_tokens=800)


_LABORATORIO_DOC_SYSTEM_PROMPT = """Eres un asistente que convierte una
conversación de brainstorming entre un maestro y Cuali en un documento final
descargable.

Reglas estrictas:
- Responde ÚNICAMENTE con un objeto JSON válido. Sin texto antes ni después,
  sin backticks, sin markdown.
- "titulo": título corto del recurso creado.
- "contenido": el cuerpo del documento en texto plano, organizado en
  párrafos. Usa líneas que empiecen con "- " para listas cuando ayude a la
  claridad. No repitas la conversación completa; sintetiza el resultado final
  al que llegaron, no el proceso de ir y venir.
- No uses markdown con asteriscos ni encabezados con #.

Responde con este JSON exacto:
{ "titulo": "string", "contenido": "string" }"""


def generar_documento_laboratorio(historial: list[dict]) -> dict:
    conversacion_texto = "\n".join(f"{m['role']}: {m['content']}" for m in historial)

    messages = [
        {"role": "system", "content": _LABORATORIO_DOC_SYSTEM_PROMPT},
        {"role": "user", "content": f"Conversación:\n{conversacion_texto}"},
    ]

    respuesta_cruda = chat_completion_json(messages=messages, temperature=0.3, max_tokens=2000)
    datos = _extraer_json(respuesta_cruda)
    return {
        "titulo": datos.get("titulo", "Recurso del Laboratorio"),
        "contenido": datos.get("contenido", ""),
    }