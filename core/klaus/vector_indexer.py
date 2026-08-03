"""
KLAUS Legal Knowledge Acquisition System - Vector Indexing Service

Generates embeddings for document chunks using local sentence-transformers.
Uses a lightweight 384-dimension model (all-MiniLM-L6-v2) running entirely
locally -- no external API calls, in compliance with the security requirements.

Stores embeddings in PostgreSQL through the db_manager insert_chunk path
and supports similarity search against approved, full_storage documents only.
"""

import logging
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
MODEL_NAME = "all-MiniLM-L6-v2"
EMBEDDING_DIM = 384


def _get_model():
    global _embedding_model
    if _embedding_model is None:
        from sentence_transformers import SentenceTransformer
        _embedding_model = SentenceTransformer(MODEL_NAME)
        logger.info("KLAUS: Loaded embedding model %s (dim=%d)", MODEL_NAME, EMBEDDING_DIM)
    return _embedding_model


def generate_embedding(text: str) -> List[float]:
    model = _get_model()
    return model.encode(text, normalize_embeddings=True).tolist()


def generate_embeddings(texts: List[str]) -> List[List[float]]:
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
        }
    except Exception as e:
        return {"error": str(e)}
