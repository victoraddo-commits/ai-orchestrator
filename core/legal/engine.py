"""Legal Analysis Engine — main orchestrator.

Usage:
    engine = LegalEngine("ghana_legal.db")
    engine.connect()
    doc_id = engine.ingest(doc, "Full text of the judgment...")
    results = engine.search("mensah constitutional")
"""

from __future__ import annotations
from pathlib import Path
from typing import Optional
from core.legal.schema import LegalDocument, validate_document
from core.legal.storage import LegalStorage
from core.legal.search import LegalSearch


class LegalEngine:
    """Orchestrator for the Ghana legal document platform."""

    def __init__(self, db_path: str | Path = ":memory:"):
        self._storage = LegalStorage(db_path)
        self._search = LegalSearch(self._storage)

    def connect(self):
        self._storage.connect()

    def close(self):
        self._storage.close()

    @property
    def storage(self) -> LegalStorage:
        return self._storage

    def ingest(self, doc: LegalDocument, content: str,
               constitution_refs: Optional[list[tuple[str, str]]] = None) -> int:
        errors = validate_document(doc)
        if errors:
            raise ValueError("; ".join(errors))
        doc_id = self._storage.insert_document(doc, content)
        if constitution_refs:
            for article, section in constitution_refs:
                self._storage.add_constitution_ref(doc_id, article, section)
        return doc_id

    def get(self, doc_id: int) -> Optional[dict]:
        return self._storage.get_document(doc_id)

    def update(self, doc_id: int, metadata: Optional[dict] = None,
               new_content: Optional[str] = None) -> bool:
        return self._storage.update_document(doc_id, metadata or {}, new_content)

    def get_version(self, doc_id: int, version_number: int) -> Optional[dict]:
        return self._storage.get_version(doc_id, version_number)

    def list_versions(self, doc_id: int) -> list[dict]:
        return self._storage.list_versions(doc_id)

    def search(self, query: str, limit: int = 20) -> list[dict]:
        return self._search.full_text(query, limit=limit)

    def search_by_constitution(self, article: str) -> list[dict]:
        return self._search.by_constitution(article)

    def search_by_jurisdiction(self, jurisdiction: str,
                                query: Optional[str] = None) -> list[dict]:
        return self._search.by_jurisdiction(jurisdiction, query)

    def list_documents(self, jurisdiction=None, status=None,
                       limit=50, offset=0) -> list[dict]:
        return self._storage.list_documents(
            jurisdiction=jurisdiction, status=status, limit=limit, offset=offset)

    def audit_log(self, doc_id=None, limit=100) -> list[dict]:
        return self._storage.get_audit_log(doc_id=doc_id, limit=limit)

    def verify_integrity(self, doc_id: int) -> dict:
        return self._storage.verify_integrity(doc_id)

    def add_constitution_ref(self, doc_id: int, article: str, section: str = ""):
        self._storage.add_constitution_ref(doc_id, article, section)

    def constitution_articles(self) -> list[str]:
        conn = self._storage.conn
        rows = conn.execute(
            "SELECT DISTINCT article FROM constitution_references ORDER BY article"
        ).fetchall()
        return [r["article"] for r in rows]
