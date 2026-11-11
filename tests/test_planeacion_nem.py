"""
Planeacion NEM: lo oficial lo pone el codigo, el modelo solo redacta, y el
codigo valida lo que el modelo escribio.
"""

import json
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest
from docx import Document

from app.models.planeacion import Planeacion
from app.services.planeacion_nem import generador
from app.services.planeacion_nem.catalogo import buscar_contenido, separar_pdas
from app.services.planeacion_nem.metodologias import METODOLOGIAS, repartir_fases

CONTENIDO_CARTAS = "Producción y envío de cartas personales."
FUENTES = [
    {"id": "F1", "libro": "Proyectos Comunitarios 6", "paginas": "36-38", "seccion": "Comunidades por correspondencia"},
]
CONTEXTO_RAG = "[F1] Proyectos Comunitarios 6, pág. 36-38\nCarta de Pedro a Ana-Pecas."


def _pda_oficial() -> str:
    return buscar_contenido(6, "Lenguajes", CONTENIDO_CARTAS)["pda"]


def _payload(**extra):
    datos = {
        "grado": 6,
        "campo_formativo": "Lenguajes",
        "contenido": CONTENIDO_CARTAS,
        "pda": _pda_oficial(),
        "grupo": "A",
        "sesiones": 5,
        "tema": "Escribir cartas a las familias",
        "contexto": {
            "escenario": "comunidad",
            "minutos_por_sesion": 50,
            "recursos_disponibles": "Sobres, hojas de colores, libro Proyectos Comunitarios",
            "ejes_articuladores": ["Pensamiento crítico", "Inclusión"],
            "periodo": "Del 12 al 16 de enero de 2026",
        },
    }
    datos.update(extra)
    return datos


def _encabezado_bueno(n_pdas: int) -> str:
    return json.dumps({
        "titulo_proyecto": "Cartas que cruzan distancias",
        "intencion_didactica": "Que las y los alumnos lean y analicen cartas personales reales y literarias, "
                               "comparen el correo postal y el electrónico y escriban una carta propia. "
                               "Se favorece la empatía y el vínculo con las familias de la comunidad.",
        "proposito": "Leeremos cartas reales y de libros y, en comunidades, escribiremos una carta a una persona "
                     "de nuestra familia. Al enviarla fortaleceremos nuestros lazos y aprenderemos a organizar "
                     "una carta y a escribir direcciones correctas.",
        "evaluacion": {"criterios": ["Analiza cartas"], "evidencias": ["Carta final"], "instrumentos": ["rúbrica"]},
        "rubrica": [
            {"indicador": f"Rasgo número {i}", "sobresaliente": "Lo logra con autonomía", "satisfactorio": "Lo logra con apoyo", "en_proceso": "Aún no lo logra"}
            for i in range(n_pdas)
        ],
    })


def _sesion(i: int, extra: str = "") -> dict:
    return {
        "inicio": f"Actividad {i}: les preguntaré qué cartas conocen y anotaré sus ideas en el pizarrón.",
        "desarrollo": f"Actividad {i}: en comunidades de cuatro analizarán la carta de Pedro (pág. 37). {extra}",
        # El cierre incluye la actividad del vinculo de prueba (gráfica de la
        # basura del recreo) para que la sesion lo trabaje de verdad.
        "cierre": f"Compartiremos lo aprendido, sesión {i}. Contaremos durante dos días la basura del recreo por tipo y haremos una gráfica de barras para el diagnóstico.",
        "recursos": ["Libro Proyectos Comunitarios", "Cuaderno"],
        "tarea": "Preguntar en casa por cartas antiguas.",
        "citas": ["F1", "F9"],
    }


def _sesiones_buenas(n: int, extra: str = "") -> str:
    return json.dumps({"sesiones": [_sesion(i, extra) for i in range(n)]})


def _generar(client, headers, pid, respuestas):
    with patch.object(generador, "chat_completion_json", side_effect=respuestas) as llm, \
         patch.object(generador, "buscar_fuentes", return_value=(FUENTES, CONTEXTO_RAG)):
        r = client.post(f"/planeaciones/{pid}/generar", headers=headers)
    return r, llm


# --------------------------------------------------------------------- fases

@pytest.mark.parametrize("metodologia", list(METODOLOGIAS))
@pytest.mark.parametrize("sesiones", range(1, 21))
def test_reparto_de_fases_cubre_todas_en_orden(metodologia, sesiones):
    reparto = repartir_fases(metodologia, sesiones)
    fases = METODOLOGIAS[metodologia].fases
    assert len(reparto) == sesiones
    assert all(reparto)  # ninguna sesion sin fase
    vistas = [f for grupo in reparto for f in grupo]
    sin_repetir = list(dict.fromkeys(vistas))
    assert sin_repetir == list(fases)  # todas, en orden, sin saltos


def test_pda_de_un_solo_bloque_se_separa_por_oraciones():
    assert len(separar_pdas(_pda_oficial())) == 5


# ------------------------------------------------------------- crear (datos)

