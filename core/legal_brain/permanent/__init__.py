"""Permanent Legal Brain — Immutable WORM document store.

Design:
  - SQLite with WAL mode for concurrent reads
  - INSERT-only for documents (no UPDATE after initial insert)
  - Hash-chain: each doc has content_hash + prev_hash
  - UUID primary keys (not auto-increment — avoids sequential guessing)
  - Separate from Kai's main database
  - Ghana-only jurisdiction enforcement
"""

import sqlite3
import os
import threading
from pathlib import Path
from contextlib import contextmanager
from typing import Optional, Dict, Any, List
from datetime import datetime, timezone

from ..config import PERMANENT_DB_PATH, PERMANENT_STORAGE_ROOT

_SCHEMA_SQL = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS sources (
    id TEXT PRIMARY KEY,
    url TEXT UNIQUE NOT NULL,
    domain TEXT NOT NULL,
    tier INTEGER CHECK (tier BETWEEN 1 AND 3),
    jurisdiction TEXT DEFAULT 'Ghana',
    reliability_score REAL DEFAULT 1.0,
    status TEXT DEFAULT 'active',
    last_checked TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS documents (
    id TEXT PRIMARY KEY,
    source_id TEXT REFERENCES sources(id),
    title TEXT NOT NULL,
    content_hash TEXT UNIQUE NOT NULL,
    prev_hash TEXT,
    file_path TEXT NOT NULL,
    category TEXT NOT NULL,
    jurisdiction TEXT NOT NULL DEFAULT 'Ghana',
    court TEXT,
    year INTEGER,
    citation_text TEXT,
    copyright_classification TEXT NOT NULL,
    access_level TEXT NOT NULL DEFAULT 'public',
    review_status TEXT NOT NULL DEFAULT 'pending',
    effective_date TEXT,
    page_count INTEGER DEFAULT 0,
    file_size_bytes INTEGER DEFAULT 0,
    version INTEGER DEFAULT 1,
    parent_doc_id TEXT REFERENCES documents(id),
    approved_by TEXT,
    ingested_at TEXT NOT NULL DEFAULT (datetime('now')),
    -- WORM enforcement: no UPDATE timestamp
    -- Versioning via parent_doc_id chain, not UPDATE
    CHECK (jurisdiction = 'Ghana')
);

CREATE TABLE IF NOT EXISTS chunks (
    id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    chunk_index INTEGER NOT NULL,
    content TEXT NOT NULL,
    content_hash TEXT,
    UNIQUE(document_id, chunk_index)
);

CREATE TABLE IF NOT EXISTS citations (
    id TEXT PRIMARY KEY,
    source_doc_id TEXT NOT NULL REFERENCES documents(id),
    target_doc_id TEXT REFERENCES documents(id),
    target_citation_text TEXT,
    citation_type TEXT NOT NULL DEFAULT 'references',
    context_snippet TEXT,
    confidence REAL DEFAULT 1.0,
    verified_by TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS audit_chain (
    id TEXT PRIMARY KEY,
    prev_entry_hash TEXT,
    event_type TEXT NOT NULL,
    doc_id TEXT REFERENCES documents(id),
    operator TEXT,
    details TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_docs_content_hash ON documents(content_hash);
CREATE INDEX IF NOT EXISTS idx_docs_review_status ON documents(review_status);
CREATE INDEX IF NOT EXISTS idx_docs_category ON documents(category);
CREATE INDEX IF NOT EXISTS idx_docs_source_id ON documents(source_id);
CREATE INDEX IF NOT EXISTS idx_chunks_doc_id ON chunks(document_id);
CREATE INDEX IF NOT EXISTS idx_citations_source ON citations(source_doc_id);
CREATE INDEX IF NOT EXISTS idx_citations_target ON citations(target_doc_id);
CREATE INDEX IF NOT EXISTS idx_audit_doc_id ON audit_chain(doc_id);
CREATE INDEX IF NOT EXISTS idx_audit_event_type ON audit_chain(event_type);
"""

_local = threading.local()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_db_path() -> Path:
    """Allow tests to override the DB path."""
    return PERMANENT_DB_PATH


@contextmanager
def get_connection(db_path: Optional[Path] = None):
    """Thread-local connection to the permanent store."""
    path = str(db_path or get_db_path())
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_permanent_store(db_path: Optional[Path] = None):
    """Initialize the permanent store schema."""
    path = str(db_path or get_db_path())
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    PERMANENT_STORAGE_ROOT.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.executescript(_SCHEMA_SQL)
    conn.commit()
    conn.close()
    return True
