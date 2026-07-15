import json
import os
import glob

# Ruta donde se descargaron tus archivos
CARPETA = "/Users/rbkespinosa/Desktop/followers_and_following"

def extraer_username(entry):
    """Intenta extraer el username de una entrada, sin importar la forma exacta del JSON."""
    # Caso típico: {"string_list_data": [{"value": "usuario", ...}]}
    for detail in entry.get("string_list_data", []):
        if "value" in detail:
            return detail["value"]
        if "href" in detail:
            # A veces solo viene el link, ej: https://www.instagram.com/usuario
            return detail["href"].rstrip("/").split("/")[-1]

    # Caso alternativo: el username viene directo en la entrada
    if "value" in entry:
        return entry["value"]

    return None


def cargar_usernames(nombre_archivo):
    path = os.path.join(CARPETA, nombre_archivo)
    with open(path, encoding='utf-8') as f:
        data = json.load(f)

    # La estructura puede venir como lista directa o dentro de una key
    if isinstance(data, list):
        items = data
    elif isinstance(data, dict):
        # Puede venir bajo "relationships_following", "relationships_followers", etc.
        items = None
        for key, value in data.items():
            if isinstance(value, list):
                items = value
                break
        if items is None:
            items = []
    else:
        items = []

    usernames = set()
    sin_extraer = 0
    for entry in items:
        username = extraer_username(entry)
        if username:
            usernames.add(username)
        else:
            sin_extraer += 1

    if sin_extraer:
        print(f"⚠️  {nombre_archivo}: no pude extraer {sin_extraer} entradas. "
              f"Ejemplo de estructura: {items[0] if items else 'N/A'}")

    return usernames

def cargar_todos_los_followers():
    """Carga todos los archivos followers_N.json que existan en la carpeta."""
    patron = os.path.join(CARPETA, "followers_*.json")
    archivos = sorted(glob.glob(patron))

    if not archivos:
        raise FileNotFoundError(f"No encontré archivos followers_*.json en {CARPETA}")

    print(f"📂 Encontré {len(archivos)} archivo(s) de seguidores: "
          f"{[os.path.basename(a) for a in archivos]}\n")

    todos = set()
    for archivo in archivos:
        nombre = os.path.basename(archivo)
        usernames = cargar_usernames(nombre)
        todos.update(usernames)

    return todos


seguidores = cargar_todos_los_followers()
siguiendo = cargar_usernames("following.json")

no_te_siguen = sorted(siguiendo - seguidores)
no_los_sigues = sorted(seguidores - siguiendo)

print(f"Sigues a {len(siguiendo)} cuentas")
print(f"Te siguen {len(seguidores)} cuentas\n")

print(f"❌ {len(no_te_siguen)} cuentas que sigues NO te siguen de vuelta:\n")
for u in no_te_siguen:
    print(u)

print(f"\n➕ {len(no_los_sigues)} cuentas que te siguen y tú no sigues:\n")
for u in no_los_sigues:
    print(u)

# Opcional: guardar en un archivo de texto para revisarlo con calma
with open(os.path.join(CARPETA, "no_me_siguen.txt"), "w", encoding='utf-8') as f:
    f.write("\n".join(no_te_siguen))

print(f"\n✅ Lista guardada en: {os.path.join(CARPETA, 'no_me_siguen.txt')}")