def test_crear_guarda_el_pda_oficial_y_no_el_del_cliente(client, db, crear_docente):
    _, headers = crear_docente()
    inventado = "Analiza características de cartas y compara el correo (parafraseado por una IA)."
    r = client.post("/planeaciones", headers=headers, json=_payload(pda=inventado))
    assert r.status_code == 201
    p = db.get(Planeacion, r.json()["id"])
    assert p.pda.replace("\n\n", " ") == " ".join(separar_pdas(_pda_oficial()))
    assert p.metodologia == "proyectos_comunitarios"  # sugerida para Lenguajes


def test_crear_respeta_los_procesos_que_desmarco_la_docente(client, db, crear_docente):
    _, headers = crear_docente()
    dos = "\n\n".join(separar_pdas(_pda_oficial())[:2])
    r = client.post("/planeaciones", headers=headers, json=_payload(pda=dos))
    assert db.get(Planeacion, r.json()["id"]).pda == dos


def test_contenido_de_otro_campo_es_422(client, crear_docente):
    _, headers = crear_docente()
    r = client.post("/planeaciones", headers=headers, json=_payload(campo_formativo="Saberes y Pensamiento Científico"))
    assert r.status_code == 422


@pytest.mark.parametrize("contexto", [
    {"ejes_articuladores": ["Eje inventado"]},
    {"metodologia": "metodologia_inventada"},
    {"minutos_por_sesion": 5},
    {"escenario": "luna"},
])
def test_contexto_invalido_es_422(client, crear_docente, contexto):
    _, headers = crear_docente()
    assert client.post("/planeaciones", headers=headers, json=_payload(contexto=contexto)).status_code == 422


# ------------------------------------------------------------------ generar

def test_generar_produce_documento_con_lo_oficial_y_word(client, db, crear_docente):
    _, headers = crear_docente(escuela="Colegio de prueba")
    pid = client.post("/planeaciones", headers=headers, json=_payload()).json()["id"]

    r, llm = _generar(client, headers, pid, [_encabezado_bueno(5), _sesiones_buenas(5)])
    assert r.status_code == 200, r.text
    assert llm.call_count == 2  # sin reparacion
    doc = r.json()["documento"]

    assert doc["contenido"] == CONTENIDO_CARTAS
    assert doc["pdas"] == separar_pdas(_pda_oficial())
    assert doc["metodologia_nombre"] == "Aprendizaje basado en proyectos comunitarios"
    assert [s["etiqueta"] for s in doc["sesiones"]] == ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes"]
    assert doc["sesiones"][0]["fases"][0] == "Fase 1. Planeación: Identificación"
    assert doc["sesiones"][-1]["fases"][-1] == "Fase 3. Intervención: Avances"
    assert doc["sesiones"][0]["citas"] == ["F1"]  # F9 no existe y se descarta
    assert doc["escenario"] == "Comunidad"
    assert doc["tiempo"] == "5 sesiones de 50 min"
    assert len(doc["rubrica"]) == 5
    assert doc["pendientes"] == []

    p = db.get(Planeacion, pid)
    db.refresh(p)
    assert p.status == "finalizada"
    word = Document(p.docx_path)
    assert "COLEGIO DE PRUEBA" in "\n".join(par.text for par in word.paragraphs)
    texto = "\n".join(c.text for t in word.tables for row in t.rows for c in row.cells)
    assert "Fase 1. Planeación: Identificación" in texto
    assert "Producción y envío de cartas personales." in texto


def test_pagina_inventada_emojis_y_frases_de_chat_se_corrigen(client, crear_docente):
    _, headers = crear_docente()
    pid = client.post("/planeaciones", headers=headers, json=_payload(sesiones=2)).json()["id"]
    mala = json.dumps({"sesiones": [
        _sesion(0, "Después resolverán la pág. 250 de Integra \U0001F600."),
        _sesion(1, "¿Te gustaría que agregue otra actividad?"),
    ]})
    # Reparacion tambien falla igual: el codigo limpia lo que pueda.
    r, llm = _generar(client, headers, pid, [_encabezado_bueno(5), mala, mala])
    assert r.status_code == 200
    assert llm.call_count == 3  # encabezado + sesiones + 1 reparacion
    desarrollo0 = r.json()["documento"]["sesiones"][0]["desarrollo"]["texto"]
    assert "250" not in desarrollo0 and "[Confirmar página]" in desarrollo0
    assert "pág. 37" in desarrollo0  # pagina de una fuente real: se queda
    assert "\U0001F600" not in desarrollo0


def test_si_el_modelo_deja_huecos_se_marcan_pendientes(client, crear_docente):
    _, headers = crear_docente()
    pid = client.post("/planeaciones", headers=headers, json=_payload(sesiones=2)).json()["id"]
    incompleto = json.dumps({"sesiones": [_sesion(0)]})
    r, _ = _generar(client, headers, pid, [_encabezado_bueno(5), incompleto, incompleto])
    assert r.status_code == 200
    doc = r.json()["documento"]
    assert len(doc["sesiones"]) == 2
    assert doc["sesiones"][1]["inicio"]["texto"].startswith("[Completar")
    assert "Martes: inicio" in doc["pendientes"]


