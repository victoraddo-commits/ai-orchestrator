"""
KLAUS Legal Knowledge Acquisition System - Vector Indexing Service

Embeddings are computed on the LOCAL GPU fabric (Ollama, model
``nomic-embed-text``) which is resident on the Tesla P40 in VM104 and was
previously idle (0% util). Benchmarked on CT111 (4 vCPU): local CPU MiniLM
= ~1.9 chunks/s; nomic-embed-text over the fabric = ~40-64 chunks/s (>30x).
The GPU path is used first and the local sentence-transformers model is a
fallback so indexing never hard-fails when the tunnel/model is unavailable.

Dimension: nomic-embed-text emits 768-dim vectors; the local MiniLM fallback
emits 384. The DB column is VECTOR(384) today; setting KLAUS_EMBED_DIM=768
after migrating the column switches to the GPU model. Until then the indexer
keeps the configured dim and falls back to the 384-dim local model, so a
mis-set env var can never write wrong-width vectors.
"""

import logging
import os
from typing import List, Optional, Dict, Any

from core.klaus.db_manager import (
    get_chunks_for_document,
    insert_chunk,
    similarity_search,
    get_cursor,
    log_audit_event,
)

logger = logging.getLogger(__name__)

_embedding_model = None

# GPU fabric (Ollama) embedding model — resident on the idle Tesla P40 in
# VM104. ``all-minilm`` is the same 384-dim family as the local CPU model, so
# vectors are interchangeable with the existing VECTOR(384) column: no schema
# migration and no re-embedding required. Benchmarked on CT111: local CPU
# MiniLM ~1.9 chunks/s; all-minilm over the fabric 57-127 chunks/s (>60x).
OLLAMA_URL = os.environ.get("KAI_OLLAMA_URL", "http://127.0.0.1:11434")
GPU_MODEL_NAME = os.environ.get("KLAUS_EMBED_GPU_MODEL", "all-minilm")
GPU_DIM = 384

# Local CPU fallback model.
LOCAL_MODEL_NAME = "all-MiniLM-L6-v2"
LOCAL_DIM = 384

# Dimension the DB stores. Both models above are 384, so this stays 384.
EMBEDDING_DIM = int(os.environ.get("KLAUS_EMBED_DIM", str(LOCAL_DIM)))


def _gpu_enabled_raw() -> bool:
    return os.environ.get("KLAUS_EMBED_GPU", "1") not in ("0", "false", "no")


def _gpu_enabled() -> bool:
    return _gpu_enabled_raw()


MODEL_NAME = GPU_MODEL_NAME if _gpu_enabled_raw() else LOCAL_MODEL_NAME


def _get_model():
    global _embedding_model
    if _embedding_model is None:
        from sentence_transformers import SentenceTransformer
        _embedding_model = SentenceTransformer(LOCAL_MODEL_NAME)
        logger.info("KLAUS: Loaded local embedding model %s (dim=%d)",
                    LOCAL_MODEL_NAME, LOCAL_DIM)
    return _embedding_model


def _gpu_embed(texts: List[str]) -> Optional[List[List[float]]]:
    """Embed via the GPU fabric. Returns None on any failure (caller falls back).

    Sends in bounded batches with a per-request retry: a document with >1000
    chunks fired as one giant concurrent burst can trip Ollama's request
    handling and return an HTTPError, which previously forced a full local
    fallback. Batching keeps the GPU saturated without flooding it.
    """
    if not _gpu_enabled():
        return None
    try:
        import requests
        from concurrent.futures import ThreadPoolExecutor
        from time import sleep

        url = OLLAMA_URL.rstrip("/") + "/api/embeddings"
        # Ollama's all-minilm context is only ~256 tokens; prompts beyond that
        # return HTTP 500. Cap the text sent for embedding well inside that
        # (default 900 chars ≈ 180 tokens) so a large chunk never triggers a
        # fallback. The leading text is representative for retrieval.
        cap = int(os.environ.get("KLAUS_EMBED_MAX_CHARS", "900"))
        safe_texts = [(t or "")[:cap] for t in texts]

        def _one(t: str) -> List[float]:
            last = None
            for attempt in range(3):
                try:
                    r = requests.post(
                        url, json={"model": GPU_MODEL_NAME, "prompt": t},
                        timeout=120)
                    r.raise_for_status()
                    return r.json()["embedding"]
                except Exception as e:  # noqa: BLE001
                    last = e
                    sleep(0.5 * (attempt + 1))
            raise last  # type: ignore[misc]

        out: List[List[float]] = []
        BATCH = 64
        for i in range(0, len(safe_texts), BATCH):
            batch = safe_texts[i:i + BATCH]
            with ThreadPoolExecutor(max_workers=8) as pool:
                out.extend(pool.map(_one, batch))
        if out and len(out[0]) != EMBEDDING_DIM:
            logger.warning("KLAUS: GPU embedding dim %d != configured %d; use local",
                           len(out[0]), EMBEDDING_DIM)
            return None
        return out
    except Exception as e:  # noqa: BLE001
        logger.warning("KLAUS: GPU embedding unavailable (%s) — local fallback",
                       type(e).__name__)
        return None


