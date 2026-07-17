"""
generate_catalog.py
--------------------
Genera catalog.json automáticamente a partir de los nombres de archivo que ya
tienes en data/pdfs/ (respeta subcarpetas por grado, como
"01_primero de primaria/1°_Proyectos de Aula.pdf").

Reconoce el patrón de nombres de los libros de la Nueva Escuela Mexicana:
    "1°_Proyectos de Aula.pdf"          -> grado=1, libro="Proyectos de Aula"
    "4°-Proyectos Comunitarios.pdf"     -> grado=4, libro="Proyectos Comunitarios"
    "2°_Múltiples Lenguajes.pdf"        -> grado=2, libro="Múltiples Lenguajes"

Si no logra inferir el grado de un archivo (nombre atípico), lo deja fuera y
te avisa al final para que lo agregues a mano.

Uso:
    python generate_catalog.py

Esto SOBREESCRIBE catalog.json. Después de correrlo, ábrelo y revisa que los
nombres de "libro" queden limpios (a veces el nombre original del PDF trae
errores de captura, p. ej. "Mútiples" en vez de "Múltiples") -- corrígelos
ahí, no hace falta renombrar el PDF.
"""
import json
import os
import re

from . import config

GRADE_WORDS = {
    "primero": "1", "segundo": "2", "tercero": "3",
    "cuarto": "4", "quinto": "5", "sexto": "6",
}

# Acepta "1°_", "1º_", "1_", "1-", "1 ", e incluso "4°Cartografía" (sin separador)
FILENAME_PATTERN = re.compile(r"^(\d)\s*[°º]?[\s_\-]*(.+?)\.pdf$", re.IGNORECASE)


def guess_grade_from_path(rel_path):
    for part in rel_path.split(os.sep):
        low = part.lower()
        for word, num in GRADE_WORDS.items():
            if word in low:
                return num
    return None


def parse_filename(filename):
    match = FILENAME_PATTERN.match(filename)
    if not match:
        return None, None
    grado, libro_raw = match.groups()
    libro = re.sub(r"[_\-]+", " ", libro_raw).strip()
    libro = re.sub(r"\s{2,}", " ", libro)
    return grado, libro


def main():
    if not os.path.isdir(config.PDF_DIR):
        print(f"No existe {config.PDF_DIR}. Crea la carpeta y copia ahí tus PDFs.")
        return

    catalog = {}
    unresolved = []

    for root, _, files in os.walk(config.PDF_DIR):
        for filename in sorted(files):
            if not filename.lower().endswith(".pdf"):
                continue
            rel_path = os.path.relpath(os.path.join(root, filename), config.PDF_DIR)

            grado, libro = parse_filename(filename)
            if grado is None:
                grado = guess_grade_from_path(rel_path)
                libro = os.path.splitext(filename)[0]

            if grado is None:
                unresolved.append(rel_path)
                continue

            catalog[rel_path] = {
                "grado": grado,
                "materia": libro,
                "libro": f"{libro} - {grado}° grado",
            }

    with open(config.CATALOG_PATH, "w", encoding="utf-8") as f:
        json.dump(catalog, f, ensure_ascii=False, indent=2)

    print(f"✅ catalog.json generado con {len(catalog)} libros en {config.CATALOG_PATH}")
    print("   Ábrelo y revisa que los nombres de 'libro'/'materia' hayan quedado limpios.")

    if unresolved:
        print(f"\n⚠️  No pude inferir el grado de {len(unresolved)} archivo(s). "
              f"Agrégalos a mano en catalog.json:")
        for u in unresolved:
            print(f"   - {u}")


if __name__ == "__main__":
    main()