def test_json_invalido_es_502_y_libera_el_candado(client, db, crear_docente):
    _, headers = crear_docente()
    pid = client.post("/planeaciones", headers=headers, json=_payload()).json()["id"]
    r, _ = _generar(client, headers, pid, ["no es json", "tampoco"])
    assert r.status_code == 502
    p = db.get(Planeacion, pid)
    db.refresh(p)
    assert p.status == "borrador"  # no se queda "generando" para siempre


# ------------------------------------------------------------- concurrencia

def test_doble_generacion_simultanea_es_409(client, db, crear_docente):
    _, headers = crear_docente()
    pid = client.post("/planeaciones", headers=headers, json=_payload()).json()["id"]
    p = db.get(Planeacion, pid)
    p.status = "generando"
    p.updated_at = datetime.utcnow()
    db.commit()

    r, llm = _generar(client, headers, pid, [])
    assert r.status_code == 409
    assert llm.call_count == 0  # no se gasto ninguna llamada al modelo


def test_candado_viejo_se_libera(client, db, crear_docente):
    _, headers = crear_docente()
    pid = client.post("/planeaciones", headers=headers, json=_payload(sesiones=1)).json()["id"]
    p = db.get(Planeacion, pid)
    p.status = "generando"
    db.commit()
    db.query(Planeacion).filter(Planeacion.id == pid).update(
        {"updated_at": datetime.utcnow() - timedelta(minutes=30)}, synchronize_session=False
    )
    db.commit()
    r, _ = _generar(client, headers, pid, [_encabezado_bueno(5), _sesiones_buenas(1)])
    assert r.status_code == 200


# --------------------------------------------------- aislamiento y edicion

def test_otro_docente_no_ve_ni_edita_el_documento(client, crear_docente):
    _, headers_a = crear_docente()
    _, headers_b = crear_docente()
    pid = client.post("/planeaciones", headers=headers_a, json=_payload(sesiones=1)).json()["id"]
    doc = _generar(client, headers_a, pid, [_encabezado_bueno(5), _sesiones_buenas(1)])[0].json()["documento"]

    assert client.get(f"/planeaciones/{pid}/documento", headers=headers_b).status_code == 404
    assert client.put(f"/planeaciones/{pid}/documento", headers=headers_b, json=doc).status_code == 404
    assert client.get(f"/planeaciones/{pid}/docx", headers=headers_b).status_code == 404
    _, llm = _generar(client, headers_b, pid, [])
    assert llm.call_count == 0


def test_editar_no_permite_cambiar_lo_oficial(client, crear_docente):
    _, headers = crear_docente()
    pid = client.post("/planeaciones", headers=headers, json=_payload(sesiones=2)).json()["id"]
    doc = _generar(client, headers, pid, [_encabezado_bueno(5), _sesiones_buenas(2)])[0].json()["documento"]

    doc["titulo_proyecto"] = "Título editado por la docente"
    doc["pdas"] = ["PDA inventado"]
    doc["contenido"] = "Otro contenido"
    doc["sesiones"][0]["fases"] = ["Fase inventada"]
    r = client.put(f"/planeaciones/{pid}/documento", headers=headers, json=doc)
    assert r.status_code == 200
    nuevo = r.json()
    assert nuevo["titulo_proyecto"] == "Título editado por la docente"
    assert nuevo["pdas"] == separar_pdas(_pda_oficial())
    assert nuevo["contenido"] == CONTENIDO_CARTAS
    assert nuevo["sesiones"][0]["fases"][0].startswith("Fase 1")

    doc["sesiones"] = doc["sesiones"][:1]
    assert client.put(f"/planeaciones/{pid}/documento", headers=headers, json=doc).status_code == 422


# ------------------------------------------------------- integracion curricular

from app.services.planeacion_nem import integracion  # noqa: E402

VINCULO_DATOS = {"campo": "Saberes y Pensamiento Científico", "contenido": "Organización e interpretación de datos."}


def _sin_embeddings():
    return patch.object(integracion, "_puntajes_semanticos", side_effect=RuntimeError("sin indice"))


def _integracion_buena(sesion="Miércoles"):
    return json.dumps({"vinculos": [{
        "sesion": sesion,
        "actividad": "Contaremos durante dos días la basura del recreo por tipo y haremos una gráfica de barras para el diagnóstico.",
        "evidencia": "Gráfica de barras",
        "rubrica": {"indicador": "Organiza datos en una gráfica", "sobresaliente": "Lo logra con autonomía", "satisfactorio": "Lo logra con apoyo", "en_proceso": "Aún no lo logra"},
    }]})


def test_sugerencias_solo_de_otros_campos_y_oficiales():
    with _sin_embeddings():
        sug = integracion.sugerir_vinculos(
            6, "De lo Humano y lo Comunitario",
            "Recreo limpio: en el recreo se tira mucha basura en el patio y los botes no se separan",
        )
    assert 1 <= len(sug) <= 3
    campos = [s["campo"] for s in sug]
    assert "De lo Humano y lo Comunitario" not in campos
    assert len(campos) == len(set(campos))  # a lo mas uno por campo
    assert all(buscar_contenido(6, s["campo"], s["contenido"]) for s in sug)


