"""
embeddings.py
-------------
Capa que unifica cómo se generan los embeddings, sin importar si usas la API
de OpenAI o un modelo local (open-source, sin API key, corre en tu máquina).

Tanto build_index.py como retrieval.py usan este módulo -- así siempre usan
el MISMO método para indexar y para buscar. Mezclar métodos (indexar con uno
y buscar con otro) daría resultados sin sentido, porque cada modelo coloca
los significados en un espacio vectorial distinto.

Para evitar ese error por accidente, build_index.py guarda qué proveedor usó
en data/chroma_db/embedding_provider.json, y retrieval.py lee ese archivo en
vez de confiar en lo que diga config.EMBEDDING_PROVIDER en ese momento.
"""
import json
import os

from . import config

_local_model = None


def _get_local_model():
    global _local_model
    if _local_model is None:
        from sentence_transformers import SentenceTransformer
        print(f"Cargando modelo local de embeddings: {config.LOCAL_EMBEDDING_MODEL} "
              f"(la primera vez tarda porque se descarga, ~1-2 GB)...")
        _local_model = SentenceTransformer(config.LOCAL_EMBEDDING_MODEL)
    return _local_model


def _marker_path():
    return os.path.join(config.CHROMA_DIR, "embedding_provider.json")


def get_active_provider():
    """Lee qué proveedor/modelo se usó para construir el índice actual (si
    ya existe uno). Si no existe todavía, regresa lo que diga config.py."""
    path = _marker_path()
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    model = (config.EMBEDDING_MODEL if config.EMBEDDING_PROVIDER == "openai"
             else config.LOCAL_EMBEDDING_MODEL)
    return {"provider": config.EMBEDDING_PROVIDER, "model": model}


def save_active_provider(provider, model):
    os.makedirs(config.CHROMA_DIR, exist_ok=True)
    with open(_marker_path(), "w", encoding="utf-8") as f:
        json.dump({"provider": provider, "model": model}, f)


def embed_texts(texts, is_query=False, provider=None, model=None):
    """
    Genera embeddings para una lista de textos.

    is_query=True aplica el prefijo 'query: ' en vez de 'passage: ' -- el
    modelo local (E5) espera ese prefijo distinto para preguntas vs.
    contenido, es parte de cómo fue entrenado y afecta la calidad si se
    omite.
    """
    if provider is None:
        info = get_active_provider()
        provider, model = info["provider"], info["model"]

    if provider == "openai":
        if not config.OPENAI_API_KEY:
            raise EnvironmentError(
                "EMBEDDING_PROVIDER='openai' pero no está definida la variable "
                "de entorno OPENAI_API_KEY."
            )
        from openai import OpenAI
        client = OpenAI(api_key=config.OPENAI_API_KEY, base_url=config.OPENAI_BASE_URL)
        response = client.embeddings.create(model=model, input=texts)
        return [item.embedding for item in response.data]

    elif provider == "local":
        model_obj = _get_local_model()
        prefix = "query: " if is_query else "passage: "
        prefixed = [prefix + t for t in texts]
        vectors = model_obj.encode(prefixed, normalize_embeddings=True, show_progress_bar=False)
        return vectors.tolist()

    else:
        raise ValueError(f"EMBEDDING_PROVIDER desconocido: '{provider}' (usa 'openai' o 'local')")