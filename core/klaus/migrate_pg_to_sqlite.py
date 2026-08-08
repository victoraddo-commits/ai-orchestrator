"""
One-shot migration: KLAUS PostgreSQL → Legal Brain SQLite WORM store.

Syncs sources, documents, and chunks from the acquisition pipeline's
operational PostgreSQL database into the permanent, append-only SQLite
database that Juris Kai queries at runtime.

ID remapping:
    PG klaus_sources.id       → SQLite sources.id      = "src-{pg_id}"
    PG klaus_documents.id     → SQLite documents.id     = "pg-{pg_id}"
    PG klaus_document_chunks.id → SQLite chunks.id      = "ch-{pg_id}"

Idempotent: skips docs whose content_hash already exists in SQLite.
Run as:  .venv/bin/python -m core.klaus.migrate_pg_to_sqlite
"""

import hashlib
import logging
import os
import sqlite3
import uuid
from datetime import datetime, timezone

import psycopg2
from psycopg2.extras import RealDictCursor

logger = logging.getLogger("klaus.migrate")
logging.basicConfig(level=logging.INFO, format="%(levelname)s [%(name)s] %(message)s")

# ── Connection config ─────────────────────────────────────────────────────

PG_CONFIG = {
    "dbname": os.getenv("KLAUS_DB_NAME", "klaus_db"),
    "user": os.getenv("KLAUS_DB_USER", "klaus_user"),
    "password": os.getenv("KLAUS_DB_PASSWORD", "klaus_password"),
    "host": os.getenv("KLAUS_DB_HOST", "localhost"),
}

# Dedicated path — same as legal-brain config, NOT a shared connection
LEGAL_BRAIN_DB = os.getenv(
    "LEGAL_BRAIN_DB",
    "/var/lib/ai-orchestrator/legal_brain/permanent/legal_brain.db",
)


# ── Migration ──────────────────────────────────────────────────────────────