def test_endpoint_de_sugerencias(client, crear_docente):
    _, headers = crear_docente()
    with _sin_embeddings():
        r = client.get("/planeaciones/vinculos/sugerencias", headers=headers,
                       params={"grado": 6, "campo": "Lenguajes", "contenido": CONTENIDO_CARTAS, "tema": "cuidado del agua"})
    assert r.status_code == 200
    assert all(s["campo"] != "Lenguajes" for s in r.json())


@pytest.mark.parametrize("vinculo", [
    {"campo": "Lenguajes", "contenido": "Narración de sucesos autobiográficos."},  # mismo campo
    {"campo": "Saberes y Pensamiento Científico", "contenido": "Contenido inventado"},
])
def test_vinculo_invalido_es_422(client, crear_docente, vinculo):
    _, headers = crear_docente()
    payload = _payload()
    payload["contexto"]["integracion"] = {"modo": "elegida", "contenidos": [vinculo]}
    assert client.post("/planeaciones", headers=headers, json=payload).status_code == 422


def test_generar_con_integracion(client, db, crear_docente):
    _, headers = crear_docente()
    payload = _payload(sesiones=3)
    payload["contexto"]["integracion"] = {"modo": "elegida", "contenidos": [VINCULO_DATOS]}
    pid = client.post("/planeaciones", headers=headers, json=payload).json()["id"]

    r, llm = _generar(client, headers, pid, [_encabezado_bueno(5), _integracion_buena(), _sesiones_buenas(3)])
    assert r.status_code == 200, r.text
    assert llm.call_count == 3
    doc = r.json()["documento"]
    v = doc["integracion"][0]
    assert v["campo"] == "Saberes y Pensamiento Científico"
    assert v["pdas"] == separar_pdas(buscar_contenido(6, VINCULO_DATOS["campo"], VINCULO_DATOS["contenido"])["pda"])
    assert v["sesion"] == "Miércoles"
    assert doc["sesiones"][2]["vinculos"] == ["Saberes y Pensamiento Científico"]
    assert len(doc["rubrica"]) == 5 + 1  # una fila por PDA principal + una por vinculo

    p = db.get(Planeacion, pid)
    db.refresh(p)
    texto = "\n".join(par.text for par in Document(p.docx_path).paragraphs)
    assert "Integración curricular" in texto


def test_sesion_inventada_del_vinculo_se_reasigna(client, crear_docente):
    _, headers = crear_docente()
    payload = _payload(sesiones=3)
    payload["contexto"]["integracion"] = {"modo": "elegida", "contenidos": [VINCULO_DATOS]}
    pid = client.post("/planeaciones", headers=headers, json=payload).json()["id"]
    r, _ = _generar(client, headers, pid, [_encabezado_bueno(5), _integracion_buena("Sábado"), _sesiones_buenas(3)])
    assert r.json()["documento"]["integracion"][0]["sesion"] in {"Lunes", "Martes", "Miércoles"}


def test_modo_sugerida_sin_eleccion_la_calcula_el_codigo(client, crear_docente):
    _, headers = crear_docente()
    payload = _payload(sesiones=2)
    payload["contexto"]["integracion"] = {"modo": "sugerida", "contenidos": []}
    pid = client.post("/planeaciones", headers=headers, json=payload).json()["id"]
    with _sin_embeddings():
        n = len(integracion.sugerir_vinculos(6, "Lenguajes", CONTENIDO_CARTAS + " " + _pda_oficial() + " Escribir cartas a las familias"))
    base_v = json.loads(_integracion_buena("Lunes"))["vinculos"][0]
    productos = ["Gráfica de barras del consumo", "Tríptico informativo para familias", "Acuerdo escrito del grupo"]
    respuesta_c = json.dumps({"vinculos": [{**base_v, "evidencia": productos[i]} for i in range(n)]})
    with _sin_embeddings():
        # Las sesiones de prueba no mencionan el tríptico ni el acuerdo: el
        # codigo pide una reparacion (cuarta respuesta).
        r, _ = _generar(client, headers, pid, [_encabezado_bueno(5), respuesta_c, _sesiones_buenas(2), _sesiones_buenas(2)])
    doc = r.json()["documento"]
    assert len(doc["integracion"]) == n >= 1
    assert all(v["campo"] != "Lenguajes" for v in doc["integracion"])


# -------------------------------------------- fechas, citas, recursos y ejes

def test_fecha_de_inicio_calcula_dias_reales(client, crear_docente):
    _, headers = crear_docente()
    payload = _payload(sesiones=3)
    payload["contexto"]["fecha_inicio"] = "2026-10-13"  # martes
    pid = client.post("/planeaciones", headers=headers, json=payload).json()["id"]
    doc = _generar(client, headers, pid, [_encabezado_bueno(5), _sesiones_buenas(3)])[0].json()["documento"]
    assert [s["etiqueta"] for s in doc["sesiones"]] == ["Martes 13 oct", "Miércoles 14 oct", "Jueves 15 oct"]
    assert doc["periodo"] == "Del 13 al 15 de octubre de 2026"


