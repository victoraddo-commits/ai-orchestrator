"""Store registry — maps store names to their tier, merge policy, and class."""
from __future__ import annotations

from core.second_brain.types import MergePolicy


# Store tier: source_authority value for records from this store
STORE_TIERS: dict[str, int] = {
    "operational": 5,
    "cognitive": 5,
    "conversational": 6,
    "project": 5,
    "relationship": 5,
    "legal_supplemental": 4,  # documentation-tier supplemental
}

# Store merge policy
STORE_MERGE_POLICIES: dict[str, MergePolicy] = {
    "operational": MergePolicy.NEWEST_WINS,
    "cognitive": MergePolicy.SOURCE_AUTHORITY,
    "conversational": MergePolicy.NEWEST_WINS,
    "project": MergePolicy.NEWEST_WINS,
    "relationship": MergePolicy.UNION_ALL,
    "legal_supplemental": MergePolicy.SOURCE_AUTHORITY,
}

# Manifest template per store
STORE_MANIFEST: dict[str, dict] = {
    name: {
        "schema_version": 1,
        "merge_policy": policy.value,
        "record_count": 0,
        "store_name": name,
    }
    for name, policy in STORE_MERGE_POLICIES.items()
}