def migrate(dry_run: bool = False) -> dict:
    """Run the full migration. Returns a summary dict."""
    started = datetime.now(timezone.utc).isoformat()

    pg = psycopg2.connect(**PG_CONFIG)
    pg.autocommit = False
    sl = sqlite3.connect(f"file:{LEGAL_BRAIN_DB}", uri=True)
    sl.row_factory = sqlite3.Row
    sl.execute("PRAGMA journal_mode=WAL")
    sl.execute("PRAGMA foreign_keys=ON")
    sl.execute(
        "CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts "
        "USING fts5(chunk_id, content, tokenize='porter unicode61')"
    )

    summary = {
        "started": started,
        "sources": {"in_pg": 0, "inserted": 0, "skipped": 0},
        "documents": {"in_pg": 0, "inserted": 0, "skipped": 0},
        "chunks": {"in_pg": 0, "inserted": 0, "skipped": 0},
        "errors": [],
    }

    try:
        # ── Step 1: Sources ──────────────────────────────────────────────
        with pg.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT id, url, domain, tier, jurisdiction, reliability_score, "
                "       last_validated_at, status "
                "FROM klaus_sources WHERE status = 'active'"
            )
            pg_sources = cur.fetchall()

        summary["sources"]["in_pg"] = len(pg_sources)

        for src in pg_sources:
            src_id = f"src-{src['id']}"
            try:
                # Check if source already exists in SQLite
                existing = sl.execute(
                    "SELECT id FROM sources WHERE url = ?", (src["url"],)
                ).fetchone()
                if existing:
                    summary["sources"]["skipped"] += 1
                    continue

                sl.execute(
                    """INSERT INTO sources (id, url, domain, tier, jurisdiction,
                       reliability_score, status, last_checked, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        src_id,
                        src["url"],
                        src["domain"],
                        src["tier"],
                        src.get("jurisdiction", "Ghana"),
                        src.get("reliability_score", 1.0),
                        src.get("status", "active"),
                        str(src.get("last_validated_at") or ""),
                        started,
                    ),
                )
                summary["sources"]["inserted"] += 1
            except Exception as e:
                summary["errors"].append(f"source {src_id}: {e}")

        if not dry_run:
            sl.commit()
        logger.info(
            "Sources: %d inserted, %d skipped",
            summary["sources"]["inserted"],
            summary["sources"]["skipped"],
        )

        # ── Step 2: Documents ────────────────────────────────────────────
        with pg.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """SELECT d.id, d.source_id, d.title, d.file_hash, d.file_path,
                          d.category, d.jurisdiction, d.court, d.year,
                          d.legislation_number, d.copyright_classification,
                          d.access_level, d.review_status, d.effective_date,
                          d.version, d.parent_document_id, d.created_at,
                          ar.citation_text
                   FROM klaus_documents d
                   LEFT JOIN klaus_legal_authority_records ar
                     ON ar.document_id = d.id
                   WHERE d.review_status = 'approved'
                     AND d.access_level = 'full_storage'
                   ORDER BY d.id"""
            )
            pg_docs = cur.fetchall()

        summary["documents"]["in_pg"] = len(pg_docs)

        for doc in pg_docs:
            doc_id = f"pg-{doc['id']}"
            try:
                # Check by content hash (idempotent)
                existing = sl.execute(
                    "SELECT id FROM documents WHERE content_hash = ?",
                    (doc["file_hash"],),
                ).fetchone()
                if existing:
                    summary["documents"]["skipped"] += 1
                    continue

                source_id = f"src-{doc['source_id']}" if doc["source_id"] else None
                parent_id = f"pg-{doc['parent_document_id']}" if doc.get("parent_document_id") else None

                # Build citation_text from legislation_number if no authority record
                citation = doc.get("citation_text")
                if not citation and doc.get("legislation_number"):
                    citation = doc["legislation_number"]
                if not citation and doc.get("file_hash"):
                    citation = doc["file_hash"][:12]

                # Handle file_path — PG stores the local path; in WORM we
                # record the original path as-is.
                file_path = doc.get("file_path") or f"pg-{doc['id']}/{doc['title']}"

                effective_date = None
                if doc.get("effective_date"):
                    effective_date = str(doc["effective_date"])

                created_at = None
                if doc.get("created_at"):
                    created_at = str(doc["created_at"])

                sl.execute(
                    """INSERT INTO documents
                       (id, source_id, title, content_hash, file_path, category,
                        jurisdiction, court, year, citation_text,
                        copyright_classification, access_level, review_status,
                        effective_date, page_count, file_size_bytes, version,
                        parent_doc_id, approved_by, ingested_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        doc_id,
                        source_id,
                        doc["title"],
                        doc.get("file_hash", "unknown"),
                        file_path,
                        doc.get("category", "uncategorized"),
                        doc.get("jurisdiction", "Ghana"),
                        doc.get("court"),
                        doc.get("year"),
                        citation,
                        doc.get("copyright_classification", "unknown"),
                        doc.get("access_level", "full_storage"),
                        doc.get("review_status", "approved"),
                        effective_date,
                        doc.get("page_count") or 0,
                        0,  # file_size_bytes — unknown from PG
                        doc.get("version", 1),
                        parent_id,
                        "migration-sync",
                        created_at or started,
                    ),
                )
                summary["documents"]["inserted"] += 1
            except Exception as e:
                summary["errors"].append(f"document {doc_id}: {e}")

        if not dry_run:
            sl.commit()
        logger.info(
            "Documents: %d inserted, %d skipped",
            summary["documents"]["inserted"],
            summary["documents"]["skipped"],
        )

        # ── Step 3: Chunks ───────────────────────────────────────────────
        with pg.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """SELECT c.id, c.document_id, c.chunk_index, c.content
                   FROM klaus_document_chunks c
                   JOIN klaus_documents d ON d.id = c.document_id
                   WHERE d.review_status = 'approved'
                     AND d.access_level = 'full_storage'
                   ORDER BY c.document_id, c.chunk_index"""
            )
            pg_chunks = cur.fetchall()

        summary["chunks"]["in_pg"] = len(pg_chunks)

        for chunk in pg_chunks:
            chunk_id = f"ch-{chunk['id']}"
            doc_id = f"pg-{chunk['document_id']}"
            try:
                # Only insert if the parent document was successfully created
                parent_exists = sl.execute(
                    "SELECT id FROM documents WHERE id = ?", (doc_id,)
                ).fetchone()
                if not parent_exists:
                    continue

                # Check for existing chunk at same position
                existing = sl.execute(
                    "SELECT id FROM chunks WHERE document_id = ? AND chunk_index = ?",
                    (doc_id, chunk["chunk_index"]),
                ).fetchone()
                if existing:
                    summary["chunks"]["skipped"] += 1
                    continue

                content = chunk["content"] or ""
                content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest() if content else None

                sl.execute(
                    """INSERT INTO chunks (id, document_id, chunk_index, content, content_hash)
                       VALUES (?, ?, ?, ?, ?)""",
                    (chunk_id, doc_id, chunk["chunk_index"], content, content_hash),
                )
                # Also index into FTS5 for full-text search
                sl.execute(
                    "INSERT OR REPLACE INTO chunks_fts(chunk_id, content) VALUES (?, ?)",
                    (chunk_id, content),
                )
                summary["chunks"]["inserted"] += 1
            except Exception as e:
                summary["errors"].append(f"chunk {chunk_id}: {e}")

        if not dry_run:
            sl.commit()
        logger.info(
            "Chunks: %d inserted, %d skipped",
            summary["chunks"]["inserted"],
            summary["chunks"]["skipped"],
        )

        # ── Final verification ───────────────────────────────────────────
        final = sl.execute("SELECT COUNT(*) as c FROM documents").fetchone()
        final_chunks = sl.execute("SELECT COUNT(*) as c FROM chunks").fetchone()
        sources_final = sl.execute("SELECT COUNT(*) as c FROM sources").fetchone()

        summary["final_state"] = {
            "sources": sources_final["c"] if sources_final else 0,
            "documents": final["c"] if final else 0,
            "chunks": final_chunks["c"] if final_chunks else 0,
        }

        if not dry_run:
            # Record migration in audit chain
            import uuid as _uuid
            audit_id = str(_uuid.uuid4())
            sl.execute(
                """INSERT INTO audit_chain (id, event_type, operator, details, created_at)
                   VALUES (?, 'migration', 'klaus_sync', ?, ?)""",
                (
                    audit_id,
                    f"Synced from PostgreSQL: {summary['sources']['inserted']} sources, "
                    f"{summary['documents']['inserted']} docs, "
                    f"{summary['chunks']['inserted']} chunks",
                    started,
                ),
            )
            sl.commit()

            # Rebuild FTS5 index to pick up any existing chunks
            # that were skipped (already present) but missing from FTS
            sl.execute("INSERT INTO chunks_fts(chunks_fts) VALUES('rebuild')")
            sl.commit()
            fts_count = sl.execute("SELECT COUNT(*) as c FROM chunks_fts").fetchone()
            summary["fts_doc_count"] = fts_count["c"] if fts_count else 0

    except Exception as e:
        logger.error("Migration failed: %s", e)
        sl.rollback()
        summary["errors"].append(f"fatal: {e}")
    finally:
        pg.close()
        sl.close()

    return summary


# ── CLI ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    dry_run = "--dry-run" in sys.argv
    if dry_run:
        print("DRY RUN — no writes will be committed")

    result = migrate(dry_run=dry_run)
    print()
    print("═" * 60)
    print("KLAUS PostgreSQL → Legal Brain SQLite Migration Report")
    print("═" * 60)
    print(f"Sources:   {result['sources']['in_pg']} in PG → "
          f"{result['sources']['inserted']} inserted, {result['sources']['skipped']} skipped")
    print(f"Documents: {result['documents']['in_pg']} in PG → "
          f"{result['documents']['inserted']} inserted, {result['documents']['skipped']} skipped")
    print(f"Chunks:    {result['chunks']['in_pg']} in PG → "
          f"{result['chunks']['inserted']} inserted, {result['chunks']['skipped']} skipped")
    if "final_state" in result:
        fs = result["final_state"]
        print(f"\nLegal Brain SQLite now: {fs['sources']} sources, "
              f"{fs['documents']} documents, {fs['chunks']} chunks")
    if result["errors"]:
        print(f"\n{len(result['errors'])} errors:")
        for err in result["errors"]:
            print(f"  ⚠️  {err}")
    print("═" * 60)
