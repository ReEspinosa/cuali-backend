import os

from dotenv import load_dotenv

# Carga el .env de la raíz del proyecto (busca desde el directorio de
# trabajo hacia arriba). Esto es necesario porque otros mecanismos de carga
# de .env (como pydantic-settings en app/core/config.py) llenan SUS PROPIOS
# objetos de configuración, pero no copian esos valores a os.environ -- así
# que si dependiéramos solo de os.environ ya puesto por alguien más, esta
# variable podría llegar vacía aunque sí esté en el .env.
load_dotenv()

# ---------------------------------------------------------------------------
# RUTAS
# ---------------------------------------------------------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# RAG_DATA_DIR permite mover pdfs/chunks/chroma_db fuera de la carpeta del
# código (útil al integrar esto dentro de otro proyecto, para no mezclar
# datos generados con el código versionado en git). Si no se define, usa
# data/ junto a este archivo, como hasta ahora.
DATA_DIR = os.environ.get("RAG_DATA_DIR") or os.path.join(BASE_DIR, "data")
PDF_DIR = os.path.join(DATA_DIR, "pdfs")             # Aquí van los PDFs de la SEP
CHUNKS_PATH = os.path.join(DATA_DIR, "chunks.jsonl")  # Salida de ingest.py
CHROMA_DIR = os.path.join(DATA_DIR, "chroma_db")      # Persistencia de Chroma
CATALOG_PATH = os.path.join(BASE_DIR, "catalog.json")  # Metadata de cada PDF

# ---------------------------------------------------------------------------
# OPENAI (o cualquier servidor compatible con la API de OpenAI, como LM
# Studio corriendo un modelo propio -- define OPENAI_BASE_URL para eso)
# ---------------------------------------------------------------------------
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
OPENAI_BASE_URL = os.environ.get("OPENAI_BASE_URL") or None  # None = api.openai.com oficial
EMBEDDING_MODEL = "text-embedding-3-small"
# Acepta CHAT_MODEL o, si no está, OPENAI_MODEL (para no duplicar la
# variable si tu proyecto ya usa ese nombre, como es el caso de Cuali).
CHAT_MODEL = os.environ.get("CHAT_MODEL") or os.environ.get("OPENAI_MODEL", "gpt-4o-mini")

# Algunos servidores propios (como uno detrás de nginx con auth_basic) piden
# HTTP Basic Auth (usuario/contraseña) ADEMÁS o en vez de la API key tipo
# Bearer que manda el SDK de OpenAI por default. Si defines estas dos
# variables, se manda un header Authorization: Basic ... en cada request.
LLM_USER = os.environ.get("LLM_USER")
LLM_PASSWORD = os.environ.get("LLM_PASSWORD")

# ---------------------------------------------------------------------------
# EMBEDDINGS: elige el proveedor
# ---------------------------------------------------------------------------
# "openai" -> usa la API de OpenAI (requiere OPENAI_API_KEY, es de paga).
# "local"  -> usa un modelo open-source que corre en tu máquina, sin costo ni
#             API key (requiere pip install sentence-transformers, que
#             instala también pytorch -- es una descarga grande).
# Cámbialo aquí o con la variable de entorno EMBEDDING_PROVIDER.
EMBEDDING_PROVIDER = os.environ.get("EMBEDDING_PROVIDER", "openai")
LOCAL_EMBEDDING_MODEL = "intfloat/multilingual-e5-base"

# ---------------------------------------------------------------------------
# CHUNKING
# ---------------------------------------------------------------------------
CHUNK_TARGET_CHARS = 1000     # tamaño objetivo de cada chunk
CHUNK_MIN_CHARS = 300         # por debajo de esto, se fusiona con el siguiente
CHUNK_OVERLAP_CHARS = 150     # traslape entre chunks consecutivos (solo en fallback)

# Patrones para detectar posibles encabezados de sección/lección dentro de los
# libros de la SEP. AJUSTA esta lista revisando 2-3 PDFs reales: la Nueva
# Escuela Mexicana organiza por "Proyectos" y "Escenarios", los libros de
# generaciones anteriores por "Bloques" y "Lecciones".
SECTION_HEADER_PATTERNS = [
    r"^(Bloque|BLOQUE)\s+\d+",
    r"^(Lecci[oó]n|LECCI[OÓ]N)\s+\d+",
    r"^(Proyecto|PROYECTO)\s+\d+",
    r"^(Escenario|ESCENARIO)[:\s]",
    r"^(Unidad|UNIDAD)\s+\d+",
    r"^(Secuencia|SECUENCIA)\s+\d+",
    r"^(Fase|FASE)\s+\d+",        # estructura típica de los libros NEM
    r"^(Momento|MOMENTO)\s+\d+",
]

# ---------------------------------------------------------------------------
# RETRIEVAL
# ---------------------------------------------------------------------------
TOP_K_VECTOR = 15   # candidatos que trae la búsqueda semántica
TOP_K_FINAL = 5      # chunks finales que se mandan al LLM
VECTOR_WEIGHT = 0.6  # peso de la similitud semántica en el score híbrido
BM25_WEIGHT = 0.4    # peso de la coincidencia de palabras clave (BM25)