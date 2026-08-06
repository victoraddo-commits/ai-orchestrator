"""Legal Analysis Engine — 17O-C.

Metadata schema, version control, immutable storage with audit trail for
Ghana legal documents (parliamentary proceedings, judicial opinions, gazette
notices).

Core components:
- schema:  dataclass models for document metadata + validation
- storage: SQLite engine with immutable version tracking + audit triggers
- search:  FTS5 full-text search over document text + metadata
- engine:  Orchestrator tying schema/storage/search together
"""

from core.legal.engine import LegalEngine

__all__ = ["LegalEngine"]