def test_ids_de_fuente_y_recursos_en_mayusculas_se_limpian(client, crear_docente):
    _, headers = crear_docente()
    pid = client.post("/planeaciones", headers=headers, json=_payload(sesiones=1)).json()["id"]
    sesion = _sesion(0)
    sesion["desarrollo"] = "Leeremos juntos el fragmento del libro [F1] (pág. 37) y haremos un árbol de problemas en cartulina."
    sesion["recursos"] = ["LIBROS", "CARTULINAS", "cartulinas"]
    sesion["citas"] = []
    fuentes = [{"id": "F1", "libro": "Proyectos Escolares - 6° grado", "paginas": "36-38", "seccion": "x"}]
    with patch.object(generador, "chat_completion_json", side_effect=[_encabezado_bueno(5), json.dumps({"sesiones": [sesion]})]), \
         patch.object(generador, "buscar_fuentes", return_value=(fuentes, "[F1] ...")):
        doc = client.post(f"/planeaciones/{pid}/generar", headers=headers).json()["documento"]
    s = doc["sesiones"][0]
    assert "[F1]" not in s["desarrollo"]["texto"]
    assert "Proyectos Escolares 6°" in s["desarrollo"]["texto"]
    assert s["citas"] == ["F1"]
    assert s["recursos"] == ["Libros", "Cartulinas"]


def test_si_no_eligio_ejes_se_proponen_solo_oficiales(client, crear_docente):
    _, headers = crear_docente()
    payload = _payload(sesiones=1)
    payload["contexto"]["ejes_articuladores"] = []
    pid = client.post("/planeaciones", headers=headers, json=payload).json()["id"]
    enc = json.loads(_encabezado_bueno(5))
    enc["ejes"] = ["Pensamiento crítico", "Eje inventado", "vida saludable"]
    doc = _generar(client, headers, pid, [json.dumps(enc), _sesiones_buenas(1)])[0].json()["documento"]
    assert doc["ejes"] == ["Pensamiento crítico", "Vida saludable"]


# ------------------------------------------------------------ texto de relleno

def test_texto_de_relleno_se_detecta_y_se_repara(client, crear_docente):
    _, headers = crear_docente()
    pid = client.post("/planeaciones", headers=headers, json=_payload(sesiones=2)).json()["id"]
    basura = _sesion(1)
    basura["inicio"] = ".."
    basura["desarrollo"] = "Usaré el pizarrón para anotar las…"
    basura["tarea"] = "..…………??..}{"
    mala = json.dumps({"sesiones": [_sesion(0), basura]})
    r, llm = _generar(client, headers, pid, [_encabezado_bueno(5), mala, _sesiones_buenas(2)])
    assert llm.call_count == 3  # el relleno dispara la reparacion
    doc = r.json()["documento"]
    assert doc["sesiones"][1]["inicio"]["texto"].startswith("Actividad 1")
    assert doc["pendientes"] == []


def test_relleno_que_sigue_tras_reparar_queda_como_pendiente(client, crear_docente):
    _, headers = crear_docente()
    pid = client.post("/planeaciones", headers=headers, json=_payload(sesiones=2)).json()["id"]
    basura = _sesion(1)
    basura["inicio"] = ".."
    basura["tarea"] = "..…………??..}{"
    mala = json.dumps({"sesiones": [_sesion(0), basura]})
    doc = _generar(client, headers, pid, [_encabezado_bueno(5), mala, mala])[0].json()["documento"]
    s = doc["sesiones"][1]
    assert s["inicio"]["texto"].startswith("[Completar")
    assert s["tarea"] == ""


def test_sesion_que_copia_el_inicio_de_otra_se_detecta():
    from app.services.planeacion_nem.generador import normalizar_sesiones

    plan = [{"etiqueta": e, "fases": ["F"], "minutos": (10, 30, 10)} for e in ("Lunes", "Martes")]
    copia = _sesion(1)
    copia["inicio"] = _sesion(0)["inicio"]
    _, problemas = normalizar_sesiones({"sesiones": [_sesion(0), copia]}, plan, set(), set())
    assert any("repite el inicio" in p for p in problemas)


# ------------------------------------------------- vinculos y ajustes razonables

def test_vinculo_solo_trae_procesos_relevantes():
    from app.services.planeacion_nem.integracion import procesos_relevantes

    oficial = buscar_contenido(6, "Saberes y Pensamiento Científico",
                               "Pérdida de biodiversidad, problemas medioambientales en la comunidad, México y el mundo, acciones orientadas a fortalecer estilos de vida sustentables.")
    pdas = separar_pdas(oficial["pda"])
    elegidos = procesos_relevantes(pdas, "En la escuela se desperdicia mucha agua; propondremos acciones de consumo responsable con las familias")
    assert 1 <= len(elegidos) <= 2
    assert not any("invernadero" in p for p in elegidos)


def test_ajustes_razonables_deben_aplicarse_en_alguna_sesion(client, crear_docente):
    _, headers = crear_docente()
    payload = _payload(sesiones=2)
    payload["contexto"]["ajustes_razonables"] = "Un alumno necesita instrucciones por pasos."
    pid = client.post("/planeaciones", headers=headers, json=payload).json()["id"]
    con_ajuste = json.loads(_sesiones_buenas(2))
    con_ajuste["sesiones"][0]["desarrollo"] += " Ajuste razonable: le daré los pasos en tarjetas con imágenes."
    r, llm = _generar(client, headers, pid, [_encabezado_bueno(5), _sesiones_buenas(2), json.dumps(con_ajuste)])
    assert llm.call_count == 3  # sin ajuste aplicado se pide reparacion
    assert "Ajuste razonable" in r.json()["documento"]["sesiones"][0]["desarrollo"]["texto"]


