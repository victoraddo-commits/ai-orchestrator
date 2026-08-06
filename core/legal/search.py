"""FTS5 search interface for legal documents."""

from __future__ import annotations
from typing import Optional
from core.legal.storage import LegalStorage


class LegalSearch:
    """FTS5-powered search over Ghana legal documents."""

    def __init__(self, storage: LegalStorage):
        self._storage = storage

    def full_text(self, query: str, limit: int = 20) -> list[dict]:
        return self._storage.search(query, limit=limit)

    def by_citation(self, citation: str) -> Optional[dict]:
        conn = self._storage.conn
        row = conn.execute(
            "SELECT * FROM documents WHERE citation=?", (citation,)).fetchone()
        if row is None:
            return None
        return self._storage.get_document(row["id"])

    def by_constitution(self, article: str) -> list[dict]:
        return self._storage.find_by_constitution_ref(article)

    def by_jurisdiction(self, jurisdiction: str, query: Optional[str] = None,
                        limit: int = 20) -> list[dict]:
        if query:
            conn = self._storage.conn
            rows = conn.execute(
                """SELECT d.*, snippet(fts_documents, 2, '<mark>', '</mark>', '', 32) AS snippet
                   FROM fts_documents
                   JOIN documents d ON d.id = fts_documents.rowid
                   WHERE fts_documents MATCH ? AND d.jurisdiction = ?
                   ORDER BY rank LIMIT ?""",
                (query, jurisdiction, limit)).fetchall()
            return [dict(r) for r in rows]
        return self._storage.list_documents(jurisdiction=jurisdiction, limit=limit)

    def by_status(self, status: str, limit: int = 50) -> list[dict]:
        return self._storage.list_documents(status=status, limit=limit)

    def by_year_range(self, start_year: int, end_year: int,
                      limit: int = 50) -> list[dict]:
        conn = self._storage.conn
        rows = conn.execute(
            """SELECT * FROM documents WHERE year BETWEEN ? AND ?
               ORDER BY year DESC, updated_at DESC LIMIT ?""",
            (start_year, end_year, limit)).fetchall()
        return [dict(r) for r in rows]
