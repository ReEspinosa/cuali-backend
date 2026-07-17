"""
build_index.py
---------------
Lee data/chunks.jsonl, genera embeddings en lotes (con OpenAI o con un modelo
local, según config.EMBEDDING_PROVIDER), y los guarda en una colección de
Chroma persistida en disco.

Uso:
    python build_index.py
"""
import json
import os

import chromadb
from tqdm import tqdm

from . import config
from .embeddings import embed_texts, save_active_provider

BATCH_SIZE = 96  # tamaño de lote para no exceder límites de la API / memoria


def load_chunks():
    if not os.path.exists(config.CHUNKS_PATH):
        raise FileNotFoundError(
            f"No encontré {config.CHUNKS_PATH}. Corre primero: python ingest.py"
        )
    records = []
    with open(config.CHUNKS_PATH, "r", encoding="utf-8") as f:
        for line in f:
            records.append(json.loads(line))
    return records


def batched(iterable, size):
    for i in range(0, len(iterable), size):
        yield iterable[i:i + size]


def main():
    provider = config.EMBEDDING_PROVIDER
    model = config.EMBEDDING_MODEL if provider == "openai" else config.LOCAL_EMBEDDING_MODEL
    print(f"Proveedor de embeddings: {provider} ({model})")

    if provider == "openai" and not config.OPENAI_API_KEY:
        raise EnvironmentError(
            "EMBEDDING_PROVIDER='openai' pero no está definida OPENAI_API_KEY. "
            "Define la variable de entorno, o usa EMBEDDING_PROVIDER=local para "
            "generar embeddings sin API key."
        )

    chroma_client = chromadb.PersistentClient(path=config.CHROMA_DIR)

    # Si ya existía la colección de una corrida previa, se recrea desde cero
    # para evitar duplicados al reprocesar los mismos PDFs.
    try:
        chroma_client.delete_collection("sep_libros")
    except Exception:
        pass
    collection = chroma_client.create_collection(
        name="sep_libros",
        metadata={"hnsw:space": "cosine"},
    )

    records = load_chunks()
    print(f"Generando embeddings para {len(records)} chunks...")

    for batch in tqdm(list(batched(records, BATCH_SIZE)), desc="Embeddings"):
        texts = [r["text"] for r in batch]
        vectors = embed_texts(texts, is_query=False, provider=provider, model=model)

        collection.add(
            ids=[r["id"] for r in batch],
            embeddings=vectors,
            documents=texts,
            metadatas=[{
                "book_file": r["book_file"],
                "libro": r["libro"],
                "grado": r["grado"],
                "materia": r["materia"],
                "section_title": r["section_title"],
                "page_start": r["page_start"],
                "page_end": r["page_end"],
            } for r in batch],
        )

    # Guardar qué proveedor/modelo se usó, para que retrieval.py use
    # exactamente el mismo al buscar (mezclar proveedores da resultados
    # sin sentido).
    save_active_provider(provider, model)

    print(f"\n✅ Índice construido en {config.CHROMA_DIR} con {len(records)} chunks "
          f"(proveedor: {provider}).")


if __name__ == "__main__":
    main()