def test_vinculo_ya_descrito_en_la_sesion_no_se_repite(client, crear_docente):
    _, headers = crear_docente()
    payload = _payload(sesiones=3)
    payload["contexto"]["integracion"] = {"modo": "elegida", "contenidos": [VINCULO_DATOS]}
    pid = client.post("/planeaciones", headers=headers, json=payload).json()["id"]
    sesiones = json.loads(_sesiones_buenas(3))
    actividad = json.loads(_integracion_buena())["vinculos"][0]["actividad"]
    sesiones["sesiones"][2]["inicio"] = actividad
    doc = _generar(client, headers, pid, [_encabezado_bueno(5), _integracion_buena(), json.dumps(sesiones)])[0].json()["documento"]
    assert doc["integracion"][0]["en_sesion"] is True


def test_rubrica_larga_se_recorta_y_no_truena(client, crear_docente):
    _, headers = crear_docente()
    payload = _payload(sesiones=3)
    payload["contexto"]["integracion"] = {"modo": "elegida", "contenidos": [VINCULO_DATOS]}
    pid = client.post("/planeaciones", headers=headers, json=payload).json()["id"]
    integ = json.loads(_integracion_buena())
    integ["vinculos"][0]["rubrica"]["indicador"] = "Reconoce la función e importancia de las normas " * 20
    r, _ = _generar(client, headers, pid, [_encabezado_bueno(5), json.dumps(integ), _sesiones_buenas(3)])
    assert r.status_code == 200, r.text
    assert len(r.json()["documento"]["rubrica"][-1]["indicador"]) <= 300


def test_ajustes_no_aplicados_tras_reparar_quedan_pendientes(client, crear_docente):
    _, headers = crear_docente()
    payload = _payload(sesiones=3)
    payload["contexto"]["ajustes_razonables"] = "Un alumno necesita instrucciones por pasos."
    pid = client.post("/planeaciones", headers=headers, json=payload).json()["id"]
    doc = _generar(client, headers, pid, [_encabezado_bueno(5), _sesiones_buenas(3), _sesiones_buenas(3)])[0].json()["documento"]
    assert "Ajustes razonables en las sesiones" in doc["pendientes"]
    assert "Ajuste razonable: [Completar" in doc["sesiones"][1]["desarrollo"]["texto"]


def test_vinculo_parafraseado_en_la_sesion_tampoco_se_repite(client, crear_docente):
    _, headers = crear_docente()
    payload = _payload(sesiones=3)
    payload["contexto"]["integracion"] = {"modo": "elegida", "contenidos": [VINCULO_DATOS]}
    pid = client.post("/planeaciones", headers=headers, json=payload).json()["id"]
    sesiones = json.loads(_sesiones_buenas(3))
    sesiones["sesiones"][2]["desarrollo"] = (
        "Durante dos días contaremos la basura del recreo según su tipo y con esos datos haremos una gráfica de barras para el diagnóstico."
    )
    doc = _generar(client, headers, pid, [_encabezado_bueno(5), _integracion_buena(), json.dumps(sesiones)])[0].json()["documento"]
    assert doc["integracion"][0]["en_sesion"] is True


def test_proposito_en_singular_pide_reparacion(client, crear_docente):
    _, headers = crear_docente()
    pid = client.post("/planeaciones", headers=headers, json=_payload(sesiones=1)).json()["id"]
    malo = json.loads(_encabezado_bueno(5))
    malo["proposito"] = malo["proposito"].replace("escribiremos", "propondré")
    r, llm = _generar(client, headers, pid, [json.dumps(malo), _encabezado_bueno(5), _sesiones_buenas(1)])
    assert llm.call_count == 3
    assert "propondré" not in r.json()["documento"]["proposito"]


def test_ajuste_aplicado_con_otras_palabras_cuenta():
    from app.services.planeacion_nem.generador import ajustes_aplicados

    sesion = {m: {"minutos": 10, "texto": "x"} for m in ("inicio", "desarrollo", "cierre")}
    sesion["desarrollo"]["texto"] = "Guiaré a la alumna con lentes, que se sienta al frente, y le daré los pasos con apoyo visual."
    assert ajustes_aplicados([sesion], "Un alumno necesita instrucciones por pasos con apoyo visual y otra alumna usa lentes.")
    sesion["desarrollo"]["texto"] = "Organizaremos comunidades de cuatro para medir el agua."
    assert not ajustes_aplicados([sesion], "Un alumno necesita instrucciones por pasos con apoyo visual.")


def test_docente_en_tercera_persona_pide_reparacion():
    from app.services.planeacion_nem.generador import normalizar_sesiones

    plan = [{"etiqueta": "Lunes", "fases": ["F"], "minutos": (10, 30, 10)}]
    s = _sesion(0)
    s["cierre"] = "El profesor recopilará los reglamentos y preparará una propuesta conjunta para el grupo."
    _, problemas = normalizar_sesiones({"sesiones": [s]}, plan, set(), set())
    assert any("tercera persona" in p for p in problemas)


