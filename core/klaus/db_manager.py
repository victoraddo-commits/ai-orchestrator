"""
KLAUS Legal Knowledge Acquisition System - Database Manager

Thread-safe PostgreSQL/psycopg2 database layer for the KLAUS
legal knowledge system. All tables use the `klaus_` prefix as
specified in the approved Phase 17O implementation plan.
"""

import os
import hashlib
import threading
import json
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List
from pathlib import Path

import psycopg2
from psycopg2.extras import Json, RealDictCursor

DB_CONFIG = {
    "host": os.getenv("KLAUS_DB_HOST", "localhost"),
    "port": int(os.getenv("KLAUS_DB_PORT", "5432")),
    "database": os.getenv("KLAUS_DB_NAME", "klaus_db"),
    "user": os.getenv("KLAUS_DB_USER", "klaus_user"),
    "password": os.getenv("KLAUS_DB_PASSWORD", "klaus_password"),
}

STORAGE_ROOT = Path(
    os.getenv(
        "KLAUS_STORAGE_ROOT",
        "/var/lib/ai-orchestrator/klaus_storage",
    )
)

_local = threading.local()


def _ensure_storage():
    for sub in ("raw", "processed"):
        (STORAGE_ROOT / sub).mkdir(parents=True, exist_ok=True)


def _ensure_default_date():
    return datetime.now(timezone.utc)


def get_connection():
    if not hasattr(_local, "conn") or _local.conn.closed:
        _local.conn = psycopg2.connect(**DB_CONFIG)
    return _local.conn


@contextmanager
def get_cursor():
    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        yield cur
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()


def init_database():
    from core.klaus.schema import SCHEMA_SQL

    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(SCHEMA_SQL)
        conn.commit()
        _ensure_storage()
        return True
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        cur.close()


# ── Sources ────────────────────────────────────────────────────────────

def add_source(url: str, domain: str, tier: int, jurisdiction: str = "Ghana") -> int:
    with get_cursor() as cur:
        cur.execute(
            """INSERT INTO klaus_sources (url, domain, tier, jurisdiction, status)
               VALUES (%s, %s, %s, %s, 'active')
               ON CONFLICT (url) DO UPDATE SET last_discovered = %s
               RETURNING id""",
            (url, domain, tier, jurisdiction, _ensure_default_date()),
        )
        row = cur.fetchone()
        return row["id"]


def get_source(source_id: int) -> Optional[Dict[str, Any]]:
    with get_cursor() as cur:
        cur.execute("SELECT * FROM klaus_sources WHERE id = %s", (source_id,))
        row = cur.fetchone()
        return dict(row) if row else None


def list_sources(
    tier: Optional[int] = None,
    status: Optional[str] = None,
    jurisdiction: Optional[str] = None,
) -> List[Dict[str, Any]]:
    clauses = []
    params = []
    if tier is not None:
        clauses.append("tier = %s")
        params.append(tier)
    if status is not None:
        clauses.append("status = %s")
        params.append(status)
    if jurisdiction is not None:
        clauses.append("jurisdiction = %s")
        params.append(jurisdiction)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    with get_cursor() as cur:
        cur.execute(f"SELECT * FROM klaus_sources{where} ORDER BY tier, domain", params)
        return [dict(r) for r in cur.fetchall()]


def update_source_status(source_id: int, status: str, reliability_score: Optional[float] = None):
    if reliability_score is not None:
        with get_cursor() as cur:
            cur.execute(
                "UPDATE klaus_sources SET status = %s, reliability_score = %s WHERE id = %s",
                (status, reliability_score, source_id),
            )
    else:
        with get_cursor() as cur:
            cur.execute(
                "UPDATE klaus_sources SET status = %s WHERE id = %s",
                (status, source_id),
            )


# ── Documents ──────────────────────────────────────────────────────────

