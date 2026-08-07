"""Migration: Klaus (old) → Legal Brain Permanent Store (new).

Moves approved documents from the old klaus_documents table
to the new WORM permanent store. This is a ONE-WAY migration —
data is copied, not deleted from the source.

The old Klaus module continues to work; the Legal Brain is
an additional, hardened store.
"""

import hashlib
import uuid
import logging
from typing import Dict, Any, List, Optional

from .permanent import init_permanent_store
from .permanent.store import (
    add_source,
    insert_document,
    get_document_by_hash,
)

logger = logging.getLogger("kai.legal_brain.migrate")


def _try_get_klaus_sources() -> List[Dict[str, Any]]:
    """Try to get sources from the old Klaus database."""
    try:
        from core.klaus.db_manager import list_sources
        return list_sources()
    except Exception as e:
        logger.warning(f"Could not read Klaus sources: {e}")
        return []


def _try_get_klaus_documents(review_status: str = "approved") -> List[Dict[str, Any]]:
    """Try to get documents from the old Klaus database."""
    try:
        from core.klaus.db_manager import list_documents
        return list_documents(review_status=review_status, limit=10000)
    except Exception as e:
        logger.warning(f"Could not read Klaus documents: {e}")
        return []


def _try_get_klaus_chunks(document_id: int) -> List[Dict[str, Any]]:
    """Try to get chunks from the old Klaus database."""
    try:
        from core.klaus.db_manager import get_chunks_for_document
        return get_chunks_for_document(document_id)
    except Exception:
        return []


def migrate_sources() -> Dict[str, Any]:
    """Migrate Klaus sources to Legal Brain permanent store."""
    klaus_sources = _try_get_klaus_sources()
    results = {"total": len(klaus_sources), "migrated": 0, "skipped": 0, "errors": 0}

    init_permanent_store()

    for src in klaus_sources:
        try:
            add_source(
                url=src["url"],
                domain=src["domain"],
                tier=src.get("tier", 2),
                jurisdiction=src.get("jurisdiction", "Ghana"),
            )
            results["migrated"] += 1
        except Exception as e:
            logger.warning(f"Failed to migrate source {src.get('url')}: {e}")
            results["errors"] += 1

    return results


def migrate_documents(dry_run: bool = False) -> Dict[str, Any]:
    """Migrate approved Klaus documents to Legal Brain permanent store.

    Args:
        dry_run: If True, count without actually migrating.

    Returns stats about the migration.
    """
    klaus_docs = _try_get_klaus_documents(review_status="approved")
    if not klaus_docs:
        return {"total": 0, "migrated": 0, "skipped": 0, "errors": 0, "message": "No Klaus documents found"}

    results = {"total": len(klaus_docs), "migrated": 0, "skipped": 0, "errors": 0}

    if not dry_run:
        init_permanent_store()

    for doc in klaus_docs:
        try:
            # Check if already migrated (by file_hash)
            existing = get_document_by_hash(doc.get("file_hash", ""))
            if existing and not dry_run:
                results["skipped"] += 1
                continue

            if not dry_run:
                # Ensure source exists in permanent store
                _ensure_source_exists(doc)
                source_id = _get_mapped_source_id(doc)

                insert_document(
                    source_id=source_id,
                    title=doc["title"],
                    content_hash=doc["file_hash"],
                    file_path=doc.get("file_path", ""),
                    category=doc.get("category", "Legislation"),
                    copyright_classification=doc.get("copyright_classification", "official_public_access"),
                    access_level=doc.get("access_level", "public"),
                    jurisdiction=doc.get("jurisdiction", "Ghana"),
                    court=doc.get("court"),
                    year=doc.get("year"),
                    citation_text=_build_citation(doc),
                    effective_date=str(doc["effective_date"]) if doc.get("effective_date") else None,
                    page_count=0,
                    file_size_bytes=0,
                    approved_by="migration",
                    review_status="approved",
                )

            results["migrated"] += 1

        except Exception as e:
            logger.warning(f"Failed to migrate document {doc.get('title')}: {e}")
            results["errors"] += 1

    return results


def _ensure_source_exists(doc: Dict[str, Any]):
    """Ensure the document's source exists in the permanent store."""
    source_id = _get_mapped_source_id(doc)
    existing = None
    try:
        from .permanent.store import get_source
        existing = get_source(source_id)
    except Exception:
        pass

    if not existing and doc.get("source_id"):
        # Try to create from old source data
        try:
            from core.klaus.db_manager import get_source
            old_source = get_source(int(doc["source_id"]))
            if old_source:
                add_source(
                    url=old_source["url"],
                    domain=old_source["domain"],
                    tier=old_source.get("tier", 2),
                    jurisdiction=old_source.get("jurisdiction", "Ghana"),
                )
        except Exception:
            # Create a placeholder source
            add_source(
                url=f"migrated://source/{doc.get('source_id', 'unknown')}",
                domain="migrated.legal",
                tier=2,
                jurisdiction="Ghana",
            )


def _get_mapped_source_id(doc: Dict[str, Any]) -> str:
    """Get or create a source mapping."""
    # Use URL-based lookup
    try:
        from core.klaus.db_manager import get_source
        if doc.get("source_id"):
            old_source = get_source(int(doc["source_id"]))
            if old_source:
                # Check permanent store for this URL
                from .permanent.store import get_connection
                with get_connection() as conn:
                    row = conn.execute(
                        "SELECT id FROM sources WHERE url = ?", (old_source["url"],)
                    ).fetchone()
                    if row:
                        return row["id"]
    except Exception:
        pass
    return str(uuid.uuid4())


def _build_citation(doc: Dict[str, Any]) -> Optional[str]:
    """Build a standard citation string from document metadata."""
    parts = []
    if doc.get("court"):
        parts.append(doc["court"])
    if doc.get("year"):
        parts.append(str(doc["year"]))
    if doc.get("legislation_number"):
        parts.append(doc["legislation_number"])
    return " ".join(parts) if parts else None


def get_migration_status() -> Dict[str, Any]:
    """Check migration status — what's in old vs new store."""
    status = {
        "klaus_documents": 0,
        "legal_brain_documents": 0,
        "migrated": False,
    }

    try:
        from core.klaus.db_manager import list_documents
        klaus = list_documents(limit=10000)
        status["klaus_documents"] = len(klaus)
    except Exception:
        pass

    try:
        from .permanent.store import get_store_stats
        stats = get_store_stats()
        status["legal_brain_documents"] = stats["documents_total"]
    except Exception:
        pass

    status["migrated"] = status["legal_brain_documents"] > 0
    return status
