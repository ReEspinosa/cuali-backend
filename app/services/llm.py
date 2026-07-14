"""
Dos funciones que corresponden a los dos prompts que platicamos:

1. generar_respuesta_chat() — el prompt "conversacional". Recibe el contexto
   de la planeación (system prompt oculto) + el historial, y genera la
   siguiente respuesta de Cuali.

2. extraer_planeacion_estructurada() — el prompt "de extracción". Se dispara
   solo al darle a "Generar planeación": toma TODA la conversación y la
   traduce a un JSON con schema fijo, que alimenta la plantilla del PDF.

TODO: ambas funciones son un STUB. Cuando conectemos tu backend real con RAG
(ChromaDB + tu API OpenAI-compatible), reemplaza el cuerpo de estas funciones
por las llamadas reales.
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

    # TODO: reemplazar por la llamada real, por ejemplo:
    #
    # from openai import OpenAI
    # client = OpenAI(base_url=settings.openai_base_url, api_key=settings.openai_api_key)
    # messages = [{"role": "system", "content": system_prompt}]
    # for m in historial:
    #     messages.append({"role": m.role, "content": m.content})
    # messages.append({"role": "user", "content": nuevo_mensaje})
    # response = client.chat.completions.create(model=settings.openai_model, messages=messages)
    # return response.choices[0].message.content

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