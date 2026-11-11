"""
Exporta el documento estructurado a Word con el formato de planeacion que
usan las escuelas: carta horizontal, encabezado por campo formativo, tabla
Etapa | Sesion | Actividades | Recursos | Tarea, evaluacion, firmas y
anexos (rubrica y fuentes consultadas).

Lo que la docente debe completar ("[Completar: ...]", "[Confirmar ...]")
sale resaltado en amarillo para que se vea antes de imprimir.

El archivo se escribe primero en un temporal y luego se renombra
(os.replace es atomico): si dos exportaciones coinciden, nadie descarga un
Word a medio escribir. Tiempo O(tamano del documento).
"""

import os
import re
import tempfile

from docx import Document
from docx.enum.section import WD_ORIENT
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_COLOR_INDEX
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor

OUTPUT_DIR = "generated_docs"
FUENTE = "Arial"
GRIS = "E8ECF4"
TINTA = RGBColor(0x1B, 0x2A, 0x6B)
COLOR_CAMPO = {
    "lenguajes": RGBColor(0xC2, 0x47, 0x1B),
    "saberes y pensamiento cientifico": RGBColor(0x1D, 0x5B, 0xB8),
    "etica, naturaleza y sociedades": RGBColor(0x1E, 0x70, 0x48),
    "de lo humano y lo comunitario": RGBColor(0x9E, 0x2A, 0x64),
}
_MARCA = re.compile(r"(\[(?:Completar|Confirmar)[^\]]*\])")


def _sin_acentos(t: str) -> str:
    return t.translate(str.maketrans("áéíóúÁÉÍÓÚ", "aeiouAEIOU")).lower()


def _sombrear(celda, color: str) -> None:
    tc_pr = celda._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), color)
    tc_pr.append(shd)


def _ancho(celda, pulgadas: float) -> None:
    celda.width = Inches(pulgadas)


def _texto(parrafo, texto: str, negrita=False, tam=9.5, color=None, cursiva=False) -> None:
    """Agrega texto; las marcas [Completar...] salen resaltadas."""
    for parte in _MARCA.split(texto or ""):
        if not parte:
            continue
        run = parrafo.add_run(parte)
        run.bold = negrita
        run.italic = cursiva
        run.font.name = FUENTE
        run.font.size = Pt(tam)
        if color is not None:
            run.font.color.rgb = color
        if _MARCA.fullmatch(parte):
            run.font.highlight_color = WD_COLOR_INDEX.YELLOW


def _celda(celda, lineas, negrita=False, fondo=None, color=None, tam=9.5) -> None:
    if isinstance(lineas, str):
        lineas = [lineas]
    celda.text = ""
    for i, linea in enumerate(lineas or [""]):
        p = celda.paragraphs[0] if i == 0 else celda.add_paragraph()
        p.paragraph_format.space_after = Pt(2)
        _texto(p, linea, negrita=negrita, color=color, tam=tam)
    if fondo:
        _sombrear(celda, fondo)


def _celda_etiquetada(celda, etiqueta: str, texto: str) -> None:
    """'Inicio (10 min).' en negritas y el texto despues, en el mismo parrafo."""
    p = celda.add_paragraph() if celda.paragraphs[0].text else celda.paragraphs[0]
    p.paragraph_format.space_after = Pt(3)
    _texto(p, etiqueta + " ", negrita=True)
    _texto(p, texto)


def _tabla(doc, filas: int, columnas: int, anchos: list[float]):
    t = doc.add_table(rows=filas, cols=columnas)
    t.style = "Table Grid"
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    t.autofit = False
    # Word usa el ancho de cada celda; LibreOffice y Google Docs usan la
    # cuadricula de la tabla (gridCol). Se fijan los dos.
    for j, columna in enumerate(t.columns):
        columna.width = Inches(anchos[j])
    for fila in t.rows:
        for j, celda in enumerate(fila.cells):
            _ancho(celda, anchos[j])
    return t


def _titulo(doc, texto: str, tam=12) -> None:
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(8)
    p.paragraph_format.space_after = Pt(4)
    _texto(p, texto, negrita=True, tam=tam, color=TINTA)


