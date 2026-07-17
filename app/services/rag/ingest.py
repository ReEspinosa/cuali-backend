"""
ingest.py
---------
Lee todos los PDFs en data/pdfs/ (incluyendo subcarpetas por grado), extrae
el texto conservando el número de página, detecta encabezados de sección
(Bloque, Lección, Proyecto, Escenario, etc.) cuando existen, y arma chunks
con traslape sencillo (sin duplicar texto) que conservan el rango de página
REAL donde aparece cada fragmento.

Limpieza aplicada:
  - Ruido de pie de página de exportación InDesign (caracteres duplicados)
  - Bloques de texto repetidos por extracción de layout complejo -- incluso
    cuando la repetición queda partida entre el final de una página y el
    inicio de la siguiente
  - Fragmentos revueltos de infografías (no reconstruibles, se descartan)

Salida: data/chunks.jsonl (un chunk por línea, en formato JSON)

Uso:
    python ingest.py
"""
import json
import os
import re
import uuid

import pdfplumber
from tqdm import tqdm

from . import config

# Ventana (en líneas) donde se busca un bloque duplicado
DEDUPE_WINDOW = 30
DEDUPE_MIN_RUN = 3


def load_catalog():
    if not os.path.exists(config.CATALOG_PATH):
        raise FileNotFoundError(
            f"No encontré {config.CATALOG_PATH}. Corre primero: python generate_catalog.py"
        )
    with open(config.CATALOG_PATH, "r", encoding="utf-8") as f:
        catalog = json.load(f)
    catalog.pop("_comentario", None)
    return catalog


def find_pdfs():
    """Recorre PDF_DIR recursivamente (incluye subcarpetas por grado) y
    regresa las rutas relativas de cada PDF encontrado."""
    rel_paths = []
    for root, _, files in os.walk(config.PDF_DIR):
        for f in files:
            if f.lower().endswith(".pdf"):
                full_path = os.path.join(root, f)
                rel_paths.append(os.path.relpath(full_path, config.PDF_DIR))
    return sorted(rel_paths)


# ---------------------------------------------------------------------------
# Limpieza de ruido -- se aplica por página (artefactos que no cruzan
# fronteras de página)
# ---------------------------------------------------------------------------

def _is_doubled_token(token):
    """True si un token tiene cada caracter duplicado consecutivo, p. ej.
    '0066//0066//2244' en vez de '06/06/24'."""
    if len(token) < 4 or len(token) % 2 != 0:
        return False
    pairs = [token[i:i + 2] for i in range(0, len(token), 2)]
    return all(p[0] == p[1] for p in pairs)


def is_footer_artifact(line):
    """
    Detecta las líneas de pie de página que traen estos PDFs de exportación
    de InDesign, donde el nombre del archivo, número de página, fecha y hora
    aparecen con cada caracter duplicado (p. ej.
    '11EERROO__NNSS--000011--224488..iinnddbb 110033 0066//0066//2244 1111::3344',
    que en realidad dice '1ERO_NS-001-248.indb 103 06/06/24 11:34').
    Es puro ruido de maquetación, no contenido del libro.
    """
    tokens = line.strip().split()
    if not tokens:
        return False
    doubled = sum(1 for t in tokens if _is_doubled_token(t))
    return doubled / len(tokens) >= 0.5


def is_short_line(line, max_len=4):
    t = line.strip()
    return 0 < len(t) <= max_len


def strip_garbled_line_runs(lines, min_run=6):
    """
    Las infografías (texto en cajas libres, no en columnas rectas) a veces
    hacen que pdfplumber regrese los fragmentos de texto en orden geométrico
    en vez de orden de lectura -- el resultado es una corrida larga de
    líneas sueltas de 1-4 caracteres sin ningún sentido. Esto NO se puede
    reconstruir (la información real se perdió en el desorden), así que se
    elimina en vez de dejarlo como ruido en los embeddings.
    """
    n = len(lines)
    is_short = [is_short_line(l) for l in lines]
    to_remove = set()
    i = 0
    while i < n:
        if is_short[i]:
            j = i
            while j < n and is_short[j]:
                j += 1
            if j - i >= min_run:
                to_remove.update(range(i, j))
            i = j
        else:
            i += 1
    return [line for idx, line in enumerate(lines) if idx not in to_remove]


def is_garbled_line(line, min_tokens=8, short_ratio=0.7):
    """Detecta una sola línea larga compuesta casi enteramente de fragmentos
    de 1-3 caracteres (mismo fenómeno que strip_garbled_line_runs, pero
    cuando pdfplumber los junta en una sola línea en vez de varias)."""
    tokens = line.strip().split()
    if len(tokens) < min_tokens:
        return False
    short = sum(1 for t in tokens if len(t) <= 3)
    return short / len(tokens) >= short_ratio


