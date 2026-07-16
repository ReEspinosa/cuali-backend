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

Ayuda a construir actividades didácticas concretas para cada sesión, alineadas
al PDA de arriba. Sé breve, concreto y práctico — el maestro tiene poco tiempo.
No repitas el PDA completo de vuelta en tus respuestas; ya lo tienes como
contexto, úsalo para fundamentar tus sugerencias sin citarlo textualmente.
No uses emojis."""


def generar_respuesta_chat(planeacion: Planeacion, historial: list[Mensaje], nuevo_mensaje: str) -> str:
    system_prompt = _system_prompt(planeacion)

    messages = [{"role": "system", "content": system_prompt}]
    for m in historial:
        messages.append({"role": m.role, "content": m.content})
    messages.append({"role": "user", "content": nuevo_mensaje})

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

Nota: todavía no tienes acceso a una base de documentos oficiales de la SEP
para citar fuentes exactas (eso se conecta en un siguiente paso). Si no
sabes algo con certeza, dilo en vez de inventar una referencia o número de
página."""


def generar_respuesta_general(historial: list, nuevo_mensaje: str) -> tuple[str, list[dict]]:
    """
    generar_respuesta_general() ya llama al modelo real. Las "fuentes" quedan
    vacías por ahora porque el RAG con ChromaDB sobre documentos SEP todavía
    no está conectado — cuando lo esté, esta función debe buscar los
    fragmentos relevantes, pasarlos como contexto, y regresar las fuentes
    reales recuperadas (documento, campo, página) en vez de [].
    """
    messages = [{"role": "system", "content": _SYSTEM_PROMPT_GENERAL}]
    for m in historial:
        messages.append({"role": m.role, "content": m.content})
    messages.append({"role": "user", "content": nuevo_mensaje})

    respuesta = chat_completion(messages=messages, temperature=0.5, max_tokens=800)
    return respuesta, []