def _encabezado(doc, d: dict) -> None:
    anchos = [1.75, 4.4, 1.8, 1.95]
    t = _tabla(doc, 9, 4, anchos)
    color = COLOR_CAMPO.get(_sin_acentos(d["campo"]))

    def etiqueta(celda, texto):
        _celda(celda, texto, negrita=True, fondo=GRIS)

    r = t.rows
    etiqueta(r[0].cells[0], "Campo formativo")
    _celda(r[0].cells[1], d["campo"], negrita=True, color=color)
    _celda(r[0].cells[2], f"Fase: {d['fase']}" if d.get("fase") else "Fase:")
    _celda(r[0].cells[3], d.get("docente") or "Docente:")
    etiqueta(r[1].cells[0], "Metodología")
    _celda(r[1].cells[1], d["metodologia_nombre"])
    _celda(r[1].cells[2], f"Tiempo: {d['tiempo']}")
    _celda(r[1].cells[3], d.get("periodo") or "[Completar: fechas]")
    etiqueta(r[2].cells[0], "Grado")
    _celda(r[2].cells[1], f"{d['grado']}°" + (f" grupo {d['grupo']}" if d.get("grupo") else ""))
    etiqueta(r[2].cells[2], "Escenario")
    _celda(r[2].cells[3], d["escenario"])

    filas = (
        ("Ejes articuladores", ". ".join(d["ejes"]) + "." if d["ejes"] else "[Completar: ejes articuladores]"),
        ("Título del proyecto", d["titulo_proyecto"]),
        ("Contenido", d["contenido"]),
        ("Proceso de desarrollo de aprendizaje (PDA)", d["pdas"]),
        ("Intención didáctica", d["intencion_didactica"]),
        ("Propósito", d["proposito"]),
    )
    for i, (lab, valor) in enumerate(filas, start=3):
        fila = r[i]
        unida = fila.cells[1].merge(fila.cells[3])
        etiqueta(fila.cells[0], lab)
        _celda(unida, valor, negrita=(lab == "Título del proyecto"))


def _integracion(doc, d: dict) -> None:
    """Tabla de integracion curricular: con que otros campos se vincula el
    proyecto, en que sesion y con que evidencia."""
    vinculos = d.get("integracion") or []
    if not vinculos:
        return
    _titulo(doc, "Integración curricular", tam=11)
    anchos = [1.6, 3.3, 3.0, 0.9, 1.1]
    t = _tabla(doc, 1 + len(vinculos), 5, anchos)
    for j, cab in enumerate(("Campo formativo", "Contenido y PDA", "Cómo se vincula", "Sesión", "Evidencia")):
        _celda(t.rows[0].cells[j], cab, negrita=True, fondo=GRIS)
    for i, v in enumerate(vinculos, start=1):
        c = t.rows[i].cells
        _celda(c[0], v["campo"], negrita=True, color=COLOR_CAMPO.get(_sin_acentos(v["campo"])))
        _celda(c[1], [v["contenido"], *v["pdas"]], tam=8.5)
        _celda(c[2], v["actividad"])
        _celda(c[3], v["sesion"])
        _celda(c[4], v.get("evidencia", ""))


def _sesiones(doc, d: dict) -> None:
    anchos = [1.6, 0.9, 4.75, 1.45, 1.2]
    t = _tabla(doc, 1 + len(d["sesiones"]), 5, anchos)
    for j, cab in enumerate(("Etapa (fase de la metodología)", "Sesión", "Actividades", "Recursos o materiales didácticos", "Tarea")):
        _celda(t.rows[0].cells[j], cab, negrita=True, fondo=GRIS)
    _repetir_encabezado(t.rows[0])
    vinculos_por_sesion: dict[str, list[dict]] = {}
    for v in d.get("integracion") or []:
        vinculos_por_sesion.setdefault(v["sesion"], []).append(v)

    for i, s in enumerate(d["sesiones"], start=1):
        c = t.rows[i].cells
        _celda(c[0], s["fases"], negrita=True, fondo="F7F9FC", tam=9)
        _celda(c[1], s["etiqueta"], negrita=True)
        c[2].text = ""
        _celda_etiquetada(c[2], f"Inicio ({s['inicio']['minutos']} min).", s["inicio"]["texto"])
        _celda_etiquetada(c[2], f"Desarrollo ({s['desarrollo']['minutos']} min).", s["desarrollo"]["texto"])
        _celda_etiquetada(c[2], f"Cierre ({s['cierre']['minutos']} min).", s["cierre"]["texto"])
        for v in vinculos_por_sesion.get(s["etiqueta"], []):
            if v.get("en_sesion"):
                continue
            p = c[2].add_paragraph()
            p.paragraph_format.space_after = Pt(3)
            _texto(p, f"Vínculo con {v['campo']}. ", negrita=True, color=COLOR_CAMPO.get(_sin_acentos(v["campo"])))
            _texto(p, v["actividad"])
        _celda(c[3], s["recursos"])
        _celda(c[4], s.get("tarea", ""))


def _repetir_encabezado(fila) -> None:
    tr_pr = fila._tr.get_or_add_trPr()
    el = OxmlElement("w:tblHeader")
    el.set(qn("w:val"), "true")
    tr_pr.append(el)


def _lista(items: list[str]) -> str:
    """Une con "; " sin dejar puntos dobles ("estudiantes.;" o ".."). O(L)."""
    return "; ".join(t.strip().rstrip(".").strip() for t in items if t.strip())


