"""Immutable SQLite storage with version control and audit trail.

Design:
- documents:         one row per document (current metadata)
- document_versions: append-only content snapshots (immutable)
- audit_log:         trigger-populated on every version insert
- fts_documents:     FTS5 virtual table for full-text search
- constitution_refs: cross-references to 1992 Constitution articles

All writes are atomic (WAL mode); document_versions is an append-only
ledger -- no UPDATE or DELETE, only INSERT. The latest version is always
at the highest version_number per document_id.
"""

from __future__ import annotations

import sqlite3
import textwrap
from pathlib import Path
from typing import Optional

# ── DDL ──────────────────────────────────────────────────────────

DDL = textwrap.dedent("""\
    PRAGMA journal_mode=WAL;
    PRAGMA foreign_keys=ON;

    CREATE TABLE IF NOT EXISTS documents (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        jurisdiction TEXT    NOT NULL,
        court        TEXT    NOT NULL,
        year         INTEGER NOT NULL,
        citation     TEXT    NOT NULL UNIQUE,
        judge        TEXT    NOT NULL DEFAULT '',
        parties      TEXT    NOT NULL DEFAULT '',
        status       TEXT    NOT NULL DEFAULT 'current'
            CHECK (status IN ('current','overruled','amended')),
        title        TEXT    NOT NULL DEFAULT '',
        date         TEXT    NOT NULL DEFAULT '',
        type         TEXT    NOT NULL DEFAULT '',
        source_url   TEXT    NOT NULL DEFAULT '',
        created_at   TEXT    NOT NULL DEFAULT (datetime('now')),
        updated_at   TEXT    NOT NULL DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS document_versions (
        id             INTEGER PRIMARY KEY AUTOINCREMENT,
        document_id    INTEGER NOT NULL REFERENCES documents(id),
        content        TEXT    NOT NULL,
        content_hash   TEXT    NOT NULL,
        version_number INTEGER NOT NULL,
        created_at     TEXT    NOT NULL DEFAULT (datetime('now')),
        UNIQUE(document_id, version_number)
    );

    CREATE TABLE IF NOT EXISTS audit_log (
        id                   INTEGER PRIMARY KEY AUTOINCREMENT,
        document_version_id  INTEGER NOT NULL REFERENCES document_versions(id),
        document_id          INTEGER NOT NULL REFERENCES documents(id),
        user_id              TEXT    NOT NULL DEFAULT 'system',
        action               TEXT    NOT NULL,
        timestamp            TEXT    NOT NULL DEFAULT (datetime('now'))
    );

    CREATE VIRTUAL TABLE IF NOT EXISTS fts_documents
        USING fts5(
            content,
            jurisdiction,
            court,
            year,
            citation,
            judge,
            parties,
            title
        );

    CREATE TABLE IF NOT EXISTS constitution_references (
        document_id  INTEGER NOT NULL REFERENCES documents(id),
        article      TEXT    NOT NULL,
        section      TEXT    NOT NULL DEFAULT '',
        PRIMARY KEY (document_id, article, section)
    );

    -- audit trigger
    CREATE TRIGGER IF NOT EXISTS trg_audit_version
    AFTER INSERT ON document_versions
    BEGIN
        INSERT INTO audit_log
            (document_version_id, document_id, user_id, action)
        VALUES
            (NEW.id, NEW.document_id, 'system', 'create');
    END;

    -- FTS sync trigger
    CREATE TRIGGER IF NOT EXISTS trg_fts_sync
    AFTER INSERT ON document_versions
    BEGIN
        INSERT OR REPLACE INTO fts_documents
            (rowid, content, jurisdiction, court, year,
             citation, judge, parties, title)
        SELECT
            d.id, NEW.content, d.jurisdiction, d.court,
            CAST(d.year AS TEXT), d.citation, d.judge, d.parties, d.title
        FROM documents d
        WHERE d.id = NEW.document_id;
    END;

    CREATE INDEX IF NOT EXISTS idx_versions_doc
        ON document_versions(document_id, version_number DESC);
    CREATE INDEX IF NOT EXISTS idx_audit_doc
        ON audit_log(document_id, timestamp DESC);
    CREATE INDEX IF NOT EXISTS idx_docs_citation
        ON documents(citation);
    CREATE INDEX IF NOT EXISTS idx_docs_status
        ON documents(status);
    CREATE INDEX IF NOT EXISTS idx_docs_jurisdiction
        ON documents(jurisdiction);
""")


