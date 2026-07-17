from unittest.mock import patch

from app.services.llm import generar_respuesta_general


def test_generar_respuesta_general_adapta_fuentes_del_rag():
    fake_result = {
        "answer": "respuesta de prueba",
        "sources": [
            {
                "libro": "Libro SEP",
                "materia": "Matemáticas",
                "seccion": "Lección 1",
                "paginas": "12-13",
            }
        ],
    }

    with patch("app.services.llm.rag_ask", return_value=fake_result):
        respuesta, fuentes = generar_respuesta_general([], "¿Qué dice?")

    assert respuesta == "respuesta de prueba"
    assert len(fuentes) == 1
    assert fuentes[0]["documento"] == "Libro SEP"
    assert fuentes[0]["campo"] == "Matemáticas"
    assert fuentes[0]["pagina"] == "12-13"