def _evaluacion(doc, d: dict) -> None:
    ev = d["evaluacion"]
    t = _tabla(doc, 1, 2, [1.75, 8.15])
    _celda(t.rows[0].cells[0], "Evaluación", negrita=True, fondo=GRIS)
    lineas = []
    if ev["criterios"]:
        lineas.append("Criterios: " + _lista(ev["criterios"]) + ".")
    if ev["evidencias"]:
        lineas.append("Evidencias: " + _lista(ev["evidencias"]) + ".")
    if ev["instrumentos"]:
        lineas.append("Instrumentos: " + _lista(ev["instrumentos"]) + " (ver anexos).")
    if d.get("ajustes_razonables"):
        lineas.append("Ajustes razonables: " + d["ajustes_razonables"])
    _celda(t.rows[0].cells[1], lineas or ["[Completar: evaluación]"])


def _firmas(doc, d: dict) -> None:
    doc.add_paragraph()
    doc.add_paragraph()
    t = doc.add_table(rows=3, cols=2)
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    textos = (
        ("Docente de grupo", "Director(a)"),
        ("_____________________________", "_____________________________"),
        (d.get("docente") or "Nombre y firma", "Nombre y firma del Director Técnico"),
    )
    for i, (a, b) in enumerate(textos):
        for j, txt in enumerate((a, b)):
            celda = t.rows[i].cells[j]
            celda.text = ""
            p = celda.paragraphs[0]
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            _texto(p, txt)


def _anexos(doc, d: dict) -> None:
    if d["rubrica"]:
        doc.add_page_break()
        _titulo(doc, "Anexo 1. Rúbrica de desempeño")
        t = _tabla(doc, 1 + len(d["rubrica"]), 4, [2.2, 2.57, 2.57, 2.56])
        for j, cab in enumerate(("Indicador de logro", "Sobresaliente", "Satisfactorio", "En proceso")):
            _celda(t.rows[0].cells[j], cab, negrita=True, fondo=GRIS)
        for i, f in enumerate(d["rubrica"], start=1):
            for j, k in enumerate(("indicador", "sobresaliente", "satisfactorio", "en_proceso")):
                _celda(t.rows[i].cells[j], f.get(k, ""), negrita=(j == 0))

    if d["fuentes"]:
        _titulo(doc, "Fuentes consultadas (libros de texto de la SEP)")
        t = _tabla(doc, 1 + len(d["fuentes"]), 3, [4.6, 1.3, 4.0])
        for j, cab in enumerate(("Libro", "Páginas", "Sección")):
            _celda(t.rows[0].cells[j], cab, negrita=True, fondo=GRIS)
        for i, f in enumerate(d["fuentes"], start=1):
            _celda(t.rows[i].cells[0], f["libro"])
            _celda(t.rows[i].cells[1], f["paginas"])
            _celda(t.rows[i].cells[2], f["seccion"])

    if d.get("pendientes"):
        _titulo(doc, "Pendientes para la docente")
        for p in d["pendientes"]:
            par = doc.add_paragraph()
            _texto(par, f"[Completar: {p}]")


def construir_docx(d: dict) -> Document:
    doc = Document()
    sec = doc.sections[0]
    sec.orientation = WD_ORIENT.LANDSCAPE
    sec.page_width, sec.page_height = Inches(11), Inches(8.5)
    for lado in ("left_margin", "right_margin"):
        setattr(sec, lado, Inches(0.55))
    sec.top_margin = sec.bottom_margin = Inches(0.5)

    estilo = doc.styles["Normal"]
    estilo.font.name = FUENTE
    estilo.font.size = Pt(9.5)

    for texto, tam in ((d.get("escuela") or "", 14), (f"Planeación didáctica de {d['grado']}° grado", 10)):
        if not texto:
            continue
        p = doc.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p.paragraph_format.space_after = Pt(2)
        _texto(p, texto.upper() if tam == 14 else texto, negrita=True, tam=tam)
    doc.add_paragraph()

    _encabezado(doc, d)
    _integracion(doc, d)
    doc.add_paragraph()
    _sesiones(doc, d)
    doc.add_paragraph()
    _evaluacion(doc, d)
    _firmas(doc, d)
    _anexos(doc, d)
    return doc


def guardar_docx(planeacion_id: str, documento: dict) -> str:
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    destino = os.path.join(OUTPUT_DIR, f"planeacion_{planeacion_id}.docx")
    fd, temporal = tempfile.mkstemp(dir=OUTPUT_DIR, suffix=".docx.tmp")
    os.close(fd)
    try:
        construir_docx(documento).save(temporal)
        os.replace(temporal, destino)
    finally:
        if os.path.exists(temporal):
            os.remove(temporal)
    return destino
