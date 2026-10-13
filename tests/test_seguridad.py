"""
Pruebas de aislamiento entre usuarios ("seguridad entre paginas"),
validacion de archivos, concurrencia y numero de consultas.

Idea central: el docente A nunca debe poder ver, descargar, comentar ni
evaluar algo privado del docente B, aunque conozca su id o su URL.
"""

import io
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from sqlalchemy import event

from app.core.firmas import url_firmada
from app.db.session import engine
from app.models.archivo import RecursoGenerado
from app.models.comunidad import Like, Publicacion
from app.models.planeacion import Planeacion
from app.models.retroalimentacion import Retroalimentacion

PNG_MINIMO = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


def _docx_minimo() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("word/document.xml", "<document/>")
    return buffer.getvalue()


def _subir(client, headers, nombre: str, contenido: bytes):
    return client.post("/archivos", headers=headers, files={"file": (nombre, contenido)})


# ---------------------------------------------------------------- archivos

def test_archivo_privado_solo_lo_ve_su_dueno(client, crear_docente):
    _, headers_a = crear_docente()
    _, headers_b = crear_docente()

    r = _subir(client, headers_a, "foto.png", PNG_MINIMO)
    assert r.status_code == 200, r.text
    url = r.json()["url"]

    assert client.get(url, headers=headers_a).status_code == 200
    assert client.get(url, headers=headers_b).status_code == 404
    assert client.get(url).status_code == 404  # sin sesion


def test_url_firmada_funciona_sin_sesion_y_no_se_puede_alterar(client, crear_docente):
    _, headers_a = crear_docente()
    r = _subir(client, headers_a, "foto.png", PNG_MINIMO)
    firmada = r.json()["url_firmada"]

    assert client.get(firmada).status_code == 200

    alterada = firmada[:-1] + ("0" if firmada[-1] != "0" else "1")
    assert client.get(alterada).status_code == 404

    nombre = r.json()["url"].rsplit("/", 1)[1]
    vencida = url_firmada(nombre, ttl_segundos=-10)
    assert client.get(vencida).status_code == 404


def test_extension_falsa_se_rechaza(client, crear_docente):
    _, headers = crear_docente()
    assert _subir(client, headers, "tarea.pdf", PNG_MINIMO).status_code == 400
    assert _subir(client, headers, "script.docx", b"print('hola')").status_code == 400
    assert _subir(client, headers, "script.py", b"print('hola')").status_code == 400
    assert _subir(client, headers, "ficha.docx", _docx_minimo()).status_code == 200


def test_nombres_fuera_del_formato_no_tocan_el_disco(client, crear_docente):
    _, headers = crear_docente()
    for nombre in ("..%2Fpruebas.db", "pruebas.db", "abc.png", f"{uuid.uuid4()}.py"):
        assert client.get(f"/archivos/{nombre}", headers=headers).status_code == 404


def test_url_legacy_uploads_tambien_pide_sesion(client, crear_docente):
    _, headers = crear_docente()
    nombre = f"{uuid.uuid4()}.png"
    Path("uploads", nombre).write_bytes(PNG_MINIMO)  # archivo viejo sin registro

    assert client.get(f"/uploads/{nombre}").status_code == 404
    assert client.get(f"/uploads/{nombre}", headers=headers).status_code == 200


# ---------------------------------------------------------------- recursos

def test_recurso_de_otro_docente_no_se_descarga_ni_se_ajusta(client, db, crear_docente):
    a, headers_a = crear_docente()
    _, headers_b = crear_docente()

    recurso_id = str(uuid.uuid4())
    Path("generated_docs").mkdir(exist_ok=True)
    Path("generated_docs", f"cuestionario_{recurso_id}.docx").write_bytes(_docx_minimo())
    db.add(RecursoGenerado(id=recurso_id, owner_id=a.id, tipo="cuestionario"))
    db.commit()

    url = f"/recursos/cuestionarios/{recurso_id}/docx"
    assert client.get(url, headers=headers_a).status_code == 200
    assert client.get(url, headers=headers_b).status_code == 404

    ajuste = {
        "titulo": "x", "tipo_preguntas": "abierta", "num_preguntas": 3,
        "preguntas_actuales": [], "instrucciones": "cambia todo",
    }
    r = client.post(f"/recursos/cuestionarios/{recurso_id}/ajustar", headers=headers_b, json=ajuste)
    assert r.status_code == 404  # se rechaza antes de llamar al LLM


