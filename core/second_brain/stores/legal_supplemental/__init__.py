"""Legal supplemental store — passthrough to core/legal_brain/ (source_authority)."""
from core.second_brain.base_store import AppendOnlyStore
from core.second_brain.types import MergePolicy

from core.second_brain.stores.legal_supplemental.store import LegalSupplementalStore

#: Passthrough store — queries delegate to core/legal_brain/
store: LegalSupplementalStore | None = None
try:
    store = LegalSupplementalStore()
except Exception:
    pass
