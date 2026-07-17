"""
Construye la cuadrícula de una sopa de letras a partir de una lista de
palabras. Esto NO usa el LLM — es un algoritmo determinista de acomodo:
intenta colocar cada palabra en una dirección y posición aleatoria (8
direcciones: horizontal, vertical y las 2 diagonales, en ambos sentidos),
permitiendo que se crucen si comparten una letra, y rellena el resto de la
cuadrícula con letras aleatorias.
"""

import random
import unicodedata

DIRECCIONES = [
    (0, 1), (0, -1),   # horizontal derecha / izquierda
    (1, 0), (-1, 0),   # vertical abajo / arriba
    (1, 1), (1, -1),   # diagonal abajo-derecha / abajo-izquierda
    (-1, 1), (-1, -1),  # diagonal arriba-derecha / arriba-izquierda
]

ALFABETO = "ABCDEFGHIJKLMNÑOPQRSTUVWXYZ"


def _normalizar(palabra: str) -> str:
    s = palabra.upper().replace(" ", "").replace("-", "")
    s = s.replace("Ñ", "§")  # proteger la Ñ antes de quitar acentos
    s = "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))
    s = s.replace("§", "Ñ")
    return "".join(c for c in s if c.isalpha())


def construir_sopa_letras(palabras_originales: list[str]) -> dict:
    normalizadas = [(orig, _normalizar(orig)) for orig in palabras_originales]
    normalizadas = [(o, n) for o, n in normalizadas if 3 <= len(n) <= 12]

    if not normalizadas:
        return {"grid": [], "soluciones": [], "palabras": [], "tamano": 0}

    tam = max(10, max(len(n) for _, n in normalizadas) + 2)
    tam = min(tam, 15)

    grid: list[list[str | None]] = [[None] * tam for _ in range(tam)]
    soluciones = []
    palabras_colocadas = []

    # Palabras más largas primero: encajan mejor con menos conflictos.
    for original, palabra in sorted(normalizadas, key=lambda x: -len(x[1])):
        length = len(palabra)
        colocada = False

        for _ in range(300):
            dr, dc = random.choice(DIRECCIONES)

            if dr == 1:
                r0_min, r0_max = 0, tam - length
            elif dr == -1:
                r0_min, r0_max = length - 1, tam - 1
            else:
                r0_min, r0_max = 0, tam - 1

            if dc == 1:
                c0_min, c0_max = 0, tam - length
            elif dc == -1:
                c0_min, c0_max = length - 1, tam - 1
            else:
                c0_min, c0_max = 0, tam - 1

            if r0_min > r0_max or c0_min > c0_max:
                continue

            r0 = random.randint(r0_min, r0_max)
            c0 = random.randint(c0_min, c0_max)
            celdas = [(r0 + dr * i, c0 + dc * i) for i in range(length)]

            valido = all(
                0 <= r < tam and 0 <= c < tam and (grid[r][c] is None or grid[r][c] == ch)
                for (r, c), ch in zip(celdas, palabra)
            )
            if not valido:
                continue

            for (r, c), ch in zip(celdas, palabra):
                grid[r][c] = ch
            soluciones.append({"palabra": original, "celdas": [[r, c] for r, c in celdas]})
            palabras_colocadas.append(original)
            colocada = True
            break

        # Si no se pudo colocar tras 300 intentos, se omite (cuadrícula muy llena).
        _ = colocada

    for r in range(tam):
        for c in range(tam):
            if grid[r][c] is None:
                grid[r][c] = random.choice(ALFABETO)

    return {
        "grid": grid,
        "soluciones": soluciones,
        "palabras": palabras_colocadas,
        "tamano": tam,
    }