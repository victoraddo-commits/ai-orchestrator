"""Permanent Legal Brain store — WORM document operations.

All writes to the permanent store are INSERT-only.
No UPDATE on document records after initial insert.
Hash-chain integrity maintained via content_hash + prev_hash.
"""

import hashlib
import uuid
import json
from pathlib import Path
from typing import Optional, Dict, Any, List
from datetime import datetime, timezone

from . import get_connection, get_db_path, _now

# ---------------------------------------------------------------------------
# Sources
# ---------------------------------------------------------------------------

def add_source(
    url: str,
    domain: str,
    tier: int,
    jurisdiction: str = "Ghana",
) -> str:
    """Register a legal source. Returns source ID."""
    source_id = str(uuid.uuid4())
    with get_connection() as conn:
        conn.execute(
            """INSERT INTO sources (id, url, domain, tier, jurisdiction, status, last_checked)
               VALUES (?, ?, ?, ?, ?, 'active', ?)
               ON CONFLICT(url) DO UPDATE SET last_checked = ?""",
            (source_id, url, domain, tier, jurisdiction, _now(), _now()),
        )
    return source_id


def get_source(source_id: str) -> Optional[Dict[str, Any]]:
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM sources WHERE id = ?", (source_id,)).fetchone()
        return dict(row) if row else None


def list_sources(
    tier: Optional[int] = None,
    status: Optional[str] = None,
) -> List[Dict[str, Any]]:
    clauses = []
    params = []
    if tier is not None:
        clauses.append("tier = ?")
        params.append(tier)
    if status is not None:
        clauses.append("status = ?")
        params.append(status)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    with get_connection() as conn:
        rows = conn.execute(
            f"SELECT * FROM sources{where} ORDER BY tier, domain", params
        ).fetchall()
        return [dict(r) for r in rows]


def update_source_status(source_id: str, status: str, reliability_score: Optional[float] = None):
    with get_connection() as conn:
        if reliability_score is not None:
            conn.execute(
                "UPDATE sources SET status = ?, reliability_score = ? WHERE id = ?",
                (status, reliability_score, source_id),
            )
        else:
            conn.execute(
                "UPDATE sources SET status = ? WHERE id = ?",
                (status, source_id),
            )


# ---------------------------------------------------------------------------
# Documents (WORM — INSERT only, no UPDATE)
# ---------------------------------------------------------------------------

def compute_hash(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _get_last_doc_hash(conn) -> Optional[str]:
    """Get the content_hash of the most recently inserted document for hash-chaining."""
    row = conn.execute(
        "SELECT content_hash FROM documents ORDER BY ingested_at DESC LIMIT 1"
    ).fetchone()
    return row["content_hash"] if row else None


def insert_document(
    source_id: str,
    title: str,
    content_hash: str,
    file_path: str,
    category: str,
    copyright_classification: str,
    access_level: str = "public",
    jurisdiction: str = "Ghana",
    court: Optional[str] = None,
    year: Optional[int] = None,
    citation_text: Optional[str] = None,
    effective_date: Optional[str] = None,
    parent_doc_id: Optional[str] = None,
    page_count: int = 0,
    file_size_bytes: int = 0,
    approved_by: Optional[str] = None,
    review_status: str = "pending",
) -> str:
    """Insert a document into the permanent WORM store.

    Once inserted, the document record CANNOT be modified.
    Versioning is handled by creating new records with parent_doc_id.
    """
    doc_id = str(uuid.uuid4())

    with get_connection() as conn:
        prev_hash = _get_last_doc_hash(conn)
        conn.execute(
            """INSERT INTO documents
               (id, source_id, title, content_hash, prev_hash, file_path,
                category, jurisdiction, court, year, citation_text,
                copyright_classification, access_level, review_status,
                effective_date, page_count, file_size_bytes,
                parent_doc_id, approved_by)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                doc_id, source_id, title, content_hash, prev_hash, file_path,
                category, jurisdiction, court, year, citation_text,
                copyright_classification, access_level, review_status,
                effective_date, page_count, file_size_bytes,
                parent_doc_id, approved_by,
            ),
        )
    _log_audit("document.inserted", doc_id, f"Document '{title}' inserted")
    return doc_id


def approve_document(document_id: str, operator: str) -> bool:
    """Approve a document for search indexing.

    This is the ONLY update allowed on a document record —
    transitioning review_status from 'pending' to 'approved'.
    Once approved, NO further updates are permitted.
    """
    with get_connection() as conn:
        row = conn.execute(
            "SELECT review_status FROM documents WHERE id = ?", (document_id,)
        ).fetchone()
        if not row:
            return False
        if row["review_status"] != "pending":
            return False  # Already approved or rejected
        conn.execute(
            """UPDATE documents SET review_status = 'approved', approved_by = ?
               WHERE id = ? AND review_status = 'pending'""",
            (operator, document_id),
        )
    _log_audit("document.approved", document_id, f"Approved by {operator}")
    return True


def reject_document(document_id: str, operator: str) -> bool:
    """Reject a document — it stays in the store but won't appear in search."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT review_status FROM documents WHERE id = ?", (document_id,)
        ).fetchone()
        if not row:
            return False
        if row["review_status"] != "pending":
            return False
        conn.execute(
            """UPDATE documents SET review_status = 'rejected', approved_by = ?
               WHERE id = ? AND review_status = 'pending'""",
            (operator, document_id),
        )
    _log_audit("document.rejected", document_id, f"Rejected by {operator}")
    return True


def get_document(document_id: str) -> Optional[Dict[str, Any]]:
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM documents WHERE id = ?", (document_id,)).fetchone()
        return dict(row) if row else None


def get_document_by_hash(content_hash: str) -> Optional[Dict[str, Any]]:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM documents WHERE content_hash = ?", (content_hash,)
        ).fetchone()
        return dict(row) if row else None