def extract_pages(pdf_path):
    """Devuelve una lista de (numero_pagina, texto) para un PDF, ya sin
    ruido de pie de página ni fragmentos revueltos de infografías. La
    deduplicación de bloques repetidos se hace después, a nivel de sección,
    porque a veces la repetición cruza la frontera entre dos páginas."""
    pages = []
    with pdfplumber.open(pdf_path) as pdf:
        for i, page in enumerate(pdf.pages, start=1):
            raw = page.extract_text() or ""
            clean_lines = [
                line for line in raw.split("\n")
                if not is_footer_artifact(line)
            ]
            clean_lines = strip_garbled_line_runs(clean_lines)
            clean_lines = [l for l in clean_lines if not is_garbled_line(l)]
            pages.append((i, "\n".join(clean_lines)))
    return pages


# ---------------------------------------------------------------------------
# Deduplicación de bloques repetidos -- a nivel de sección (cruza páginas)
# ---------------------------------------------------------------------------

def dedupe_repeated_line_runs(lines, pages, min_run=DEDUPE_MIN_RUN, window=DEDUPE_WINDOW):
    """
    A veces pdfplumber extrae el mismo bloque de texto dos veces en zonas
    con diseño complejo (texto que rodea una imagen, cuadros de texto
    superpuestos) -- y esa repetición puede quedar partida entre el final de
    una página y el inicio de la siguiente. Por eso esto recibe TODAS las
    líneas de la sección (ya juntando varias páginas), no solo una página a
    la vez.

    Busca una secuencia de al menos `min_run` líneas idénticas que se repite
    dentro de una ventana cercana, y conserva solo la primera aparición.
    `pages` es una lista paralela a `lines` (a qué página pertenece cada
    línea) y se filtra igual, para no perder la trazabilidad de página.
    """
    n = len(lines)
    to_remove = set()
    i = 0
    while i < n:
        if i in to_remove:
            i += 1
            continue
        max_j = min(n, i + window)
        for j in range(i + 1, max_j):
            run_len = 0
            while (i + run_len < n and j + run_len < n
                   and lines[i + run_len].strip() == lines[j + run_len].strip()
                   and lines[i + run_len].strip() != ""):
                run_len += 1
            if run_len >= min_run:
                for k in range(run_len):
                    to_remove.add(j + k)
                break
        i += 1
    kept_lines = [l for idx, l in enumerate(lines) if idx not in to_remove]
    kept_pages = [p for idx, p in enumerate(pages) if idx not in to_remove]
    return kept_lines, kept_pages


def build_page_bounds(lines, line_pages):
    """A partir de la lista final de líneas y a qué página pertenece cada
    una, construye una lista de (pagina, offset_acumulado_hasta_donde_llega)
    -- el mismo formato que usa page_for_offset() -- para poder ubicar
    después cualquier chunk en su página real, ya con las líneas
    deduplicadas."""
    bounds = []
    cumulative = 0
    for idx, (line, page) in enumerate(zip(lines, line_pages)):
        if idx > 0:
            cumulative += 1  # el separador '\n' que agrega "\n".join
        cumulative += len(line)
        if bounds and bounds[-1][0] == page:
            bounds[-1] = (page, cumulative)
        else:
            bounds.append((page, cumulative))
    return bounds


def is_section_header(line):
    line = line.strip()
    if not line or len(line) > 120:
        return False
    for pattern in config.SECTION_HEADER_PATTERNS:
        if re.match(pattern, line):
            return True
    return False


def split_into_sections(pages):
    """
    Recorre las páginas y agrupa el texto en secciones usando los patrones de
    encabezado. Antes de cerrar cada sección, deduplica bloques de texto
    repetidos (incluso si la repetición cruza páginas dentro de la misma
    sección) y reconstruye el mapa de páginas ya con las líneas finales.

    Si no se detecta ningún encabezado en todo el libro, regresa una sola
    sección "Contenido general" que cubre todo el documento.
    """
    sections = []
    current_title = "Contenido general"
    current_lines = []
    current_line_pages = []
    current_page_start = pages[0][0] if pages else 1
    last_page = current_page_start

    def close_section(end_page):
        if not current_lines:
            return
        deduped_lines, deduped_pages = dedupe_repeated_line_runs(
            current_lines, current_line_pages
        )
        text = "\n".join(deduped_lines).strip()
        if not text:
            return
        page_bounds = build_page_bounds(deduped_lines, deduped_pages)
        sections.append({
            "title": current_title,
            "text": text,
            "page_start": current_page_start,
            "page_end": end_page,
            "page_bounds": page_bounds,
        })

    for page_num, text in pages:
        last_page = page_num
        for line in text.split("\n"):
            if is_section_header(line):
                close_section(page_num)
                current_title = line.strip()
                current_lines = []
                current_line_pages = []
                current_page_start = page_num
            else:
                current_lines.append(line)
                current_line_pages.append(page_num)

    close_section(last_page)
    return [s for s in sections if len(s["text"]) >= 20]