def test_planeacion_ajena_es_404(client, db, crear_docente):
    a, _ = crear_docente()
    _, headers_b = crear_docente()
    p = Planeacion(
        owner_id=a.id, grado=4, campo_formativo="Lenguajes", contenido="c",
        pda="p", grupo="A", sesiones=2, tema="t",
    )
    db.add(p)
    db.commit()
    assert client.get(f"/planeaciones/{p.id}", headers=headers_b).status_code == 404


# ---------------------------------------------------------------- comunidad

def test_publicacion_de_otra_escuela_no_se_puede_tocar(client, crear_docente):
    _, headers_a = crear_docente(escuela="Escuela Benito Juarez")
    _, headers_b = crear_docente(escuela="Escuela Benito Juarez")
    _, headers_c = crear_docente(escuela="Otra escuela")

    r = client.post(
        "/comunidad/publicaciones", headers=headers_a,
        json={"contenido": "Solo para mi escuela", "visibilidad": "escuela"},
    )
    pub_id = r.json()["id"]

    base = f"/comunidad/publicaciones/{pub_id}"
    assert client.get(f"{base}/comentarios", headers=headers_b).status_code == 200
    assert client.get(f"{base}/comentarios", headers=headers_c).status_code == 404
    assert client.post(f"{base}/like", headers=headers_c).status_code == 404
    assert client.post(f"{base}/comentarios", headers=headers_c, json={"contenido": "hola"}).status_code == 404

    ids_c = [p["id"] for p in client.get("/comunidad/publicaciones", headers=headers_c).json()]
    assert pub_id not in ids_c


def test_no_se_puede_publicar_la_imagen_privada_de_otro(client, crear_docente):
    _, headers_a = crear_docente()
    _, headers_b = crear_docente()
    url_privada = _subir(client, headers_a, "foto.png", PNG_MINIMO).json()["url"]

    r = client.post(
        "/comunidad/publicaciones", headers=headers_b,
        json={"contenido": "mira", "visibilidad": "publico", "imagenes": [url_privada]},
    )
    assert r.status_code == 400
    assert client.get(url_privada, headers=headers_b).status_code == 404


def test_imagen_publicada_la_ve_la_comunidad_con_url_firmada(client, crear_docente):
    _, headers_a = crear_docente()
    _, headers_b = crear_docente()
    url = _subir(client, headers_a, "foto.png", PNG_MINIMO).json()["url"]

    r = client.post(
        "/comunidad/publicaciones", headers=headers_a,
        json={"contenido": "nuestro huerto", "visibilidad": "publico", "imagenes": [url]},
    )
    assert r.status_code == 200, r.text
    imagen_firmada = r.json()["imagenes"][0]
    assert "firma=" in imagen_firmada
    assert client.get(imagen_firmada).status_code == 200
    assert client.get(url, headers=headers_b).status_code == 200


def test_likes_simultaneos_no_rompen_ni_duplican(client, db, crear_docente):
    _, headers = crear_docente()
    pub_id = client.post(
        "/comunidad/publicaciones", headers=headers,
        json={"contenido": "doble clic", "visibilidad": "publico"},
    ).json()["id"]

    def dar_like(_):
        return client.post(f"/comunidad/publicaciones/{pub_id}/like", headers=headers).status_code

    with ThreadPoolExecutor(max_workers=8) as pool:
        codigos = list(pool.map(dar_like, range(8)))

    assert all(c == 200 for c in codigos), codigos
    total = db.query(Like).filter(Like.publicacion_id == pub_id).count()
    assert total in (0, 1)


