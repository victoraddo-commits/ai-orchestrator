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
    from core.klaus.schema import SCHEMA_SQL, MIGRATION_SQL

    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(SCHEMA_SQL)
        cur.execute(MIGRATION_SQL)
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
    # Strip NUL bytes from content — PostgreSQL rejects them
    content = content.replace("\x00", "")
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


# ── Tier Management ─────────────────────────────────────────────────────

def seed_acquisition_tiers() -> int:
    """Seed the 16 acquisition tiers from schema constants. Returns count seeded."""
    from core.klaus.schema import ACQUISITION_TIERS

    count = 0
    with get_cursor() as cur:
        for tier_num, info in ACQUISITION_TIERS.items():
            cur.execute(
                """INSERT INTO klaus_acquisition_tiers
                   (tier_number, tier_name, tier_category, priority_weight, coverage_target)
                   VALUES (%s, %s, %s, %s, %s)
                   ON CONFLICT (tier_number) DO UPDATE SET
                       tier_name = EXCLUDED.tier_name,
                       tier_category = EXCLUDED.tier_category,
                       priority_weight = EXCLUDED.priority_weight,
                       coverage_target = EXCLUDED.coverage_target""",
                (tier_num, info["name"], info["category"], info["weight"], info["target"]),
            )
            count += 1
    return count


def get_tier(tier_number: int) -> Optional[Dict[str, Any]]:
    with get_cursor() as cur:
        cur.execute(
            "SELECT * FROM klaus_acquisition_tiers WHERE tier_number = %s",
            (tier_number,),
        )
        row = cur.fetchone()
        return dict(row) if row else None


def list_tiers() -> List[Dict[str, Any]]:
    with get_cursor() as cur:
        cur.execute("SELECT * FROM klaus_acquisition_tiers ORDER BY tier_number")
        return [dict(r) for r in cur.fetchall()]


def set_document_tier(document_id: int, tier_number: int):
    """Set the acquisition tier for a document. Called by TierClassificationAgent."""
    with get_cursor() as cur:
        cur.execute(
            "UPDATE klaus_documents SET tier_id = "
            "(SELECT id FROM klaus_acquisition_tiers WHERE tier_number = %s) "
            "WHERE id = %s",
            (tier_number, document_id),
        )
        # Update tier counter
        cur.execute(
            """UPDATE klaus_acquisition_tiers
               SET acquisition_current = (
                   SELECT COUNT(*) FROM klaus_documents
                   WHERE tier_id = klaus_acquisition_tiers.id
               )
               WHERE tier_number = %s""",
            (tier_number,),
        )


def get_documents_by_tier(
    tier_number: int, limit: int = 100, offset: int = 0
) -> List[Dict[str, Any]]:
    with get_cursor() as cur:
        cur.execute(
            """SELECT d.* FROM klaus_documents d
               JOIN klaus_acquisition_tiers t ON d.tier_id = t.id
               WHERE t.tier_number = %s
               ORDER BY d.created_at DESC LIMIT %s OFFSET %s""",
            (tier_number, limit, offset),
        )
        return [dict(r) for r in cur.fetchall()]


def get_tier_coverage_stats() -> List[Dict[str, Any]]:
    """Return per-tier coverage statistics with counts and gap analysis."""
    with get_cursor() as cur:
        cur.execute(
            """SELECT
                   t.tier_number,
                   t.tier_name,
                   t.tier_category,
                   t.coverage_target,
                   t.acquisition_current,
                   t.status,
                   COUNT(d.id) as actual_count,
                   CASE
                       WHEN t.coverage_target > 0
                       THEN ROUND(COUNT(d.id)::numeric / t.coverage_target * 100, 1)
                       ELSE 0
                   END as coverage_pct
               FROM klaus_acquisition_tiers t
               LEFT JOIN klaus_documents d ON d.tier_id = t.id
               GROUP BY t.id, t.tier_number, t.tier_name, t.tier_category,
                        t.coverage_target, t.acquisition_current, t.status
               ORDER BY t.tier_number"""
        )
        return [dict(r) for r in cur.fetchall()]


def update_tier_acquisition_count(tier_number: int):
    """Recalculate acquisition_current for a given tier."""
    with get_cursor() as cur:
        cur.execute(
            """UPDATE klaus_acquisition_tiers t
               SET acquisition_current = (
                   SELECT COUNT(*) FROM klaus_documents d
                   WHERE d.tier_id = t.id
               ),
               last_acquired_at = CASE
                   WHEN (SELECT COUNT(*) FROM klaus_documents d WHERE d.tier_id = t.id) > 0
                   THEN CURRENT_TIMESTAMP
                   ELSE t.last_acquired_at
               END
               WHERE t.tier_number = %s""",
            (tier_number,),
        )


# ── Legal Authority Records ─────────────────────────────────────────────