def compute_file_hash(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def insert_document(
    source_id: int,
    title: str,
    file_hash: str,
    file_path: str,
    category: str,
    jurisdiction: str,
    copyright_classification: str,
    access_level: str,
    court: Optional[str] = None,
    year: Optional[int] = None,
    legislation_number: Optional[str] = None,
    effective_date: Optional[str] = None,
    parent_document_id: Optional[int] = None,
) -> int:
    with get_cursor() as cur:
        cur.execute(
            """INSERT INTO klaus_documents
               (source_id, title, file_hash, file_path, category, jurisdiction,
                copyright_classification, access_level, court, year,
                legislation_number, effective_date, parent_document_id)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
               RETURNING id""",
            (
                source_id, title, file_hash, file_path, category, jurisdiction,
                copyright_classification, access_level, court, year,
                legislation_number, effective_date, parent_document_id,
            ),
        )
        row = cur.fetchone()
        return row["id"]


def get_document(document_id: int) -> Optional[Dict[str, Any]]:
    with get_cursor() as cur:
        cur.execute("SELECT * FROM klaus_documents WHERE id = %s", (document_id,))
        row = cur.fetchone()
        return dict(row) if row else None


def get_document_by_hash(file_hash: str) -> Optional[Dict[str, Any]]:
    with get_cursor() as cur:
        cur.execute(
            "SELECT * FROM klaus_documents WHERE file_hash = %s", (file_hash,)
        )
        row = cur.fetchone()
        return dict(row) if row else None


def get_document_by_title(title: str) -> Optional[Dict[str, Any]]:
    with get_cursor() as cur:
        cur.execute(
            "SELECT * FROM klaus_documents WHERE title = %s", (title,)
        )
        row = cur.fetchone()
        return dict(row) if row else None


def update_document_review_status(document_id: int, review_status: str):
    with get_cursor() as cur:
        cur.execute(
            """UPDATE klaus_documents SET review_status = %s,
               updated_at = %s WHERE id = %s""",
            (review_status, _ensure_default_date(), document_id),
        )


def update_document_version(
    document_id: int,
    file_hash: str,
    file_path: str,
    title: str,
) -> int:
    """Create a new version of an existing document."""
    with get_cursor() as cur:
        cur.execute(
            """INSERT INTO klaus_documents
               (source_id, title, file_hash, file_path, category, jurisdiction,
                copyright_classification, access_level, court, year,
                legislation_number, effective_date, version, parent_document_id)
               SELECT source_id, %s, %s, %s, category, jurisdiction,
                      copyright_classification, access_level, court, year,
                      legislation_number, effective_date, version + 1, id
               FROM klaus_documents WHERE id = %s
               RETURNING id""",
            (title, file_hash, file_path, document_id),
        )
        row = cur.fetchone()
        return row["id"]


def list_documents(
    category: Optional[str] = None,
    review_status: Optional[str] = None,
    copyright_classification: Optional[str] = None,
    jurisdiction: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
) -> List[Dict[str, Any]]:
    clauses = []
    params = []
    if category:
        clauses.append("category = %s")
        params.append(category)
    if review_status:
        clauses.append("review_status = %s")
        params.append(review_status)
    if copyright_classification:
        clauses.append("copyright_classification = %s")
        params.append(copyright_classification)
    if jurisdiction:
        clauses.append("jurisdiction = %s")
        params.append(jurisdiction)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    with get_cursor() as cur:
        cur.execute(
            f"SELECT * FROM klaus_documents{where} ORDER BY created_at DESC LIMIT %s OFFSET %s",
            params + [limit, offset],
        )
        return [dict(r) for r in cur.fetchall()]


def count_documents_by_status() -> Dict[str, int]:
    with get_cursor() as cur:
        cur.execute(
            """SELECT review_status, COUNT(*) as cnt
               FROM klaus_documents GROUP BY review_status"""
        )
        return {r["review_status"]: r["cnt"] for r in cur.fetchall()}


# ── Chunks & Embeddings ────────────────────────────────────────────────

def insert_chunk(
    document_id: int,
    chunk_index: int,
    content: str,
    embedding: Optional[List[float]] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> int:
    with get_cursor() as cur:
        cur.execute(
            """INSERT INTO klaus_document_chunks
               (document_id, chunk_index, content, embedding, metadata)
               VALUES (%s, %s, %s, %s, %s)
               RETURNING id""",
            (
                document_id,
                chunk_index,
                content,
                embedding,
                Json(metadata) if metadata else None,
            ),
        )
        row = cur.fetchone()
        return row["id"]


def get_chunks_for_document(document_id: int) -> List[Dict[str, Any]]:
    with get_cursor() as cur:
        cur.execute(
            """SELECT id, chunk_index, content, metadata
               FROM klaus_document_chunks
               WHERE document_id = %s ORDER BY chunk_index""",
            (document_id,),
        )
        return [dict(r) for r in cur.fetchall()]


def similarity_search(
    embedding: List[float],
    limit: int = 10,
    threshold: float = 0.5,
) -> List[Dict[str, Any]]:
    with get_cursor() as cur:
        cur.execute(
            """SELECT c.id, c.document_id, c.chunk_index, c.content, c.metadata,
                      1 - (c.embedding <=> %s::vector) AS similarity
               FROM klaus_document_chunks c
               JOIN klaus_documents d ON c.document_id = d.id
               WHERE 1 - (c.embedding <=> %s::vector) >= %s
                 AND d.review_status = 'approved'
                 AND d.access_level = 'full_storage'
               ORDER BY similarity DESC LIMIT %s""",
            (embedding, embedding, threshold, limit),
        )
        return [dict(r) for r in cur.fetchall()]


# ── Audit Logs ──────────────────────────────────────────────────────────

def log_audit_event(
    event_type: str,
    severity: str,
    message: str,
    document_id: Optional[int] = None,
) -> int:
    with get_cursor() as cur:
        cur.execute(
            """INSERT INTO klaus_audit_logs
               (document_id, event_type, severity, message)
               VALUES (%s, %s, %s, %s)
               RETURNING id""",
            (document_id, event_type, severity, message),
        )
        row = cur.fetchone()
        return row["id"]


def get_audit_logs(
    event_type: Optional[str] = None,
    severity: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
) -> List[Dict[str, Any]]:
    clauses = []
    params = []
    if event_type:
        clauses.append("event_type = %s")
        params.append(event_type)
    if severity:
        clauses.append("severity = %s")
        params.append(severity)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    with get_cursor() as cur:
        cur.execute(
            f"SELECT * FROM klaus_audit_logs{where} ORDER BY created_at DESC LIMIT %s OFFSET %s",
            params + [limit, offset],
        )
        return [dict(r) for r in cur.fetchall()]


def get_failed_sources() -> List[Dict[str, Any]]:
    with get_cursor() as cur:
        cur.execute("SELECT * FROM klaus_sources WHERE status = 'broken'")
        return [dict(r) for r in cur.fetchall()]


def get_documents_flagged_for_review() -> List[Dict[str, Any]]:
    with get_cursor() as cur:
        cur.execute(
            "SELECT * FROM klaus_documents WHERE review_status = 'flagged' ORDER BY created_at"
        )
        return [dict(r) for r in cur.fetchall()]


def init_sample_data() -> bool:
    """Seed initial Ghana legal sources into the database."""
    from core.klaus.scheduler import TIER_1_SEEDS

    try:
        for seed in TIER_1_SEEDS:
            add_source(
                url=seed["url"],
                domain=seed["domain"],
                tier=seed["tier"],
                jurisdiction=seed["jurisdiction"],
            )
        log_audit_event("discovery", "info", f"Seeded {len(TIER_1_SEEDS)} initial sources")
        return True
    except Exception:
        return False


def init_sample_data():
    """Seed the database with sample Ghana legal sources."""
    sources = [
        ("https://parliament.gh", "parliament.gh", 1, "Ghana"),
        ("https://judiciary.gov.gh", "judiciary.gov.gh", 1, "Ghana"),
        ("https://ghalii.org", "ghalii.org", 2, "Ghana"),
    ]
    for url, domain, tier, jurisdiction in sources:
        try:
            add_source(url, domain, tier, jurisdiction)
        except Exception:
            pass
    return True


def get_storage_estimate() -> int:
    """Estimate total raw file bytes stored. Returns 0 on error."""
    try:
        with get_cursor() as cur:
            cur.execute(
                """SELECT COUNT(*) as doc_count FROM klaus_documents
                   WHERE access_level = 'full_storage'"""
            )
            row = cur.fetchone()
            return row["doc_count"] if row else 0
    except Exception:
        return 0