class LegalStorage:
    """Manages the legal document SQLite database."""

    def __init__(self, db_path: str | Path = ":memory:"):
        self.db_path = str(db_path)
        self._conn: Optional[sqlite3.Connection] = None

    def connect(self) -> sqlite3.Connection:
        if self._conn is not None:
            return self._conn
        self._conn = sqlite3.connect(self.db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(DDL)
        return self._conn

    def close(self):
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("LegalStorage not connected. Call .connect() first.")
        return self._conn

    def insert_document(self, doc, content: str) -> int:
        import hashlib
        from core.legal.schema import LegalDocument

        cur = self.conn.execute(
            """INSERT INTO documents
               (jurisdiction, court, year, citation, judge, parties,
                status, title, date, type, source_url)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (doc.jurisdiction, doc.court, doc.year, doc.citation,
             doc.judge, doc.parties, doc.status,
             doc.title, doc.date, doc.type, doc.source_url),
        )
        doc_id = cur.lastrowid
        content_hash = hashlib.sha256(content.encode()).hexdigest()
        self.conn.execute(
            """INSERT INTO document_versions
               (document_id, content, content_hash, version_number)
               VALUES (?, ?, ?, 1)""",
            (doc_id, content, content_hash),
        )
        self.conn.commit()
        return doc_id

    def update_document(self, doc_id: int, updates: dict,
                        new_content: Optional[str] = None) -> bool:
        import hashlib

        if updates:
            set_clause = ", ".join(f"{k}=?" for k in updates)
            values = list(updates.values())
            self.conn.execute(
                f"UPDATE documents SET {set_clause}, updated_at=datetime('now') "
                f"WHERE id=?", values + [doc_id])

        if new_content is not None:
            row = self.conn.execute(
                "SELECT COALESCE(MAX(version_number),0)+1 FROM document_versions "
                "WHERE document_id=?", (doc_id,)).fetchone()
            next_ver = row[0]
            content_hash = hashlib.sha256(new_content.encode()).hexdigest()
            self.conn.execute(
                """INSERT INTO document_versions
                   (document_id, content, content_hash, version_number)
                   VALUES (?, ?, ?, ?)""",
                (doc_id, new_content, content_hash, next_ver))

        self.conn.commit()
        return self.conn.total_changes > 0

    def get_document(self, doc_id: int) -> Optional[dict]:
        row = self.conn.execute(
            "SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
        if row is None:
            return None
        doc = dict(row)
        ver = self.conn.execute(
            """SELECT content, content_hash, version_number, created_at
               FROM document_versions WHERE document_id=?
               ORDER BY version_number DESC LIMIT 1""", (doc_id,)).fetchone()
        if ver:
            doc.update({"content": ver["content"], "content_hash": ver["content_hash"],
                        "version_number": ver["version_number"],
                        "version_created_at": ver["created_at"]})
        return doc

    def get_version(self, doc_id: int, version_number: int) -> Optional[dict]:
        row = self.conn.execute(
            """SELECT * FROM document_versions
               WHERE document_id=? AND version_number=?""",
            (doc_id, version_number)).fetchone()
        return dict(row) if row else None

    def list_versions(self, doc_id: int) -> list[dict]:
        rows = self.conn.execute(
            """SELECT * FROM document_versions WHERE document_id=?
               ORDER BY version_number DESC""", (doc_id,)).fetchall()
        return [dict(r) for r in rows]

    def list_documents(self, jurisdiction=None, status=None,
                       limit=50, offset=0) -> list[dict]:
        where, params = [], []
        if jurisdiction:
            where.append("jurisdiction=?"); params.append(jurisdiction)
        if status:
            where.append("status=?"); params.append(status)
        sql = "SELECT * FROM documents"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY updated_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        return [dict(r) for r in self.conn.execute(sql, params).fetchall()]

    def get_audit_log(self, doc_id=None, limit=100) -> list[dict]:
        if doc_id is not None:
            rows = self.conn.execute(
                "SELECT * FROM audit_log WHERE document_id=? "
                "ORDER BY timestamp DESC LIMIT ?", (doc_id, limit)).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM audit_log ORDER BY timestamp DESC LIMIT ?",
                (limit,)).fetchall()
        return [dict(r) for r in rows]

    def search(self, query: str, limit: int = 20) -> list[dict]:
        rows = self.conn.execute(
            """SELECT d.*, snippet(fts_documents, 2, '<mark>', '</mark>', '', 32) AS snippet
               FROM fts_documents
               JOIN documents d ON d.id = fts_documents.rowid
               WHERE fts_documents MATCH ?
               ORDER BY rank LIMIT ?""",
            (query, limit)).fetchall()
        return [dict(r) for r in rows]

    def add_constitution_ref(self, doc_id: int, article: str, section: str = ""):
        self.conn.execute(
            """INSERT OR IGNORE INTO constitution_references
               (document_id, article, section) VALUES (?,?,?)""",
            (doc_id, article, section))
        self.conn.commit()

    def find_by_constitution_ref(self, article: str) -> list[dict]:
        rows = self.conn.execute(
            """SELECT d.* FROM documents d
               JOIN constitution_references cr ON d.id = cr.document_id
               WHERE cr.article = ? ORDER BY d.year DESC""",
            (article,)).fetchall()
        return [dict(r) for r in rows]

    def verify_integrity(self, doc_id: int) -> dict:
        import hashlib
        rows = self.conn.execute(
            "SELECT version_number, content, content_hash "
            "FROM document_versions WHERE document_id=? ORDER BY version_number",
            (doc_id,)).fetchall()
        results = []
        for r in rows:
            actual = hashlib.sha256(r["content"].encode()).hexdigest()
            results.append({"version": r["version_number"],
                           "hash_ok": actual == r["content_hash"],
                           "stored_hash": r["content_hash"],
                           "computed_hash": actual})
        all_ok = all(r["hash_ok"] for r in results)
        return {"document_id": doc_id, "all_versions_intact": all_ok,
                "versions": results}
