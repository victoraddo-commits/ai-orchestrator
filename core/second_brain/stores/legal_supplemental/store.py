"""Legal supplemental store — passthrough to core/legal_brain/.

Interface matches AppendOnlyStore for transparent integration with the router,
but queries delegate to core/legal_brain/ instead of reading local files.
"""
from core.second_brain.types import MergePolicy, SecondBrainRecord


class LegalSupplementalStore:
    """Passthrough store for legal domain queries.

    Implements the same interface as AppendOnlyStore for router compatibility.
    In Phase 5, always returns empty results (core/legal_brain not wired yet).
    """

    STORE_NAME = "legal_supplemental"
    MERGE_POLICY = MergePolicy.SOURCE_AUTHORITY

    def scan(
        self,
        memory_type=None,
        entity=None,
        time_range=None,
        limit=100,
    ) -> list[SecondBrainRecord]:
        """Query core/legal_brain/ for legal domain knowledge.

        Returns empty list until core/legal_brain integration is implemented.
        """
        # TODO: Wire to core/legal_brain/ query interface
        return []

    def get_current(self, entity: str) -> SecondBrainRecord | None:
        """Get latest legal knowledge for entity."""
        # TODO: Wire to core/legal_brain/
        return None

    def history(self, entity: str) -> list[SecondBrainRecord]:
        """Get full history for entity from legal brain."""
        # TODO: Wire to core/legal_brain/
        return []
