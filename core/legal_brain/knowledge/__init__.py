"""Phase 19E: Legal Knowledge Engine.

Builds a structured knowledge graph on top of the permanent Legal Brain:
  - Entity extraction from document metadata (courts, statutes, judges, principles)
  - Citation network with relationship types
  - Transitive closure queries (what does this citation ultimately depend on?)
  - Ghana-only Phase 1 scope
  - Version-aware: tracks amendments, repeals, judicial treatment
"""

from .engine import KnowledgeEngine, init_knowledge_store

__all__ = ["KnowledgeEngine", "init_knowledge_store"]
