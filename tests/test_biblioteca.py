"""Libros SEP y documentos de docentes: aislamiento entre usuarios."""

from unittest.mock import patch

PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"


def _subir(client, headers, nombre="ficha.pdf"):
    r = client.post("/archivos", headers=headers, files={"file": (nombre, PDF, "application/pdf")})
    assert r.status_code == 200, r.text
    return r.json()["url"]


def _crear(client, headers, url, **extra):
    datos = {"url": url, "titulo": "Examen de fracciones", "campo": "Saberes y Pensamiento Científico", "grado": 6}
    datos.update(extra)
    return client.post("/documentos", headers=headers, json=datos)


def test_no_se_puede_registrar_un_archivo_ajeno(client, crear_docente):
    _, a = crear_docente()
    _, b = crear_docente()
    url = _subir(client, a)
    assert _crear(client, b, url).status_code == 400


def test_compartir_exige_aceptar_condiciones(client, crear_docente):
    _, a = crear_docente()
    url = _subir(client, a)
    assert _crear(client, a, url, visibilidad="comunidad").status_code == 422
    r = _crear(client, a, url, visibilidad="comunidad", acepto_condiciones=True)
    assert r.status_code == 201
    assert "firma=" in r.json()["url_firmada"]


def test_mismo_archivo_dos_veces_es_409(client, crear_docente):
    _, a = crear_docente()
    url = _subir(client, a)
    assert _crear(client, a, url).status_code == 201
    assert _crear(client, a, url).status_code == 409


def test_comunidad_solo_muestra_lo_compartido_por_otros(client, crear_docente):
    _, a = crear_docente()
    _, b = crear_docente()
    privado = _crear(client, a, _subir(client, a), titulo="Privado de A").json()
    compartido = _crear(client, a, _subir(client, a), titulo="Compartido de A",
                        visibilidad="comunidad", acepto_condiciones=True).json()
    _crear(client, b, _subir(client, b), titulo="Propio de B", visibilidad="comunidad", acepto_condiciones=True)

    ids = {d["id"] for d in client.get("/documentos/comunidad", headers=b).json()}
    assert compartido["id"] in ids
    assert privado["id"] not in ids
    assert all(not d["es_mio"] for d in client.get("/documentos/comunidad", headers=b).json())

    # "%" se busca literal, no como comodin.
    assert client.get("/documentos/comunidad", headers=b, params={"q": "%"}).json() == []


def test_editar_o_borrar_documento_ajeno_es_404(client, crear_docente):
    _, a = crear_docente()
    _, b = crear_docente()
    doc = _crear(client, a, _subir(client, a)).json()
    assert client.patch(f"/documentos/{doc['id']}", headers=b, json={"titulo": "Mio"}).status_code == 404
    assert client.delete(f"/documentos/{doc['id']}", headers=b).status_code == 404
    assert client.delete(f"/documentos/{doc['id']}", headers=a).status_code == 204


def test_campo_inventado_es_422(client, crear_docente):
    _, a = crear_docente()
    assert _crear(client, a, _subir(client, a), campo="Astrología").status_code == 422


def test_buscar_en_libros_regresa_pagina_y_fragmento(client, crear_docente):
    _, a = crear_docente()
    falso = [{"libro": "Nuestros saberes - 6° grado", "materia": "Nuestros saberes", "grado": "6",
              "page_start": 118, "page_end": 119, "section_title": "El aire", "text": "El aire " * 200}]
    with patch("app.routers.biblioteca._buscar_en_indice", return_value=falso) as busqueda:
        r = client.get("/libros/buscar", headers=a, params={"q": "componentes del aire", "grado": 6})
    assert r.status_code == 200
    res = r.json()[0]
    assert res["paginas"] == "118-119" and res["grado"] == 6
    assert len(res["fragmento"]) <= 603
    assert busqueda.call_args.kwargs["grado"] == "6"


def test_libros_requiere_sesion(client):
    assert client.get("/libros/buscar", params={"q": "aire"}).status_code == 401
    assert client.get("/documentos/comunidad").status_code == 401