def test_evaluacion_incluye_los_vinculos(client, crear_docente):
    _, headers = crear_docente()
    payload = _payload(sesiones=3)
    payload["contexto"]["integracion"] = {"modo": "elegida", "contenidos": [VINCULO_DATOS]}
    pid = client.post("/planeaciones", headers=headers, json=payload).json()["id"]
    doc = _generar(client, headers, pid, [_encabezado_bueno(5), _integracion_buena(), _sesiones_buenas(3)])[0].json()["documento"]
    assert "Organiza datos en una gráfica" in doc["evaluacion"]["criterios"]
    assert "Gráfica de barras" in doc["evaluacion"]["evidencias"]


def test_vinculos_con_el_mismo_producto_piden_reparacion():
    from app.services.planeacion_nem.generador import normalizar_integracion

    vinculos = [
        {"campo": "Saberes y Pensamiento Científico", "contenido": "x", "pdas": ["p"]},
        {"campo": "Lenguajes", "contenido": "y", "pdas": ["q"]},
    ]
    def v(evidencia, indicador):
        return {"sesion": "Lunes", "actividad": "Les pediré que trabajen en comunidades con los datos del agua de la escuela.",
                "evidencia": evidencia, "rubrica": {"indicador": indicador, "sobresaliente": "Lo logra con autonomía",
                                                    "satisfactorio": "Lo logra con apoyo", "en_proceso": "Aún no lo logra"}}
    datos = {"vinculos": [v("Reglamento escolar del agua", "Cumplimiento del PDA de Saberes"),
                          v("Reglamento escolar sobre el agua", "Escribe reglas claras")]}
    _, _, problemas = normalizar_integracion(datos, vinculos, ["Lunes"])
    assert any("producen lo mismo" in p for p in problemas)
    assert any("cumplimiento del PDA" in p for p in problemas)


def test_evaluacion_en_word_sin_puntos_dobles():
    from app.services.planeacion_nem.documento_docx import _lista

    assert _lista(["Reglamento escrito por los estudiantes.", "Acta del debate.."]) == "Reglamento escrito por los estudiantes; Acta del debate"


def test_instancias_inventadas_se_detectan():
    from app.services.planeacion_nem.generador import instancias_inventadas

    texto = "Consolidaremos un reglamento final que se presentará a la dirección escolar y al comité de convivencia."
    assert set(instancias_inventadas(texto, "En la escuela se desperdicia agua")) == {"la dirección escolar", "un comité"}
    # Si la docente lo menciono, si se permite.
    assert instancias_inventadas(texto, "Presentaremos el reglamento a la dirección y al comité escolar") == []


def test_instancia_inventada_pide_reparacion_y_si_sigue_queda_pendiente(client, crear_docente):
    _, headers = crear_docente()
    pid = client.post("/planeaciones", headers=headers, json=_payload(sesiones=1)).json()["id"]
    mala = json.loads(_sesiones_buenas(1))
    mala["sesiones"][0]["cierre"] = "Consolidaremos un reglamento final que se presentará a la dirección escolar al final de la semana."
    r, llm = _generar(client, headers, pid, [_encabezado_bueno(5), json.dumps(mala), json.dumps(mala)])
    assert llm.call_count == 3
    assert any("dirección escolar" in p for p in r.json()["documento"]["pendientes"])


def test_vinculo_que_no_aparece_en_su_sesion_pide_reparacion():
    from app.services.planeacion_nem.generador import normalizar_sesiones

    plan = [{"etiqueta": e, "fases": ["F"], "minutos": (10, 30, 10)} for e in ("Lunes", "Martes")]
    vinculo = {"campo": "Lenguajes", "sesion": "Martes",
               "actividad": "Les pediré que redacten un reglamento escolar sobre el uso responsable del agua con cinco reglas."}
    _, problemas = normalizar_sesiones({"sesiones": [_sesion(0), _sesion(1)]}, plan, set(), set(), vinculos=[vinculo])
    assert any("no incluye el vínculo con Lenguajes" in p for p in problemas)

    con = _sesion(1)
    con["desarrollo"] = "En comunidades redactarán un reglamento escolar sobre el uso responsable del agua, con cinco reglas claras."
    _, problemas = normalizar_sesiones({"sesiones": [_sesion(0), con]}, plan, set(), set(), vinculos=[vinculo])
    assert not any("no incluye el vínculo" in p for p in problemas)


def test_indicador_largo_se_queda_con_la_primera_oracion():
    from app.services.planeacion_nem.generador import _indicador_corto

    pda = ("Reconoce la función e importancia de cumplir las pautas o normas establecidas en reglamentos para regular "
           "la convivencia. Escribe las reglas para lograr una convivencia respetuosa, incluyente, equitativa e "
           "igualitaria en la escuela, tomando en consideración sus experiencias a lo largo de la primaria.")
    assert _indicador_corto(pda) == ("Reconoce la función e importancia de cumplir las pautas o normas establecidas "
                                     "en reglamentos para regular la convivencia.")



