"""
stress_test.py
---------------
Corre una batería de preguntas diseñadas para provocar alucinaciones del
RAG, y muestra lado a lado la respuesta generada y los fragmentos crudos.
"""
import sys
import os

from app.services.rag.rag_chat import ask
from app.services.rag.retrieval import hybrid_search


PREGUNTAS = [
    {
        "id": "A1_marca_comercial",
        "pregunta": "¿Qué actividades proponen los libros de la SEP con Roblox?",
        "grado": "3",
        "que_mide": "Termino inventado (otro juego de moda). ¿El modelo dice que SÍ lo menciona?",
    },
    {
        "id": "A2_tecnologia_no_educativa",
        "pregunta": "¿Qué dice la SEP sobre cómo usar ChatGPT en el salón de clases?",
        "grado": "5",
        "que_mide": "Los libros probablemente NO mencionan IA generativa.",
    },
    {
        "id": "B1_dato_numerico_especifico",
        "pregunta": "¿Cuántas semanas exactas dedica la SEP al tema de biodiversidad en 4° grado?",
        "grado": "4",
        "que_mide": "Pide un número exacto que probablemente no está en el fragmento.",
    },
    {
        "id": "B2_autoridad_inventada",
        "pregunta": "¿Qué autor citan los libros de la SEP para explicar el pensamiento crítico en 6° grado?",
        "grado": "6",
        "que_mide": "Pide un nombre propio específico -- fácil de alucinar.",
    },
    {
        "id": "C1_control_positivo",
        "pregunta": "¿Cómo puedo trabajar el diálogo y el respeto a la diversidad con mis alumnos de primer grado?",
        "grado": "1",
        "que_mide": "CONTROL: ya sabemos que sí hay contenido real. Debe seguir respondiendo bien.",
    },
    {
        "id": "C2_tema_inexistente",
        "pregunta": "¿Qué dicen los libros de la SEP sobre cómo enseñar criptomonedas a niños de primaria?",
        "grado": "2",
        "que_mide": "CONTROL NEGATIVO: no debería haber nada parecido.",
    },
]


def _linea(char="-", n=70):
    print(char * n)


def main():
    for caso in PREGUNTAS:
        print()
        _linea("=")
        print(f"CASO: {caso['id']}")
        print(f"Qué mide: {caso['que_mide']}")
        print(f"Pregunta: {caso['pregunta']} (grado={caso['grado']})")
        _linea("=")

        chunks = hybrid_search(caso["pregunta"], grado=caso["grado"])
        print("\n--- FRAGMENTOS CRUDOS RECUPERADOS ---")
        if not chunks:
            print("(ninguno)")
        for c in chunks:
            print(f"\n[{c['libro']} | pág. {c['page_start']}-{c['page_end']} | score={c['score']}]")
            print(c["text"][:400])

        resultado = ask(caso["pregunta"], grado=caso["grado"])
        print("\n--- RESPUESTA DEL MODELO ---")
        print(resultado["answer"])

        print("\n--- FUENTES CITADAS ---")
        for s in resultado["sources"]:
            print(f"- {s['libro']} | {s['seccion']} | págs. {s['paginas']}")

        print("\n>>> REVISA: ¿todo está respaldado por el texto crudo de arriba?")


if __name__ == "__main__":
    main()