def test_feed_usa_numero_constante_de_consultas(client, db, crear_docente):
    """
    Evidencia empirica de la complejidad: el numero de consultas SQL del
    feed no depende de cuantas publicaciones se muestran.
    """
    autor, headers = crear_docente()
    db.query(Publicacion).delete()
    db.commit()

    def contar_consultas_del_feed() -> int:
        contador = {"n": 0}

        def _al_ejecutar(*_args, **_kwargs):
            contador["n"] += 1

        event.listen(engine, "before_cursor_execute", _al_ejecutar)
        try:
            r = client.get("/comunidad/publicaciones?limite=100", headers=headers)
            assert r.status_code == 200
        finally:
            event.remove(engine, "before_cursor_execute", _al_ejecutar)
        return contador["n"]

    for i in range(5):
        db.add(Publicacion(autor_id=autor.id, contenido=f"p{i}", visibilidad="publico"))
    db.commit()
    con_5 = contar_consultas_del_feed()

    for i in range(45):
        db.add(Publicacion(autor_id=autor.id, contenido=f"q{i}", visibilidad="publico"))
    db.commit()
    con_50 = contar_consultas_del_feed()

    assert con_5 == con_50, (con_5, con_50)


def test_busqueda_trata_comodines_como_texto(client, crear_docente):
    _, headers = crear_docente()
    client.post("/comunidad/publicaciones", headers=headers, json={"contenido": "avance del 50% del grupo", "visibilidad": "publico"})
    client.post("/comunidad/publicaciones", headers=headers, json={"contenido": "sin porcentaje", "visibilidad": "publico"})
    r = client.get("/comunidad/publicaciones?busqueda=%25", headers=headers)
    contenidos = [p["contenido"] for p in r.json()]
    assert "avance del 50% del grupo" in contenidos
    assert "sin porcentaje" not in contenidos


# ---------------------------------------------------------- retroalimentacion

def test_retroalimentacion_solo_sobre_lo_propio_y_un_voto(client, db, crear_docente):
    a, headers_a = crear_docente()
    _, headers_b = crear_docente()
    recurso_id = str(uuid.uuid4())
    db.add(RecursoGenerado(id=recurso_id, owner_id=a.id, tipo="diapositivas"))
    db.commit()

    voto = {"objetivo_tipo": "recurso", "objetivo_id": recurso_id, "valor": 1}
    assert client.put("/retroalimentacion", headers=headers_b, json=voto).status_code == 404

    assert client.put("/retroalimentacion", headers=headers_a, json=voto).status_code == 200
    voto["valor"] = -1
    voto["comentario"] = "le faltaron ejemplos"
    assert client.put("/retroalimentacion", headers=headers_a, json=voto).status_code == 200

    filas = db.query(Retroalimentacion).filter(Retroalimentacion.objetivo_id == recurso_id).all()
    assert len(filas) == 1
    assert filas[0].valor == -1
    assert filas[0].comentario == "le faltaron ejemplos"


def test_retroalimentacion_rechaza_valores_fuera_de_rango(client, db, crear_docente):
    a, headers_a = crear_docente()
    recurso_id = str(uuid.uuid4())
    db.add(RecursoGenerado(id=recurso_id, owner_id=a.id, tipo="diapositivas"))
    db.commit()
    voto = {"objetivo_tipo": "recurso", "objetivo_id": recurso_id, "valor": 5}
    assert client.put("/retroalimentacion", headers=headers_a, json=voto).status_code == 422


def test_no_se_puede_adjuntar_el_archivo_de_otro(client, crear_docente):
    _, headers_a = crear_docente()
    _, headers_b = crear_docente()
    url_de_a = _subir(client, headers_a, "foto.png", PNG_MINIMO).json()["url"]

    conv_id = client.post("/conversaciones", headers=headers_b).json()["id"]
    r = client.post(
        f"/conversaciones/{conv_id}/mensajes", headers=headers_b,
        json={"content": "revisa esto", "adjuntos": [{"url": url_de_a, "filename": "x.png", "tipo": "imagen"}]},
    )
    assert r.status_code == 400  # se rechaza antes de llamar al LLM