def test_vinculo_con_palabras_genericas_no_cuenta_como_incluido():
    from app.services.planeacion_nem.generador import normalizar_sesiones

    plan = [{"etiqueta": e, "fases": ["F"], "minutos": (10, 30, 10)} for e in ("Lunes", "Martes", "Miércoles")]
    sesiones = [_sesion(i) for i in range(3)]
    for s in sesiones:
        s["desarrollo"] = "Organizaremos la experiencia: las comunidades medirán el agua del bebedero y anotarán los datos."
        s["inicio"] = f"Paso {s['inicio']}"
    vinculo = {"campo": "Lenguajes", "sesion": "Miércoles", "evidencia": "Documento con las reglas",
               "actividad": "Organizaremos un taller donde los estudiantes redactarán las reglas del reglamento escolar del agua."}
    _, problemas = normalizar_sesiones({"sesiones": sesiones}, plan, set(), set(), vinculos=[vinculo])
    assert any("no incluye el vínculo con Lenguajes" in p for p in problemas)


def test_docente_que_redacta_el_producto_pide_reparacion():
    from app.services.planeacion_nem.generador import normalizar_sesiones

    plan = [{"etiqueta": "Lunes", "fases": ["F"], "minutos": (10, 30, 10)}]
    s = _sesion(0)
    s["desarrollo"] += " Además, redactaré las reglas del reglamento escolar basándome en sus experiencias."
    _, problemas = normalizar_sesiones({"sesiones": [s]}, plan, set(), set())
    assert any("pone a la docente a hacer el producto" in p for p in problemas)


def test_metricas_de_la_generacion(client, crear_docente):
    _, headers = crear_docente()
    pid = client.post("/planeaciones", headers=headers, json=_payload(sesiones=2)).json()["id"]
    basura = _sesion(1)
    basura["inicio"] = ".."
    mala = json.dumps({"sesiones": [_sesion(0), basura]})
    doc = _generar(client, headers, pid, [_encabezado_bueno(5), mala, _sesiones_buenas(2)])[0].json()["documento"]
    m = doc["metricas"]
    assert m["llamadas"] == 3 and m["reparaciones"] == 1
    assert m["problemas_detectados"] and not m["problemas_finales"]


def test_vinculos_se_emparejan_por_campo_aunque_vengan_en_otro_orden():
    from app.services.planeacion_nem.generador import normalizar_integracion

    vinculos = [
        {"campo": "Saberes y Pensamiento Científico", "contenido": "Organización e interpretación de datos.",
         "pdas": ["Organiza datos en tablas y gráficas de barras."]},
        {"campo": "Lenguajes", "contenido": "Comparación y producción de documentos que regulan la convivencia.",
         "pdas": ["Escribe las reglas de un reglamento escolar."]},
    ]
    fila = {"indicador": "Indicador específico", "sobresaliente": "Lo logra con autonomía",
            "satisfactorio": "Lo logra con apoyo", "en_proceso": "Aún no lo logra"}
    datos = {"vinculos": [  # al reves
        {"campo": "Lenguajes", "sesion": "Jueves", "actividad": "Les pediré que escriban las reglas del reglamento escolar sobre el agua.",
         "evidencia": "Reglamento escolar", "rubrica": fila},
        {"campo": "Saberes y Pensamiento Científico", "sesion": "Martes",
         "actividad": "Les pediré que organicen los datos de consumo en tablas y una gráfica de barras.",
         "evidencia": "Gráfica de barras", "rubrica": fila},
    ]}
    salida, _, problemas = normalizar_integracion(datos, vinculos, ["Martes", "Jueves"])
    assert salida[0]["sesion"] == "Martes" and "gráfica" in salida[0]["actividad"]
    assert salida[1]["sesion"] == "Jueves" and "reglamento" in salida[1]["actividad"]
    assert not any("corresponde al PDA" in p for p in problemas)


def test_vinculos_cruzados_sin_campo_se_detectan():
    from app.services.planeacion_nem.generador import normalizar_integracion

    vinculos = [
        {"campo": "Saberes y Pensamiento Científico", "contenido": "Organización e interpretación de datos.",
         "pdas": ["Organiza datos en tablas y gráficas de barras."]},
        {"campo": "Lenguajes", "contenido": "Comparación y producción de documentos que regulan la convivencia.",
         "pdas": ["Escribe las reglas de un reglamento escolar."]},
    ]
    fila = {"indicador": "Indicador específico", "sobresaliente": "Lo logra con autonomía",
            "satisfactorio": "Lo logra con apoyo", "en_proceso": "Aún no lo logra"}
    datos = {"vinculos": [
        {"sesion": "Martes", "actividad": "Les pediré que escriban las reglas del reglamento escolar sobre el agua.",
         "evidencia": "Reglamento escolar con reglas", "rubrica": fila},
        {"sesion": "Jueves", "actividad": "Les pediré que organicen los datos de consumo en tablas y una gráfica de barras.",
         "evidencia": "Gráfica de barras con datos", "rubrica": fila},
    ]}
    _, _, problemas = normalizar_integracion(datos, vinculos, ["Martes", "Jueves"])
    assert any("corresponde al PDA de Lenguajes" in p for p in problemas)
