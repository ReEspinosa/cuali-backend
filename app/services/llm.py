"""
Funciones de LLM del proyecto. Todas son STUB por ahora (no llaman a ningún
modelo real) para que el resto del flujo se pueda probar de inmediato.
Cuando conectemos tu backend real con RAG (ChromaDB + tu API OpenAI-compatible),
reemplaza el cuerpo de cada función marcada con TODO.

1. generar_respuesta_chat() — chat de una planeación específica, con el
   contexto de esa planeación (grado, campo, PDA, etc.) como system prompt oculto.

2. extraer_planeacion_estructurada() — prompt "de extracción": convierte la
   conversación completa en el JSON que alimenta la plantilla del PDF.

3. generar_respuesta_general() — chat libre "Chat con Cuali", que además
   regresa las fuentes (documento SEP + página) en las que se basó la respuesta.
"""

from app.models.planeacion import Mensaje, Planeacion


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
contexto, úsalo para fundamentar tus sugerencias sin citarlo textualmente."""


def generar_respuesta_chat(planeacion: Planeacion, historial: list[Mensaje], nuevo_mensaje: str) -> str:
    system_prompt = _system_prompt(planeacion)
    _ = system_prompt
    return (
        f'(Respuesta simulada) Entendido, tomando en cuenta "{planeacion.tema}" '
        f"para {planeacion.campo_formativo}. Aquí conectaríamos con el modelo real "
        f'para responder a: "{nuevo_mensaje}"'
    )


def extraer_planeacion_estructurada(planeacion: Planeacion, historial: list[Mensaje]) -> dict:
    _ = historial

    sesiones_ejemplo = [
        {
            "numero": i + 1,
            "objetivo": f"Objetivo de la sesión {i + 1} (a extraer de la conversación real).",
            "actividades": [
                "Actividad de apertura (a extraer de la conversación real).",
                "Actividad de desarrollo (a extraer de la conversación real).",
                "Actividad de cierre (a extraer de la conversación real).",
            ],
            "materiales": ["Material de ejemplo"],
            "evaluacion": "Criterio de evaluación formativa (a extraer de la conversación real).",
        }
        for i in range(planeacion.sesiones)
    ]

    return {
        "grado": planeacion.grado,
        "grupo": planeacion.grupo,
        "campo_formativo": planeacion.campo_formativo,
        "contenido": planeacion.contenido,
        "tema": planeacion.tema,
        "sesiones": sesiones_ejemplo,
    }


def generar_respuesta_general(historial: list, nuevo_mensaje: str) -> tuple[str, list[dict]]:
    """
    TODO: STUB. Cuando conectemos RAG real con ChromaDB, esta función debe
    buscar en la colección de documentos SEP los fragmentos relevantes,
    pasarlos como contexto al LLM, y regresar la respuesta junto con las
    fuentes reales recuperadas (documento, campo formativo, página).
    """
    _ = historial

    respuesta = (
        f'(Respuesta simulada) Aquí conectaríamos con el modelo real y el RAG '
        f'para responder a: "{nuevo_mensaje}"'
    )

    fuentes_ejemplo = [
        {
            "documento": "Programa Sintético Fase 5",
            "campo": "Saberes y Pensamiento Científico",
            "pagina": 42,
        },
    ]

    return respuesta, fuentes_ejemplo