def list_documents(
    category: Optional[str] = None,
    review_status: Optional[str] = None,
    jurisdiction: Optional[str] = "Ghana",
    limit: int = 100,
    offset: int = 0,
) -> List[Dict[str, Any]]:
    clauses = ["jurisdiction = ?"]
    params = [jurisdiction]
    if category:
        clauses.append("category = ?")
        params.append(category)
    if review_status:
        clauses.append("review_status = ?")
        params.append(review_status)
    where = " WHERE " + " AND ".join(clauses)
    with get_connection() as conn:
        rows = conn.execute(
            f"SELECT * FROM documents{where} ORDER BY ingested_at DESC LIMIT ? OFFSET ?",
            params + [limit, offset],
        ).fetchall()
        return [dict(r) for r in rows]


def search_documents(query: str, limit: int = 20) -> List[Dict[str, Any]]:
    """Full-text search on document titles and citation text."""
    with get_connection() as conn:
        rows = conn.execute(
            """SELECT * FROM documents
               WHERE (title LIKE ? OR citation_text LIKE ?)
                 AND review_status = 'approved'
                 AND jurisdiction = 'Ghana'
               ORDER BY ingested_at DESC LIMIT ?""",
            (f"%{query}%", f"%{query}%", limit),
        ).fetchall()
        return [dict(r) for r in rows]


def get_document_version_chain(document_id: str) -> List[Dict[str, Any]]:
    """Walk the version chain through parent_doc_id references."""
    chain = []
    current_id = document_id
    while current_id:
        doc = get_document(current_id)
        if not doc:
            break
        chain.append(doc)
        current_id = doc.get("parent_doc_id")
    return chain


def count_by_status() -> Dict[str, int]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT review_status, COUNT(*) as cnt FROM documents GROUP BY review_status"
        ).fetchall()
        return {r["review_status"]: r["cnt"] for r in rows}


# ---------------------------------------------------------------------------
# Chunks
# ---------------------------------------------------------------------------

def insert_chunk(
    document_id: str,
    chunk_index: int,
    content: str,
) -> str:
    chunk_id = str(uuid.uuid4())
    content_hash = compute_hash(content.encode("utf-8"))
    with get_connection() as conn:
        conn.execute(
            """INSERT INTO chunks (id, document_id, chunk_index, content, content_hash)
               VALUES (?, ?, ?, ?, ?)""",
            (chunk_id, document_id, chunk_index, content, content_hash),
        )
    return chunk_id


