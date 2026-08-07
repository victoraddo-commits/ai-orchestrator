"""Kai Legal Brain — Zero-Trust Legal Knowledge System.

Architecture:
  - permanent/ — Immutable WORM document store (SQLite, hash-chained)
  - workspace/ — Temporary user workspace (isolated, auto-destroyed)
  - knowledge/ — Knowledge graph + citation network

Service boundary:
  - Permanent and Temporary NEVER share storage, indexes, or processes
  - Communication ONLY through defined Python API (no cross-imports)
  - User uploads NEVER enter the permanent corpus
"""

__all__ = ["permanent", "workspace", "knowledge"]
