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

import os
import sqlite3
import textwrap
from pathlib import Path
from typing import Optional

# Snippet window for the LIKE fallback: ~120 chars of lead-in, 240 total.
_SNIPPET_LEAD = 120
_SNIPPET_WIDTH = 240


def _like_keyword_clause(keyword: str) -> tuple[str, list]:
    """SQL fragment + params matching one LIKE-fallback keyword.

    Alphabetic keywords keep the substring ``%kw%`` behaviour -- the fallback
    exists precisely for inflected forms FTS5 cannot hit ("offence" vs the
    indexed "offences"). A purely numeric keyword is matched with GLOB word
    boundaries instead, so ``24`` never matches ``243``, ``124`` or ``2024``
    inside a longer number. GLOB ``*`` spans any run of characters; the
    ``[^0-9]`` class asserts the digit boundary (a ``*`` is never a quantifier
    on the preceding class in GLOB).
    """
    if keyword.isdigit():
        patterns = [keyword, f"{keyword}[^0-9]*",
                    f"*[^0-9]{keyword}", f"*[^0-9]{keyword}[^0-9]*"]
        clauses, params = [], []
        for field in ("d.title", "v.content"):
            clauses.append(
                "(" + " OR ".join(f"{field} GLOB ?" for _ in patterns) + ")")
            params.extend(patterns)
        return "(" + " OR ".join(clauses) + ")", params
    return "(d.title LIKE ? OR v.content LIKE ?)", [f"%{keyword}%", f"%{keyword}%"]


def _match_snippet(content: str, keywords: list[str]) -> str:
    """Return a window of ``content`` centred on the first keyword hit.

    Falls back to the head of the content when no keyword occurs (e.g. a
    title-only LIKE hit), so the snippet is always plain text.
    """
    text = content or ""
    low = text.lower()
    pos = -1
    for kw in keywords:
        i = low.find(kw)
        if i != -1 and (pos == -1 or i < pos):
            pos = i
    if pos == -1:
        return text[:_SNIPPET_WIDTH]
    start = max(0, pos - _SNIPPET_LEAD)
    end = min(len(text), start + _SNIPPET_WIDTH)
    if end - start < _SNIPPET_WIDTH:  # near the tail: shift the window back
        start = max(0, end - _SNIPPET_WIDTH)
    return text[start:end]

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
        updated_at   TEXT    NOT NULL DEFAULT (datetime('now')),
        store_mode   TEXT    NOT NULL DEFAULT 'reference',
        rights_basis TEXT    NOT NULL DEFAULT 'unknown',
        content_available INTEGER NOT NULL DEFAULT 1
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

    -- Immutable record of rights/licence decisions and content downgrades.
    CREATE TABLE IF NOT EXISTS rights_audit (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        document_id   INTEGER NOT NULL,
        action        TEXT    NOT NULL,
        from_store_mode TEXT  NOT NULL DEFAULT '',
        to_store_mode TEXT    NOT NULL DEFAULT '',
        rights_basis  TEXT    NOT NULL DEFAULT 'unknown',
        rights_class  TEXT    NOT NULL DEFAULT 'unknown',
        chars_before  INTEGER NOT NULL DEFAULT 0,
        chars_after   INTEGER NOT NULL DEFAULT 0,
        reason        TEXT    NOT NULL DEFAULT '',
        rights_raw    TEXT    NOT NULL DEFAULT '',
        created_at    TEXT    NOT NULL DEFAULT (datetime('now'))
    );
    CREATE INDEX IF NOT EXISTS idx_rights_audit_doc
        ON rights_audit(document_id, created_at DESC);

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

    -- Dense embedding index (Legal Brain 2.0, Phase 1). One row per document
    -- chunk; ``embedding_documents`` records the doc-level state so a content
    -- change or a model/version change triggers a reindex while unchanged docs
    -- are skipped. Vectors are float32 BLOBs.
    CREATE TABLE IF NOT EXISTS embedding_documents (
        document_id  INTEGER NOT NULL REFERENCES documents(id),
        model        TEXT    NOT NULL,
        version      TEXT    NOT NULL,
        dim          INTEGER NOT NULL,
        content_hash TEXT    NOT NULL,
        chunk_count  INTEGER NOT NULL DEFAULT 0,
        indexed_at   TEXT    NOT NULL DEFAULT (datetime('now')),
        PRIMARY KEY (document_id, model, version)
    );

    CREATE TABLE IF NOT EXISTS document_embeddings (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        document_id  INTEGER NOT NULL REFERENCES documents(id),
        model        TEXT    NOT NULL,
        version      TEXT    NOT NULL,
        chunk_index  INTEGER NOT NULL,
        chunk_hash   TEXT    NOT NULL,
        char_start   INTEGER NOT NULL DEFAULT 0,
        char_end     INTEGER NOT NULL DEFAULT 0,
        dim          INTEGER NOT NULL,
        vector       BLOB    NOT NULL,
        created_at   TEXT    NOT NULL DEFAULT (datetime('now')),
        UNIQUE(document_id, model, version, chunk_index)
    );
    CREATE INDEX IF NOT EXISTS idx_emb_doc
        ON document_embeddings(document_id, model, version);