def insert_authority_record(
    document_id: int,
    authority_type: str,
    citation_text: Optional[str] = None,
    neutral_citation: Optional[str] = None,
    court_identifier: Optional[str] = None,
    judge_names: Optional[List[str]] = None,
    parties: Optional[str] = None,
    case_number: Optional[str] = None,
    docket_number: Optional[str] = None,
    date_argued: Optional[str] = None,
    date_decided: Optional[str] = None,
    status: str = "current",
    ratio_decidendi: Optional[str] = None,
    obiter_dicta: Optional[str] = None,
    headnotes: Optional[str] = None,
    legislation_history: Optional[Dict[str, Any]] = None,
    amendment_chain: Optional[List[int]] = None,
    repeal_status: Optional[str] = None,
    gazette_number: Optional[str] = None,
    gazette_date: Optional[str] = None,
    consolidation_date: Optional[str] = None,
    authoritative_version: bool = False,
    language: str = "en",
    source_trust_level: str = "unverified",
) -> int:
    with get_cursor() as cur:
        cur.execute(
            """INSERT INTO klaus_legal_authority_records
               (document_id, authority_type, citation_text, neutral_citation,
                court_identifier, judge_names, parties, case_number, docket_number,
                date_argued, date_decided, status, ratio_decidendi, obiter_dicta,
                headnotes, legislation_history, amendment_chain, repeal_status,
                gazette_number, gazette_date, consolidation_date,
                authoritative_version, language, source_trust_level)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                       %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (document_id) DO UPDATE SET
                   authority_type = EXCLUDED.authority_type,
                   status = EXCLUDED.status,
                   updated_at = CURRENT_TIMESTAMP
               RETURNING id""",
            (
                document_id, authority_type, citation_text, neutral_citation,
                court_identifier, judge_names, parties, case_number, docket_number,
                date_argued, date_decided, status, ratio_decidendi, obiter_dicta,
                headnotes, Json(legislation_history) if legislation_history else None,
                amendment_chain, repeal_status,
                gazette_number, gazette_date, consolidation_date,
                authoritative_version, language, source_trust_level,
            ),
        )
        row = cur.fetchone()
        return row["id"]


def get_authority_record(document_id: int) -> Optional[Dict[str, Any]]:
    with get_cursor() as cur:
        cur.execute(
            "SELECT * FROM klaus_legal_authority_records WHERE document_id = %s",
            (document_id,),
        )
        row = cur.fetchone()
        return dict(row) if row else None


def update_authority_record(document_id: int, **fields) -> bool:
    """Update specific fields on a Legal Authority Record."""
    if not fields:
        return False
    allowed = {
        "authority_type", "citation_text", "neutral_citation", "court_identifier",
        "judge_names", "parties", "case_number", "docket_number",
        "date_argued", "date_decided", "status", "ratio_decidendi",
        "obiter_dicta", "headnotes", "legislation_history", "amendment_chain",
        "repeal_status", "gazette_number", "gazette_date", "consolidation_date",
        "authoritative_version", "language", "source_trust_level",
    }
    set_clauses = []
    params = []
    for key, value in fields.items():
        if key not in allowed:
            continue
        set_clauses.append(f"{key} = %s")
        if key == "legislation_history" and value is not None:
            params.append(Json(value))
        else:
            params.append(value)
    if not set_clauses:
        return False
    set_clauses.append("updated_at = CURRENT_TIMESTAMP")
    params.append(document_id)
    with get_cursor() as cur:
        cur.execute(
            f"UPDATE klaus_legal_authority_records SET {', '.join(set_clauses)} "
            f"WHERE document_id = %s",
            params,
        )
    return True


def list_authority_records(
    authority_type: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
) -> List[Dict[str, Any]]:
    clauses = []
    params = []
    if authority_type:
        clauses.append("a.authority_type = %s")
        params.append(authority_type)
    if status:
        clauses.append("a.status = %s")
        params.append(status)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    with get_cursor() as cur:
        cur.execute(
            f"""SELECT a.*, d.title, d.category
                FROM klaus_legal_authority_records a
                JOIN klaus_documents d ON a.document_id = d.id
                {where}
                ORDER BY a.created_at DESC LIMIT %s OFFSET %s""",
            params + [limit, offset],
        )
        return [dict(r) for r in cur.fetchall()]


def count_documents_by_tier() -> Dict[int, int]:
    """Return {tier_number: document_count} for all tiers."""
    with get_cursor() as cur:
        cur.execute(
            """SELECT t.tier_number, COUNT(d.id) as ct
               FROM klaus_acquisition_tiers t
               LEFT JOIN klaus_documents d ON d.tier_id = t.id
               GROUP BY t.tier_number
               ORDER BY t.tier_number"""
        )
        return {r["tier_number"]: r["ct"] for r in cur.fetchall()}
