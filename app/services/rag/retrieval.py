"""
retrieval.py
------------
Búsqueda híbrida sobre el índice de Chroma: combina similitud semántica
(embeddings) con coincidencia de palabras clave (BM25). Esto ayuda cuando el
docente usa términos pedagógicos exactos (p. ej. "aprendizajes esperados",
"campo formativo") que a veces los embeddings por sí solos no priorizan bien.

También soporta filtrar por grado y/o materia antes de buscar, lo cual reduce
muchísimo el ruido cuando ya sabes en qué contexto está el docente.
"""
import json
import os
import re

import chromadb
from rank_bm25 import BM25Okapi

from . import config
from .embeddings import embed_texts

_client = None
_collection = None
_bm25 = None
_bm25_docs = None  # lista de dicts alineada con el índice del BM25


def _tokenize(text):
    return re.findall(r"\w+", text.lower())


def _load_collection():
    global _client, _collection
    if _collection is None:
        _client = chromadb.PersistentClient(path=config.CHROMA_DIR)
        _collection = _client.get_collection("sep_libros")
    return _collection


def _load_bm25():
    """Construye el índice BM25 en memoria a partir de chunks.jsonl."""
    global _bm25, _bm25_docs
    if _bm25 is not None:
        return _bm25, _bm25_docs

    if not os.path.exists(config.CHUNKS_PATH):
        raise FileNotFoundError(
            f"No encontré {config.CHUNKS_PATH}. Corre ingest.py y build_index.py primero."
        )

    docs = []
    with open(config.CHUNKS_PATH, "r", encoding="utf-8") as f:
        for line in f:
            docs.append(json.loads(line))

    tokenized = [_tokenize(d["text"]) for d in docs]
    _bm25 = BM25Okapi(tokenized)
    _bm25_docs = docs
    return _bm25, _bm25_docs


def _normalize(scores):
    if not scores:
        return scores
    lo, hi = min(scores), max(scores)
    if hi - lo < 1e-9:
        return [1.0 for _ in scores]
    return [(s - lo) / (hi - lo) for s in scores]


def hybrid_search(query, grado=None, materia=None,
                   top_k_vector=config.TOP_K_VECTOR,
                   top_k_final=config.TOP_K_FINAL):
    """
    Devuelve los top_k_final chunks más relevantes para `query`, combinando
    score semántico y BM25. Si se pasan grado/materia, filtra el universo de
    búsqueda a esos libros antes de rankear.
    """
    collection = _load_collection()
    bm25, bm25_docs = _load_bm25()

    # --- filtro de metadata (where clause de Chroma) ---
    where = {}
    if grado and materia:
        where = {"$and": [{"grado": grado}, {"materia": materia}]}
    elif grado:
        where = {"grado": grado}
    elif materia:
        where = {"materia": materia}

    # --- búsqueda semántica ---
    query_embedding = embed_texts([query], is_query=True)[0]

    vector_results = collection.query(
        query_embeddings=[query_embedding],
        n_results=top_k_vector,
        where=where or None,
    )

    candidate_ids = vector_results["ids"][0]
    candidate_docs = vector_results["documents"][0]
    candidate_meta = vector_results["metadatas"][0]
    candidate_distances = vector_results["distances"][0]
    # Chroma regresa distancia coseno (menor es mejor) -> convertir a similitud
    vector_scores = [1 - d for d in candidate_distances]

    # --- score BM25 para esos mismos candidatos ---
    id_to_bm25_doc = {d["id"]: d for d in bm25_docs}
    tokenized_query = _tokenize(query)
    bm25_scores_full = bm25.get_scores(tokenized_query)
    id_to_bm25_score = {
        d["id"]: bm25_scores_full[i] for i, d in enumerate(bm25_docs)
    }
    bm25_scores = [id_to_bm25_score.get(cid, 0.0) for cid in candidate_ids]

    # --- combinar scores normalizados ---
    norm_vector = _normalize(vector_scores)
    norm_bm25 = _normalize(bm25_scores)
    combined = [
        config.VECTOR_WEIGHT * v + config.BM25_WEIGHT * b
        for v, b in zip(norm_vector, norm_bm25)
    ]

    ranked = sorted(
        zip(candidate_ids, candidate_docs, candidate_meta, combined),
        key=lambda x: x[3],
        reverse=True,
    )[:top_k_final]

    results = []
    for cid, doc, meta, score in ranked:
        results.append({
            "id": cid,
            "text": doc,
            "score": round(score, 4),
            **meta,
        })
    return results