""")


class LegalStorage:
    """Manages the legal document SQLite database."""

    def __init__(self, db_path: str | Path = ":memory:"):
        self.db_path = str(db_path)
        self._conn: Optional[sqlite3.Connection] = None
        self._quarantine_col: Optional[bool] = None
        self._commercial_col: Optional[bool] = None

    def connect(self) -> sqlite3.Connection:
        if self._conn is not None:
            return self._conn
        self._conn = sqlite3.connect(self.db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(DDL)
        self._migrate(self._conn)
        return self._conn

    @staticmethod
    def _migrate(conn: sqlite3.Connection) -> None:
        """Idempotently add rights columns to databases created before the
        copyright/licence gate existed."""
        cols = {r[1] for r in conn.execute("PRAGMA table_info(documents)")}
        if "store_mode" not in cols:
            conn.execute("ALTER TABLE documents ADD COLUMN "
                         "store_mode TEXT NOT NULL DEFAULT 'reference'")
        if "rights_basis" not in cols:
            conn.execute("ALTER TABLE documents ADD COLUMN "
                         "rights_basis TEXT NOT NULL DEFAULT 'unknown'")
        if "content_available" not in cols:
            conn.execute("ALTER TABLE documents ADD COLUMN "
                         "content_available INTEGER NOT NULL DEFAULT 1")
            # Backfill from the immutable version ledger so legacy rows tell
            # the truth about whether they actually carry body text.
            conn.execute(
                """UPDATE documents SET content_available = CASE
                       WHEN EXISTS (
                           SELECT 1 FROM document_versions v
                           WHERE v.document_id = documents.id
                             AND TRIM(COALESCE(v.content,'')) <> ''
                       ) THEN 1 ELSE 0 END""")
        audit_cols = {r[1] for r in conn.execute("PRAGMA table_info(rights_audit)")}
        if "rights_raw" not in audit_cols:
            conn.execute("ALTER TABLE rights_audit ADD COLUMN "
                         "rights_raw TEXT NOT NULL DEFAULT ''")
        # Enriched metadata sidecar (Task 6). Idempotent CREATE IF NOT EXISTS;
        # imported lazily to keep the storage module free of import cycles.
        from core.legal.document_meta import ensure_meta_table
        ensure_meta_table(conn)
        conn.commit()

    def close(self):
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("LegalStorage not connected. Call .connect() first.")
        return self._conn

    def _has_quarantine_reason(self) -> bool:
        """Whether the remediation-added ``quarantine_reason`` column exists.

        Checked once and cached: the column is added by the remediation tool,
        so it is absent on fresh/older schemas and must not break queries.
        """
        if self._quarantine_col is None:
            cols = {r[1] for r in self.conn.execute("PRAGMA table_info(documents)")}
            self._quarantine_col = "quarantine_reason" in cols
        return self._quarantine_col

    def _has_commercial_col(self) -> bool:
        """Whether the ``document_meta.commercial_ok`` column exists.

        Checked once and cached: the column is added by the sidecar migration,
        so it is absent on very old schemas and must not break queries.
        """
        if self._commercial_col is None:
            try:
                cols = {r[1] for r in self.conn.execute(
                    "PRAGMA table_info(document_meta)")}
                self._commercial_col = "commercial_ok" in cols
            except sqlite3.Error:
                self._commercial_col = False
        return self._commercial_col

    def _visibility_sql(self, alias: str, commercial: bool = False) -> str:
        """WHERE fragment hiding non-ingestible, quarantined and (optionally)
        non-commercial documents.

        ``store_mode='none'`` is the rights gate's ceiling and must never
        surface; quarantined rows are hidden once remediation marks them. When
        ``commercial`` is set, only rows whose ``document_meta.commercial_ok``
        is exactly 1 pass -- NULL/absent/0 are excluded (fail-closed). If the
        column is missing the fragment is always false, so commercial mode can
        never leak untagged content.
        """
        parts = [f"{alias}.store_mode != 'none'"]
        if self._has_quarantine_reason():
            parts.append(f"({alias}.quarantine_reason IS NULL "
                         f"OR {alias}.quarantine_reason = '')")
        if commercial:
            if self._has_commercial_col():
                parts.append(
                    f"EXISTS (SELECT 1 FROM document_meta _cm "
                    f"WHERE _cm.document_id = {alias}.id "
                    f"AND _cm.commercial_ok = 1)")
            else:
                parts.append("0")
        return " AND ".join(parts)

    def insert_document(self, doc, content: str, store_mode: str = "reference",
                        rights_basis: str = "unknown",
                        content_available=None) -> int:
        import hashlib
        from core.legal.schema import LegalDocument

        available = bool((content or "").strip())
        if content_available is None:
            content_available = available
        # Invariant: never persist a "full" stub. A mode that promises the
        # complete text cannot be stored without any text; demote it to an
        # honest metadata-only record instead.
        if not available and store_mode in ("full", "reference"):
            store_mode = "search_only"
        content_available = 1 if content_available and available else 0

        cur = self.conn.execute(
            """INSERT INTO documents
               (jurisdiction, court, year, citation, judge, parties,
                status, title, date, type, source_url, store_mode, rights_basis,
                content_available)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (doc.jurisdiction, doc.court, doc.year, doc.citation,
             doc.judge, doc.parties, doc.status,
             doc.title, doc.date, doc.type, doc.source_url,
             store_mode, rights_basis, content_available),
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

    def record_rights_audit(self, doc_id: int, *, action: str,
                            from_store_mode: str = "", to_store_mode: str = "",
                            rights_basis: str = "unknown",
                            rights_class: str = "unknown",
                            chars_before: int = 0, chars_after: int = 0,
                            reason: str = "", rights_raw: str = "") -> int:
        """Append a rights_audit row *without* touching stored content.

        Used to record decisions that leave the content as-is (e.g. a
        metadata re-evaluation that keeps the conservative default).
        """
        cur = self.conn.execute(
            """INSERT INTO rights_audit
               (document_id, action, from_store_mode, to_store_mode,
                rights_basis, rights_class, chars_before, chars_after, reason,
                rights_raw)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (doc_id, action, from_store_mode, to_store_mode, rights_basis,
             rights_class, chars_before, chars_after, reason, rights_raw))
        self.conn.commit()
        return cur.lastrowid

    def set_rights_content(self, doc_id: int, content: str, *,
                           store_mode: str, rights_basis: str,
                           rights_class: str = "unknown", reason: str = "",
                           action: str = "downgrade",
                           rights_raw: str = "") -> bool:
        """Re-apply a rights decision to the latest version: replace its content
        with the permitted form (full/snippet/none), refresh the hash + FTS
        index, persist store_mode/rights_basis and record a rights_audit row.

        Used by remediation and re-classification; the original full text is
        removed, so this is the one sanctioned mutation of the append-only
        version ledger.
        """
        import hashlib

        row = self.conn.execute(
            """SELECT id, content FROM document_versions
               WHERE document_id=? ORDER BY version_number DESC LIMIT 1""",
            (doc_id,)).fetchone()
        if row is None:
            return False
        available = bool((content or "").strip())
        if not available and store_mode in ("full", "reference"):
            store_mode = "search_only"
        content = content or ""
        version_id = row["id"]
        chars_before = len(row["content"] or "")
        new_hash = hashlib.sha256(content.encode()).hexdigest()
        self.conn.execute(
            "UPDATE document_versions SET content=?, content_hash=? WHERE id=?",
            (content, new_hash, version_id))
        self.conn.execute(
            "UPDATE documents SET store_mode=?, rights_basis=?, "
            "content_available=?, updated_at=datetime('now') WHERE id=?",
            (store_mode, rights_basis, 1 if available else 0, doc_id))
        # FTS has no UPDATE path for rowid, so delete + reinsert from documents.
        self.conn.execute("DELETE FROM fts_documents WHERE rowid=?", (doc_id,))
        self.conn.execute(
            """INSERT INTO fts_documents
               (rowid, content, jurisdiction, court, year,
                citation, judge, parties, title)
               SELECT d.id, ?, d.jurisdiction, d.court, CAST(d.year AS TEXT),
                      d.citation, d.judge, d.parties, d.title
               FROM documents d WHERE d.id=?""",
            (content or "", doc_id))
        self.conn.execute(
            """INSERT INTO rights_audit
               (document_id, action, from_store_mode, to_store_mode,
                rights_basis, rights_class, chars_before, chars_after, reason,
                rights_raw)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (doc_id, action, "", store_mode, rights_basis, rights_class,
             chars_before, len(content or ""), reason, rights_raw))
        self.conn.commit()
        return True

    def rights_audit_log(self, doc_id: int = None, limit: int = 200) -> list[dict]:
        if doc_id is None:
            rows = self.conn.execute(
                "SELECT * FROM rights_audit ORDER BY id DESC LIMIT ?",
                (limit,)).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM rights_audit WHERE document_id=? "
                "ORDER BY id DESC LIMIT ?", (doc_id, limit)).fetchall()
        return [dict(r) for r in rows]

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
            self.conn.execute(
                "UPDATE documents SET content_available=? WHERE id=?",
                (1 if (new_content or "").strip() else 0, doc_id))

        self.conn.commit()
        return self.conn.total_changes > 0

    def get_document(self, doc_id: int, commercial: bool = False) -> Optional[dict]:
        row = self.conn.execute(
            f"SELECT * FROM documents WHERE id=? "
            f"AND {self._visibility_sql('documents', commercial=commercial)}",
            (doc_id,)).fetchone()
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
                       limit=50, offset=0, commercial=False) -> list[dict]:
        where = [self._visibility_sql("documents", commercial=commercial)]
        params = []
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

    def search(self, query: str, limit: int = 20, mode: str = "or",
               commercial: bool = False,
               with_snippet: bool = True) -> list[dict]:
        """FTS5 search with a normalized query and the CONTENT snippet.

        Never passes raw user text to MATCH (FTS5 operators raise syntax
        errors). ``mode`` selects how tokens are joined: ``"or"`` (recall),
        ``"and"`` (precision), ``"phrase"`` (exact words in order) -- so a
        caller can drive progressive retrieval without leaking operators.
        ``"like"`` bypasses FTS5 entirely for a substring keyword fallback.
        An unsupported ``mode`` raises ``ValueError`` (from
        ``build_match_query``). Snippet column 0 is ``content`` (2 was
        ``court``, a bug); the snippet is plain text with no highlighting
        markers.
        """
        if mode == "like":
            return self._search_like(query, limit, commercial=commercial)
        from core.legal.query_normalize import build_match_query
        expr = build_match_query(query, mode=mode)
        if not expr:
            return []
        # Snippet window (tokens). A 32-token window (~200 chars) is always
        # below Juris Kai's MIN_SOURCE_CHARS=400, so every full-tier hit
        # triggered an extra /document/{id} fetch that transferred the whole
        # body (up to ~830 KB) to use ~1.2 KB. A wider window lets full-tier
        # results be grounded from the snippet alone, removing the N+1 fetch.
        snippet_tokens = int(os.environ.get("KAI_LEGAL_SNIPPET_TOKENS", "190"))
        if with_snippet:
            rows = self.conn.execute(
                f"""SELECT d.*, snippet(fts_documents, 0, '', '', '…', ?) AS snippet,
                           bm25(fts_documents) AS _bm25
                   FROM fts_documents
                   JOIN documents d ON d.id = fts_documents.rowid
                   WHERE fts_documents MATCH ?
                     AND {self._visibility_sql('d', commercial=commercial)}
                   ORDER BY rank LIMIT ?""",
                (snippet_tokens, expr, limit)).fetchall()
        else:
            # Snippet-free over-fetch for rankers: snippet() costs ~6ms/row
            # over the full-copy content column (measured: 150 rows ~= 900ms),
            # and fusion discards almost every row of the over-fetch. The
            # caller refetches snippets only for surviving top hits via
            # :meth:`snippet_batch`.
            rows = self.conn.execute(
                f"""SELECT d.id, d.title, d.citation, d.type, d.year,
                           d.store_mode, bm25(fts_documents) AS _bm25
                   FROM fts_documents
                   JOIN documents d ON d.id = fts_documents.rowid
                   WHERE fts_documents MATCH ?
                     AND {self._visibility_sql('d', commercial=commercial)}
                   ORDER BY rank LIMIT ?""",
                (expr, limit)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            # Expose the FTS relevance signal. Previously it was computed for
            # ORDER BY but never returned, so the keyword path had no ranking
            # data at all — "director duties" could not prefer the Companies
            # Act over an unrelated Act. bm25() is lower-is-better; expose an
            # ordinal rank (1 = best) so callers share one convention with the
            # hybrid path's bm25_rank.
            bm = d.pop("_bm25", None)
            d["_bm25"] = bm
            out.append(d)
        # assign ordinal bm25_rank by ascending bm25 (most relevant first)
        ranked = sorted(out, key=lambda x: (x["_bm25"] if x["_bm25"] is not None else 9e9))
        for i, d in enumerate(ranked, 1):
            d["bm25_rank"] = i
        for d in ranked:
            d.pop("_bm25", None)
        return ranked

    def snippet_batch(self, query, doc_ids, mode: str = "or",
                      tokens=None) -> dict:
        """Snippets for a shortlist of documents, computed only for those rows.

        Fusion ranks over a wide over-fetch with snippets disabled, then asks
        for snippets of just the surviving top hits. FTS5 ``snippet()`` is
        computed per fetched row (~6ms/row over the full-content copy), so
        this keeps keyword-side latency bounded by what is *returned*, not
        what was scanned. Returns ``{doc_id: snippet}``.
        """
        from core.legal.query_normalize import build_match_query
        expr = build_match_query(query, mode=mode)
        if not expr or not doc_ids:
            return {}
        doc_ids = [int(d) for d in doc_ids if d is not None]
        if not doc_ids:
            return {}
        tokens = int(tokens or os.environ.get("KAI_LEGAL_SNIPPET_TOKENS", "190"))
        marks = ",".join("?" * len(doc_ids))
        rows = self.conn.execute(
            f"""SELECT fts_documents.rowid AS id,
                       snippet(fts_documents, 0, '', '', '…', ?) AS snippet
               FROM fts_documents
               JOIN documents d ON d.id = fts_documents.rowid
               WHERE fts_documents MATCH ?
                 AND fts_documents.rowid IN ({marks})
                 AND {self._visibility_sql('d', commercial=False)}""",
            (tokens, expr, *doc_ids)).fetchall()
        return {r["id"]: r["snippet"] for r in rows}

    def _search_like(self, query: str, limit: int,
                     commercial: bool = False) -> list[dict]:
        """LIKE keyword fallback over title/content -- no FTS5 involved.

        Used when FTS5 is unavailable or the caller wants substring matching
        (e.g. an inflected form the FTS tokenizer will not hit). Mirrors the
        legal-context LIKE fallback: OR each token across the title and the
        latest version's content, returning the same row shape as the FTS
        path (including ``snippet`` and ``store_mode``). The snippet is
        centred on the first content hit so the excerpt stays relevant.
        """
        from core.legal.query_normalize import tokens
        kws = tokens(query)
        if not kws:
            return []
        clauses, params = [], []
        for kw in kws:
            clause, kw_params = _like_keyword_clause(kw)
            clauses.append(clause)
            params.extend(kw_params)
        params.append(limit)
        rows = self.conn.execute(
            f"""SELECT d.*, v.content AS _content
                FROM documents d
                JOIN document_versions v ON v.document_id = d.id
                WHERE v.version_number = (
                    SELECT MAX(version_number) FROM document_versions
                    WHERE document_id = d.id)
                   AND {self._visibility_sql('d', commercial=commercial)}
                  AND ({' OR '.join(clauses)})
                ORDER BY d.updated_at DESC LIMIT ?""",
            params).fetchall()
        results = []
        for r in rows:
            row = dict(r)
            row["snippet"] = _match_snippet(row.pop("_content"), kws)
            results.append(row)
        return results

    def add_constitution_ref(self, doc_id: int, article: str, section: str = ""):
        self.conn.execute(
            """INSERT OR IGNORE INTO constitution_references
               (document_id, article, section) VALUES (?,?,?)""",
            (doc_id, article, section))
        self.conn.commit()

    def find_by_constitution_ref(self, article: str,
                                 commercial: bool = False) -> list[dict]:
        rows = self.conn.execute(
            f"""SELECT d.* FROM documents d
               JOIN constitution_references cr ON d.id = cr.document_id
               WHERE cr.article = ?
                 AND {self._visibility_sql('d', commercial=commercial)}
               ORDER BY d.year DESC""",
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

    # ── Dense embedding index (Phase 1) ──────────────────────────────────
    def embedding_candidates(self, store_modes=("full",)) -> list:
        """Rights-cleared documents eligible for embedding, oldest id first.

        Only rows that carry real body text (``content_available=1``) in an
        allowed ``store_mode`` (``full`` by default: the rights gate's ceiling
        for primary enactments/judgments) and pass the visibility filter are
        returned, with their latest version's ``content`` + ``content_hash``.
        """
        modes = tuple(store_modes) or ("full",)
        placeholders = ",".join("?" * len(modes))
        sql = (
            f"""SELECT d.id, d.type, d.store_mode, d.content_available,
                       v.content, v.content_hash
                FROM documents d
                JOIN document_versions v ON v.document_id = d.id
                WHERE v.version_number = (
                        SELECT MAX(version_number) FROM document_versions
                        WHERE document_id = d.id)
                  AND d.content_available = 1
                  AND d.store_mode IN ({placeholders})
                  AND TRIM(COALESCE(v.content,'')) <> ''
                  AND {self._visibility_sql('d')}
                ORDER BY d.id""")
        return self.conn.execute(sql, modes).fetchall()

    def get_embedding_document(self, doc_id: int, model: str,
                               version: str) -> Optional[dict]:
        row = self.conn.execute(
            "SELECT * FROM embedding_documents "
            "WHERE document_id=? AND model=? AND version=?",
            (doc_id, model, version)).fetchone()
        return dict(row) if row else None

    def replace_document_embeddings(self, doc_id: int, model: str, version: str,
                                    dim: int, content_hash: str,
                                    chunks: list) -> int:
        """Atomically replace one document's chunk vectors + state row.

        ``chunks`` items are ``{chunk_index, chunk_hash, char_start, char_end,
        vector}``. Replacing (rather than appending) makes a reindex after a
        content change safe and idempotent.
        """
        chunks = list(chunks)
        with self.conn:
            self.conn.execute(
                "DELETE FROM document_embeddings "
                "WHERE document_id=? AND model=? AND version=?",
                (doc_id, model, version))
            self.conn.executemany(
                """INSERT INTO document_embeddings
                   (document_id, model, version, chunk_index, chunk_hash,
                    char_start, char_end, dim, vector)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                [(doc_id, model, version, c["chunk_index"], c["chunk_hash"],
                  c["char_start"], c["char_end"], dim, c["vector"])
                 for c in chunks])
            self.conn.execute(
                """INSERT INTO embedding_documents
                   (document_id, model, version, dim, content_hash,
                    chunk_count, indexed_at)
                   VALUES (?,?,?,?,?,?,datetime('now'))
                   ON CONFLICT(document_id, model, version) DO UPDATE SET
                     dim=excluded.dim, content_hash=excluded.content_hash,
                     chunk_count=excluded.chunk_count,
                     indexed_at=excluded.indexed_at""",
                (doc_id, model, version, dim, content_hash, len(chunks)))
        return len(chunks)

    def iter_embeddings(self, model: str, version: str) -> list:
        """All chunk vectors for a model/version with their document metadata."""
        return self.conn.execute(
            """SELECT e.document_id, e.chunk_index, e.char_start, e.char_end,
                      e.vector, d.title, d.citation, d.type, d.year
               FROM document_embeddings e
               JOIN documents d ON d.id = e.document_id
               WHERE e.model=? AND e.version=?
               ORDER BY e.document_id, e.chunk_index""",
            (model, version)).fetchall()

    def delete_embeddings(self, model: Optional[str] = None,
                          version: Optional[str] = None) -> int:
        """Delete chunk vectors (and state rows) for a model/version; returns
        the number of chunks removed. Used by ``--reset``."""
        where, params = [], []
        if model:
            where.append("model=?"); params.append(model)
        if version:
            where.append("version=?"); params.append(version)
        clause = (" WHERE " + " AND ".join(where)) if where else ""
        with self.conn:
            cur = self.conn.execute(
                f"DELETE FROM document_embeddings{clause}", params)
            self.conn.execute(
                f"DELETE FROM embedding_documents{clause}", params)
        return cur.rowcount

    def embedding_chunk_count(self, model: Optional[str] = None,
                              version: Optional[str] = None) -> int:
        """Cheap chunk count for a model/version (ANN freshness check)."""
        where, params = [], []
        if model:
            where.append("model=?"); params.append(model)
        if version:
            where.append("version=?"); params.append(version)
        clause = (" WHERE " + " AND ".join(where)) if where else ""
        return int(self.conn.execute(
            f"SELECT COUNT(*) FROM document_embeddings{clause}",
            params).fetchone()[0])

    def embedding_stats(self, model: Optional[str] = None,
                        version: Optional[str] = None) -> dict:
        where, params = [], []
        if model:
            where.append("model=?"); params.append(model)
        if version:
            where.append("version=?"); params.append(version)
        clause = (" WHERE " + " AND ".join(where)) if where else ""
        docs = self.conn.execute(
            f"SELECT COUNT(*) FROM embedding_documents{clause}",
            params).fetchone()[0]
        chunks, nbytes = self.conn.execute(
            f"SELECT COUNT(*), COALESCE(SUM(LENGTH(vector)),0) "
            f"FROM document_embeddings{clause}", params).fetchone()
        dims = {r[0] for r in self.conn.execute(
            f"SELECT DISTINCT dim FROM embedding_documents{clause}",
            params).fetchall()}
        return {"documents": docs, "chunks": chunks, "bytes": nbytes,
                "dims": sorted(dims)}