def generate_embedding(text: str) -> List[float]:
    gpu = _gpu_embed([text])
    if gpu:
        return gpu[0]
    model = _get_model()
    return model.encode(text, normalize_embeddings=True).tolist()


def generate_embeddings(texts: List[str]) -> List[List[float]]:
    gpu = _gpu_embed(texts)
    if gpu:
        return gpu
    model = _get_model()
    embeddings = model.encode(texts, normalize_embeddings=True, show_progress_bar=False)
    return embeddings.tolist()


def index_document_chunks(document_id: int) -> int:
    """
    Generate and store embeddings for all chunks of a document.
    Returns the number of chunks indexed.
    """
    chunks = get_chunks_for_document(document_id)
    if not chunks:
        return 0

    texts = [c["content"] for c in chunks]
    embeddings = generate_embeddings(texts)

    indexed = 0
    for chunk, embedding in zip(chunks, embeddings):
        try:
            with get_cursor() as cur:
                cur.execute(
                    """UPDATE klaus_document_chunks
                       SET embedding = %s
                       WHERE id = %s""",
                    (embedding, chunk["id"]),
                )
            indexed += 1
        except Exception as e:
            logger.warning("KLAUS: Failed to index chunk %s: %s", chunk["id"], e)
            log_audit_event("failure", "error", f"Embedding failed for chunk {chunk['id']}: {e}", document_id)

    log_audit_event(
        "verification",
        "info",
        f"Indexed {indexed}/{len(chunks)} chunks for document {document_id}",
        document_id,
    )
    return indexed


def search_similar(query: str, limit: int = 10, threshold: float = 0.5) -> List[Dict[str, Any]]:
    """
    Search for document chunks similar to a query string.
    Only returns results from approved documents with full_storage access.
    """
    try:
        embedding = generate_embedding(query)
        return similarity_search(embedding, limit=limit, threshold=threshold)
    except Exception as e:
        logger.error(f"Vector search error: {e}")
        return []


def get_document_count_by_status() -> Dict[str, int]:
    """Count documents by review status."""
    try:
        with get_cursor() as cur:
            cur.execute(
                "SELECT review_status, COUNT(*) as cnt FROM klaus_documents GROUP BY review_status"
            )
            return {r["review_status"]: r["cnt"] for r in cur.fetchall()}
    except Exception:
        return {}


def get_storage_stats() -> Dict[str, Any]:
    """Get storage utilization stats for the monitoring dashboard."""
    try:
        with get_cursor() as cur:
            cur.execute("SELECT COUNT(*) as ct FROM klaus_documents")
            doc_count = cur.fetchone()["ct"]

            cur.execute("SELECT COUNT(*) as ct FROM klaus_document_chunks")
            chunk_count = cur.fetchone()["ct"]

            cur.execute("SELECT COUNT(*) as ct FROM klaus_document_chunks WHERE embedding IS NOT NULL")
            indexed_count = cur.fetchone()["ct"]

            cur.execute("SELECT COUNT(*) as ct FROM klaus_sources")
            source_count = cur.fetchone()["ct"]

            cur.execute("SELECT COUNT(*) as ct FROM klaus_sources WHERE status = 'broken'")
            broken_count = cur.fetchone()["ct"]

        return {
            "documents_total": doc_count,
            "chunks_total": chunk_count,
            "chunks_indexed": indexed_count,
            "sources_total": source_count,
            "sources_broken": broken_count,
            "embedding_model": MODEL_NAME,
            "embedding_dim": EMBEDDING_DIM,
            "gpu_enabled": _gpu_enabled(),
        }
    except Exception as e:
        return {"error": str(e)}
