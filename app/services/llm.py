"""
Funciones de LLM del proyecto.

generar_respuesta_chat() y extraer_planeacion_estructurada() ya llaman al
modelo real (LM Studio / UNAM). generar_respuesta_general() sigue en stub —
depende del RAG con ChromaDB, que todavía no está conectado.
"""

import json
import re

from app.models.planeacion import Mensaje, Planeacion
from app.services.llm_client import chat_completion, chat_completion_json


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

Reglas de formato (muy importantes):
- Escribe en texto plano, en párrafos cortos. NUNCA uses formato markdown:
  nada de asteriscos (*, **), almohadillas (#), ni tablas con barras (|).
- Si necesitas enumerar, usa guiones simples (-) o números (1., 2.).
- No uses emojis.
- Sé cálido pero breve; el maestro tiene poco tiempo.

Tu papel en esta conversación es GUIAR al maestro con preguntas para reunir
la información necesaria para su planeación, no darle la planeación completa
en el chat. La planeación final se genera aparte con un botón."""


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


def generar_respuesta_chat(
    planeacion: Planeacion,
    historial: list[Mensaje],
    nuevo_mensaje: str,
    adjuntos_nuevos: list[dict] | None = None,
) -> str:
    num_mensajes_maestro = sum(1 for m in historial if m.role == "user") + 1

    system_prompt = _system_prompt(planeacion) + "\n\n" + _directiva_de_fase(num_mensajes_maestro)

    messages = [{"role": "system", "content": system_prompt}]
    for m in historial:
        messages.append({"role": m.role, "content": _contenido_con_adjuntos(m.content, m.adjuntos)})
    messages.append({"role": "user", "content": _contenido_con_adjuntos(nuevo_mensaje, adjuntos_nuevos)})

    return chat_completion(messages=messages, temperature=0.5, max_tokens=800)


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


_SYSTEM_PROMPT_GENERAL = """Eres Cuali, asistente pedagógico para maestros de primaria en
México, alineado a la Nueva Escuela Mexicana (NEM). Ayudas con dudas sobre
planeación didáctica, el programa sintético, campos formativos, PDA, y
estrategias pedagógicas en general. Sé breve, concreto y práctico. No uses
emojis.

Reglas de formato (muy importantes, tu respuesta se renderiza como Markdown real):
- Puedes usar **negritas**, listas con "-" o "1.", y encabezados con "##"
  cuando ayuden a organizar la respuesta. Úsalos con moderación, no abuses.
- Si necesitas mostrar una tabla, usa SIEMPRE sintaxis de tabla Markdown
  válida y completa, con la fila separadora de encabezado, por ejemplo:
  | Columna A | Columna B |
  |---|---|
  | dato 1 | dato 2 |
  Nunca escribas una tabla a medias ni mezcles el formato de tabla con texto
  suelto entre celdas.
- No pongas asteriscos sueltos que no formen parte de una negrita real
  (nada de "*algo*" para énfasis simple; usa **negrita** o nada).
- No uses emojis.

Nota: todavía no tienes acceso a una base de documentos oficiales de la SEP
para citar fuentes exactas (eso se conecta en un siguiente paso). Si no
sabes algo con certeza, dilo en vez de inventar una referencia o número de
página."""


def generar_respuesta_general(
    historial: list,
    nuevo_mensaje: str,
    adjuntos_nuevos: list[dict] | None = None,
) -> tuple[str, list[dict]]:
    """
    generar_respuesta_general() ya llama al modelo real. Las "fuentes" quedan
    vacías por ahora porque el RAG con ChromaDB sobre documentos SEP todavía
    no está conectado — cuando lo esté, esta función debe buscar los
    fragmentos relevantes, pasarlos como contexto, y regresar las fuentes
    reales recuperadas (documento, campo, página) en vez de [].
    """
    messages = [{"role": "system", "content": _SYSTEM_PROMPT_GENERAL}]
    for m in historial:
        messages.append({"role": m.role, "content": _contenido_con_adjuntos(m.content, m.adjuntos)})
    messages.append({"role": "user", "content": _contenido_con_adjuntos(nuevo_mensaje, adjuntos_nuevos)})

    respuesta = chat_completion(messages=messages, temperature=0.5, max_tokens=800)
    return respuesta, []

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