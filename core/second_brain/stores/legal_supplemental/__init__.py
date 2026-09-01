"""Legal supplemental store — passthrough to core/legal_brain/.

This store does not write to the Second Brain. Queries for LEGAL domain
knowledge are routed here and delegated to core/legal_brain/.
Phase 5 does not modify core/legal_brain/ — returns empty results for now.
"""
from .store import LegalSupplementalStore

store = LegalSupplementalStore()

__all__ = ["store", "LegalSupplementalStore"]
