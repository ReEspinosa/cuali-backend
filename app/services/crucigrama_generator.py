"""
Construye la cuadrícula de un crucigrama a partir de una lista de
{palabra, pista}. Esto NO usa el LLM — es un algoritmo determinista:

1. Ordena las palabras de mayor a menor longitud.
2. Coloca la primera en horizontal, en el origen.
3. Para cada palabra siguiente, busca una letra en común con alguna palabra
   ya colocada y, si el cruce es geométricamente válido (no pisa otra letra
   distinta, no queda pegada a otra palabra sin espacio), la coloca ahí en
   la dirección perpendicular.
4. Al final, recorta la cuadrícula a su tamaño mínimo y numera las celdas
   según la convención estándar de crucigramas (una celda se numera si
   inicia una palabra horizontal y/o vertical).

Las palabras que no logran cruzarse con ninguna otra se reportan aparte en
"no_colocadas" en vez de forzarlas sueltas en la cuadrícula.
"""

import random
import unicodedata


def _normalizar(palabra: str) -> str:
    s = palabra.upper().replace(" ", "").replace("-", "")
    s = s.replace("Ñ", "§")
    s = "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))
    s = s.replace("§", "Ñ")
    return "".join(c for c in s if c.isalpha())


def _valido(grid: dict, norm: str, r0: int, c0: int, direccion: str) -> bool:
    L = len(norm)
    for i, ch in enumerate(norm):
        r = r0 + i if direccion == "V" else r0
        c = c0 if direccion == "V" else c0 + i
        existente = grid.get((r, c))
        if existente is not None:
            if existente != ch:
                return False
        else:
            if direccion == "V":
                if grid.get((r, c - 1)) is not None or grid.get((r, c + 1)) is not None:
                    return False
            else:
                if grid.get((r - 1, c)) is not None or grid.get((r + 1, c)) is not None:
                    return False

    if direccion == "V":
        antes, despues = grid.get((r0 - 1, c0)), grid.get((r0 + L, c0))
    else:
        antes, despues = grid.get((r0, c0 - 1)), grid.get((r0, c0 + L))
    return antes is None and despues is None


def _colocar(grid: dict, norm: str, r0: int, c0: int, direccion: str) -> None:
    for i, ch in enumerate(norm):
        r = r0 + i if direccion == "V" else r0
        c = c0 if direccion == "V" else c0 + i
        grid[(r, c)] = ch


def construir_crucigrama(items: list[dict]) -> dict:
    candidatos = [
        (it["palabra"], _normalizar(it["palabra"]), it["pista"])
        for it in items
        if 2 <= len(_normalizar(it["palabra"])) <= 12
    ]
    candidatos.sort(key=lambda x: -len(x[1]))

    if not candidatos:
        return {"celdas": [], "numeros": {}, "pistas": [], "ancho": 0, "alto": 0, "no_colocadas": []}

    grid: dict[tuple[int, int], str] = {}
    colocadas = []

    orig0, norm0, pista0 = candidatos[0]
    _colocar(grid, norm0, 0, 0, "H")
    colocadas.append({"palabra": orig0, "normal": norm0, "pista": pista0, "fila": 0, "col": 0, "dir": "H"})

    no_colocadas = []

    for orig, norm, pista in candidatos[1:]:
        opciones = []
        for pw in colocadas:
            for i, ch in enumerate(norm):
                for j, ch2 in enumerate(pw["normal"]):
                    if ch != ch2:
                        continue
                    if pw["dir"] == "H":
                        r0, c0, direccion = pw["fila"] - i, pw["col"] + j, "V"
                    else:
                        r0, c0, direccion = pw["fila"] + j, pw["col"] - i, "H"
                    if _valido(grid, norm, r0, c0, direccion):
                        opciones.append((r0, c0, direccion))

        if opciones:
            r0, c0, direccion = random.choice(opciones)
            _colocar(grid, norm, r0, c0, direccion)
            colocadas.append({"palabra": orig, "normal": norm, "pista": pista, "fila": r0, "col": c0, "dir": direccion})
        else:
            no_colocadas.append(orig)

    filas = [r for r, _ in grid]
    cols = [c for _, c in grid]
    min_r, min_c = min(filas), min(cols)
    alto = max(filas) - min_r + 1
    ancho = max(cols) - min_c + 1

    celdas = [[None] * ancho for _ in range(alto)]
    for (r, c), ch in grid.items():
        celdas[r - min_r][c - min_c] = ch

    for p in colocadas:
        p["fila"] -= min_r
        p["col"] -= min_c

    numeros: dict[tuple[int, int], int] = {}
    contador = 1
    for r in range(alto):
        for c in range(ancho):
            if celdas[r][c] is None:
                continue
            inicia_h = (c == 0 or celdas[r][c - 1] is None) and (c + 1 < ancho and celdas[r][c + 1] is not None)
            inicia_v = (r == 0 or celdas[r - 1][c] is None) and (r + 1 < alto and celdas[r + 1][c] is not None)
            if inicia_h or inicia_v:
                numeros[(r, c)] = contador
                contador += 1

    pistas = []
    for p in colocadas:
        numero = numeros.get((p["fila"], p["col"]))
        pistas.append({
            "numero": numero,
            "direccion": "across" if p["dir"] == "H" else "down",
            "pista": p["pista"],
            "palabra": p["palabra"],
            "longitud": len(p["normal"]),
            "fila": p["fila"],
            "col": p["col"],
        })
    pistas.sort(key=lambda x: (x["direccion"] != "across", x["numero"]))

    return {
        "celdas": celdas,
        "numeros": {f"{r}-{c}": n for (r, c), n in numeros.items()},
        "pistas": pistas,
        "ancho": ancho,
        "alto": alto,
        "no_colocadas": no_colocadas,
    }