"""Second Brain store registry — authority tiers, merge policies, and creation order."""
from __future__ import annotations

from core.second_brain.types import MergePolicy

#: Maps each store name to its authority tier (lower = higher authority).
STORE_TIERS: dict[str, int] = {
    "operational":          5,
    "cognitive":            5,
    "conversational":       6,
    "project":              5,
    "relationship":        5,
    "legal_supplemental":   4,
}

#: Maps each store name to its merge policy.
STORE_MERGE_POLICIES: dict[str, MergePolicy] = {
    "operational":          MergePolicy.NEWEST_WINS,
    "cognitive":            MergePolicy.SOURCE_AUTHORITY,
    "conversational":       MergePolicy.NEWEST_WINS,
    "project":              MergePolicy.NEWEST_WINS,
    "relationship":         MergePolicy.UNION_ALL,
    "legal_supplemental":   MergePolicy.SOURCE_AUTHORITY,
}

#: All registered store names in creation order (used to determine lookup order).
STORE_ORDER: list[str] = [
    "operational",
    "cognitive",
    "conversational",
    "project",
    "relationship",
    "legal_supplemental",
]