def get_chunks(document_id: str) -> List[Dict[str, Any]]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM chunks WHERE document_id = ? ORDER BY chunk_index",
            (document_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def search_chunks(query: str, limit: int = 10) -> List[Dict[str, Any]]:
    """Simple text search across chunks of approved documents."""
    with get_connection() as conn:
        rows = conn.execute(
            """SELECT c.*, d.title as doc_title, d.citation_text
               FROM chunks c
               JOIN documents d ON c.document_id = d.id
               WHERE c.content LIKE ?
                 AND d.review_status = 'approved'
               ORDER BY d.ingested_at DESC LIMIT ?""",
            (f"%{query}%", limit),
        ).fetchall()
        return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Citations (Knowledge Graph foundation)
# ---------------------------------------------------------------------------

def insert_citation(
    source_doc_id: str,
    target_doc_id: Optional[str] = None,
    target_citation_text: Optional[str] = None,
    citation_type: str = "references",
    context_snippet: Optional[str] = None,
    confidence: float = 1.0,
) -> str:
    citation_id = str(uuid.uuid4())
    with get_connection() as conn:
        conn.execute(
            """INSERT INTO citations
               (id, source_doc_id, target_doc_id, target_citation_text,
                citation_type, context_snippet, confidence)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (citation_id, source_doc_id, target_doc_id, target_citation_text,
             citation_type, context_snippet, confidence),
        )
    return citation_id


def get_citations_for_document(document_id: str) -> List[Dict[str, Any]]:
    with get_connection() as conn:
        rows = conn.execute(
            """SELECT c.*, d.title as target_title, d.citation_text as target_citation
               FROM citations c
               LEFT JOIN documents d ON c.target_doc_id = d.id
               WHERE c.source_doc_id = ?
               ORDER BY c.created_at""",
            (document_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def get_documents_citing(document_id: str) -> List[Dict[str, Any]]:
    """Find all documents that cite this document."""
    with get_connection() as conn:
        rows = conn.execute(
            """SELECT c.*, d.title as source_title
               FROM citations c
               JOIN documents d ON c.source_doc_id = d.id
               WHERE c.target_doc_id = ?
               ORDER BY c.created_at""",
            (document_id,),
        ).fetchall()
        return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Audit Chain (HMAC-chained for tamper evidence)
# ---------------------------------------------------------------------------

def _log_audit(event_type: str, doc_id: Optional[str] = None, details: Optional[str] = None):
    """Write an audit entry with hash-chain to previous entry."""
    with get_connection() as conn:
        prev = conn.execute(
            "SELECT id FROM audit_chain ORDER BY created_at DESC LIMIT 1"
        ).fetchone()

        entry_id = str(uuid.uuid4())
        prev_entry_hash = None
        if prev:
            prev_row = conn.execute(
                "SELECT id, event_type, doc_id, details, created_at FROM audit_chain WHERE id = ?",
                (prev["id"],),
            ).fetchone()
            if prev_row:
                chain_data = f"{prev_row['id']}{prev_row['event_type']}{prev_row['doc_id'] or ''}{prev_row['details'] or ''}{prev_row['created_at']}"
                prev_entry_hash = hashlib.sha256(chain_data.encode()).hexdigest()

        conn.execute(
            """INSERT INTO audit_chain (id, prev_entry_hash, event_type, doc_id, operator, details)
               VALUES (?, ?, ?, ?, 'system', ?)""",
            (entry_id, prev_entry_hash, event_type, doc_id, details),
        )


def get_audit_chain(limit: int = 100) -> List[Dict[str, Any]]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM audit_chain ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]


def verify_audit_chain() -> Dict[str, Any]:
    """Verify the integrity of the entire audit chain."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM audit_chain ORDER BY created_at ASC"
        ).fetchall()

    total = len(rows)
    valid = 0
    invalid = 0
    prev_hash = None

    for i, row in enumerate(rows):
        r = dict(row)
        if i == 0:
            # First entry: prev_entry_hash should be NULL
            if r["prev_entry_hash"] is None:
                valid += 1
            else:
                invalid += 1
        else:
            # Verify hash chain
            if r["prev_entry_hash"] == prev_hash:
                valid += 1
            else:
                invalid += 1

        # Compute hash for next entry
        chain_data = f"{r['id']}{r['event_type']}{r['doc_id'] or ''}{r['details'] or ''}{r['created_at']}"
        prev_hash = hashlib.sha256(chain_data.encode()).hexdigest()

    return {"total": total, "valid": valid, "invalid": invalid, "intact": invalid == 0}


# ---------------------------------------------------------------------------
# Integrity
# ---------------------------------------------------------------------------

def verify_all_document_hashes() -> Dict[str, Any]:
    """Verify content_hash for every document against its stored file."""
    total = 0
    valid = 0
    invalid = 0
    missing = 0

    with get_connection() as conn:
        rows = conn.execute("SELECT id, content_hash, file_path FROM documents").fetchall()

    for row in rows:
        total += 1
        try:
            with open(row["file_path"], "rb") as f:
                actual_hash = compute_hash(f.read())
            if actual_hash == row["content_hash"]:
                valid += 1
            else:
                invalid += 1
        except FileNotFoundError:
            missing += 1

    return {
        "total": total,
        "valid": valid,
        "invalid": invalid,
        "missing": missing,
        "intact": invalid == 0 and missing == 0,
    }


def get_store_stats() -> Dict[str, Any]:
    """Get statistics about the permanent store."""
    with get_connection() as conn:
        doc_count = conn.execute("SELECT COUNT(*) as c FROM documents").fetchone()["c"]
        approved = conn.execute(
            "SELECT COUNT(*) as c FROM documents WHERE review_status = 'approved'"
        ).fetchone()["c"]
        pending = conn.execute(
            "SELECT COUNT(*) as c FROM documents WHERE review_status = 'pending'"
        ).fetchone()["c"]
        chunk_count = conn.execute("SELECT COUNT(*) as c FROM chunks").fetchone()["c"]
        citation_count = conn.execute("SELECT COUNT(*) as c FROM citations").fetchone()["c"]
        source_count = conn.execute("SELECT COUNT(*) as c FROM sources").fetchone()["c"]
        audit_count = conn.execute("SELECT COUNT(*) as c FROM audit_chain").fetchone()["c"]

    db_size = get_db_path().stat().st_size if get_db_path().exists() else 0

    return {
        "documents_total": doc_count,
        "documents_approved": approved,
        "documents_pending": pending,
        "chunks": chunk_count,
        "citations": citation_count,
        "sources": source_count,
        "audit_entries": audit_count,
        "db_size_bytes": db_size,
    }
