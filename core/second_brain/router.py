"""SecondBrainRouter — single entry point for all Second Brain queries.

Query flow:
  1. Decompose query by entity, memory_types, time_range
  2. Filter stores to those that serve any of the requested memory types
  3. Fan out in parallel to each filtered store
  4. Merge results using each store's declared merge policy
  5. Inject confidence flags; never silently elevate inference

Result envelope:
  {
    "records": [...],
    "stores_queried": [...],
    "total": N,
  }
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING

from core.second_brain.base_store import AppendOnlyStore
from core.second_brain.registry import STORE_MERGE_POLICIES, STORE_TIERS
from core.second_brain.types import (
    Confidence,
    MEMORY_TYPE_STORE,
    MergePolicy,
    MemoryType,
    SecondBrainRecord,
    QueryRequest,
)

if TYPE_CHECKING:
    pass


class SecondBrainRouter:
    """Stateless query router for the Second Brain.

    Accepts either the stores parent directory (second_brain_root) or the
    stores directory itself (second_brain_root/stores/). Router always
    resolves to the stores directory at runtime.
    """

    def __init__(self, second_brain_root: str | None = None):
        from pathlib import Path
        if second_brain_root is None:
            self._root_parent = Path(__file__).parent
        else:
            self._root_parent = Path(second_brain_root)
        # Detect: is this already a stores dir or the parent?
        if (self._root_parent / "stores").exists():
            self.root = self._root_parent / "stores"
        elif self._root_parent.name == "stores":
            self.root = self._root_parent
        else:
            # Default: parent, expect stores/ subdir
            self.root = self._root_parent / "stores"
        self._store_cache: dict[str, _RouterStore] = {}
        self._store_cache: dict[str, _RouterStore] = {}

    def _get_store(self, store_name: str) -> _RouterStore:
        if store_name not in self._store_cache:
            store_dir = self.root / store_name
            policy = STORE_MERGE_POLICIES.get(store_name, MergePolicy.NEWEST_WINS)
            store = _RouterStore(store_name, store_dir, policy)
            store.ensure_exists()
            self._store_cache[store_name] = store
        return self._store_cache[store_name]

    def _stores_for_types(
        self, memory_types: list[MemoryType] | None
    ) -> list[str]:
        """Return list of unique store names that serve the given memory types."""
        if memory_types is None:
            return list(STORE_MERGE_POLICIES.keys())
        stores: set[str] = set()
        for mt in memory_types:
            store_name, _ = MEMORY_TYPE_STORE.get(mt, (None, None))
            if store_name:
                stores.add(store_name)
        return list(stores)

    def query(self, request: QueryRequest | dict) -> dict:
        """Execute a query and return a result envelope."""
        if isinstance(request, dict):
            request = QueryRequest(
                entity=request.get("entity"),
                memory_types=request.get("memory_types"),
                time_range=request.get("time_range"),
                require_confirmation=request.get("require_confirmation", False),
                limit=request.get("limit", 100),
            )

        store_names = self._stores_for_types(request.memory_types)
        time_range = request.time_range or {}
        time_start = time_range.get("start")
        time_end = time_range.get("end")

        # Fan out in parallel
        all_records: list[SecondBrainRecord] = []

        def fetch_store(store_name: str) -> list[SecondBrainRecord]:
            store = self._get_store(store_name)
            if request.entity:
                current = store.get_current(request.entity)
                if current:
                    return [current]
                return []
            return store.scan(
                entity=None,
                memory_types=[mt.value for mt in request.memory_types] if request.memory_types else None,
                time_start=time_start,
                time_end=time_end,
                limit=request.limit,
            )

        with ThreadPoolExecutor(max_workers=len(store_names)) as executor:
            results = executor.map(fetch_store, store_names)
            for records in results:
                all_records.extend(records)

        # Apply merge
        merged = self._merge(all_records)

        # Confidence enforcement: flag inferred records if require_confirmation
        flagged = []
        for rec in merged:
            rec_dict = rec.to_dict()
            if request.require_confirmation and rec.confidence == Confidence.INFERRED:
                rec_dict["_flags"] = rec_dict.get("_flags", []) + ["UNCONFIRMED_INFERENCE"]
            flagged.append(rec_dict)

        return {
            "records": flagged,
            "stores_queried": store_names,
            "total": len(flagged),
        }

    def _merge(self, records: list[SecondBrainRecord]) -> list[SecondBrainRecord]:
        """Apply newest_wins dedup: per entity keep newest timestamp, skip superseded."""
        if not records:
            return []
        # Group by entity for deduplication
        by_entity: dict[str, list[SecondBrainRecord]] = {}
        for rec in records:
            key = rec.entity or rec.id
            by_entity.setdefault(key, []).append(rec)

        results: list[SecondBrainRecord] = []
        for entity, recs in by_entity.items():
            # Sort by timestamp descending
            recs.sort(key=lambda r: r.timestamp, reverse=True)
            winner = recs[0]
            # Skip if superseded
            if winner.superseded_by:
                continue
            results.append(winner)

        results.sort(key=lambda r: r.timestamp, reverse=True)
        return results


class _RouterStore(AppendOnlyStore):
    """Store wrapper for the router with merge policy."""

    def __init__(self, name: str, store_dir, merge_policy):
        super().__init__(store_dir)
        self.STORE_NAME = name
        self.MERGE_POLICY = merge_policy
