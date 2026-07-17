"""
test_retrieval.py
------------------
Prueba rápida y desechable para validar que el índice vectorial (el que
acabas de construir con tus 59 chunks de prueba) responde bien a una
búsqueda real.

Uso:
    python test_retrieval.py
"""
from retrieval import hybrid_search

# Cambia esto por una pregunta relacionada a algo que sepas que está en tus
# 59 chunks de prueba
pregunta = "¿cómo puedo trabajar el diálogo y el respeto a la diversidad con niños de primero de primaria?"

print(f"Pregunta: {pregunta}\n")

resultados = hybrid_search(pregunta)

if not resultados:
    print("No se encontró nada -- revisa que el índice se haya construido bien.")

for r in resultados:
    print(f"[{r['libro']}] pág. {r['page_start']}-{r['page_end']} | score={r['score']}")
    print(r["text"][:150])
    print()