def page_for_offset(page_bounds, offset, fallback_page):
    """Dado un offset de caracter dentro del texto de una sección, regresa la
    página real a la que pertenece ese punto del texto."""
    for page_num, end_offset in page_bounds:
        if offset <= end_offset:
            return page_num
    return page_bounds[-1][0] if page_bounds else fallback_page


def split_by_size(text, target=None, overlap=None):
    """
    Ventana deslizante sobre el texto con UN SOLO traslape entre chunks
    consecutivos. Ajusta el corte al salto de línea más cercano cuando es
    posible, para no partir una palabra u oración a la mitad.

    Regresa una lista de tuplas (texto, offset_inicio, offset_fin) para poder
    mapear después cada chunk a su rango de páginas real.
    """
    target = target or config.CHUNK_TARGET_CHARS
    overlap = overlap or config.CHUNK_OVERLAP_CHARS
    n = len(text)

    if n <= target:
        stripped = text.strip()
        return [(stripped, 0, n)] if stripped else []

    chunks = []
    start = 0
    step = max(target - overlap, 1)
    while start < n:
        end = min(start + target, n)
        if end < n:
            newline_pos = text.rfind("\n", start + int(target * 0.5), end)
            if newline_pos != -1:
                end = newline_pos
        piece = text[start:end].strip()
        if piece:
            chunks.append((piece, start, end))
        if end >= n:
            break
        next_start = end - overlap
        if next_start <= start:  # salvaguarda contra loops sin avance
            next_start = start + step
        start = next_start
    return chunks


def build_chunks_for_section(section, book_meta, book_file):
    pieces = split_by_size(section["text"])
    page_bounds = section["page_bounds"]

    records = []
    for piece_text, start_off, end_off in pieces:
        page_start = page_for_offset(page_bounds, start_off, section["page_start"])
        page_end = page_for_offset(page_bounds, max(end_off - 1, start_off), section["page_end"])

        if len(piece_text) < config.CHUNK_MIN_CHARS and records:
            # fusiona chunks muy pequeños con el anterior para evitar ruido
            records[-1]["text"] += "\n" + piece_text
            records[-1]["page_end"] = page_end
            continue

        records.append({
            "id": str(uuid.uuid4()),
            "book_file": book_file,
            "libro": book_meta["libro"],
            "grado": book_meta["grado"],
            "materia": book_meta["materia"],
            "section_title": section["title"],
            "page_start": page_start,
            "page_end": page_end,
            "text": piece_text,
        })
    return records


def main():
    catalog = load_catalog()
    pdf_files = find_pdfs()

    if not pdf_files:
        print(f"No hay PDFs en {config.PDF_DIR}. Copia ahí los libros de la SEP "
              f"(puedes conservar tus subcarpetas por grado).")
        return

    missing_meta = [f for f in pdf_files if f not in catalog]
    if missing_meta:
        print("⚠️  Estos PDFs no tienen entrada en catalog.json y se van a omitir.")
        print("   Corre primero: python generate_catalog.py")
        for f in missing_meta:
            print(f"   - {f}")

    all_records = []
    section_titles_seen = set()

    for pdf_file in tqdm(pdf_files, desc="Procesando PDFs"):
        if pdf_file not in catalog:
            continue
        book_meta = catalog[pdf_file]
        pdf_path = os.path.join(config.PDF_DIR, pdf_file)

        pages = extract_pages(pdf_path)
        sections = split_into_sections(pages)

        for section in sections:
            section_titles_seen.add(section["title"])
            all_records.extend(build_chunks_for_section(section, book_meta, pdf_file))

    os.makedirs(os.path.dirname(config.CHUNKS_PATH), exist_ok=True)
    with open(config.CHUNKS_PATH, "w", encoding="utf-8") as f:
        for record in all_records:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    print(f"\n✅ Listo. {len(all_records)} chunks guardados en {config.CHUNKS_PATH}")

    if section_titles_seen == {"Contenido general"}:
        print("\n⚠️  No se detectó NINGÚN encabezado de sección en tus PDFs "
              "(todo quedó como 'Contenido general').")
        print("   Esto no rompe nada -- las páginas de cada chunk siguen siendo "
              "precisas -- pero si quieres títulos de sección más útiles en las "
              "citas, revisa un PDF real y ajusta SECTION_HEADER_PATTERNS en "
              "config.py.")


if __name__ == "__main__":
    main()