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
    QueryRequest,
    QueryResult,
    SecondBrainRecord,
)

#: All store names in priority order (tier 1 = most authoritative)
ROUTER_STORE_NAMES: list[str] = list(STORE_MERGE_POLICIES.keys())

if TYPE_CHECKING:
    pass


class SecondBrainRouter:
    """Stateless query router for the Second Brain.

    Accepts either the stores parent directory (second_brain_root) or the
    stores directory itself (second_brain_root/stores/). Router always
    resolves to the stores directory at runtime.
    """

    def __init__(self, stores_base: str | None = None):
        from pathlib import Path
        if stores_base is None:
            self._root_parent = Path(__file__).parent
        else:
            self._root_parent = Path(stores_base)
        # Detect: is this already a stores dir or the parent?
        if (self._root_parent / "stores").exists():
            self.root = self._root_parent / "stores"
        elif self._root_parent.name == "stores":
            self.root = self._root_parent
        else:
            # Default: parent, expect stores/ subdir
            self.root = self._root_parent / "stores"
        self._store_cache: dict[str, _RouterStore] = {}

    @property
    def _stores(self) -> dict[str, _RouterStore]:
        """Backward-compatible alias for _store_cache (used by tests)."""
        return self._store_cache

    @_stores.setter
    def _stores(self, value: dict[str, _RouterStore]) -> None:
        """Backward-compatible setter for _store_cache (used by tests)."""
        self._store_cache = value

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
        # Handle wildcard string "*"
        if memory_types and any(str(mt) == "*" for mt in memory_types):
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

        def fetch_store(store_name: str) -> QueryResult:
            try:
                store = self._get_store(store_name)
                records = store.scan(
                    entity=request.entity,
                    memory_type=request.memory_types[0].value if request.memory_types else None,
                    time_range=(time_start, time_end) if (time_start or time_end) else None,
                    limit=request.limit,
                )
                merge_policy = store.MERGE_POLICY
            except Exception:
                records = []
                merge_policy = MergePolicy.NEWEST_WINS
            return QueryResult(
                records=records,
                store=store_name,
                merge_policy=merge_policy,
            )

        if not store_names:
            return {"records": [], "stores_queried": [], "total": 0}

        max_workers = max(1, len(store_names))
        query_results: list[QueryResult] = []
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            for qr in executor.map(fetch_store, store_names):
                query_results.append(qr)

        # Apply merge
        merged = self._merge(query_results)

        # Confidence enforcement: flag inferred records if require_confirmation
        flagged = []
        for rec in merged:
            rec_dict = rec.to_dict()
            if request.require_confirmation and rec.confidence == Confidence.INFERRED:
                rec_dict.setdefault("metadata", {}).setdefault("_flags", []).append("UNCONFIRMED_INFERENCE")
            flagged.append(rec_dict)

        return {
            "records": flagged,
            "stores_queried": store_names,
            "total": len(flagged),
        }

    def _merge(
        self,
        results: list[QueryResult],
        merge_policy_override: MergePolicy | None = None,
    ) -> list[SecondBrainRecord]:
        """Merge per-store results using each store's merge policy.

        Args:
            results: list of QueryResult objects (one per store)
            merge_policy_override: if set, apply this single policy to all stores
        """
        if not results:
            return []

        # If override is set, treat all as one flat list with the override policy
        if merge_policy_override is not None:
            all_records = [r for qr in results for r in qr.records]
            return self._apply_merge_policy(all_records, merge_policy_override)

        # Apply per-store merge policy to each store's records
        merged_by_store: list[SecondBrainRecord] = []
        for qr in results:
            merged_by_store.extend(self._apply_merge_policy(qr.records, qr.merge_policy))

        # For single-store case, deduplicate by id to handle edge cases
        if len(results) == 1:
            seen: set[str] = set()
            unique: list[SecondBrainRecord] = []
            for rec in merged_by_store:
                if rec.id not in seen:
                    seen.add(rec.id)
                    unique.append(rec)
            return unique

        # Multiple stores: cross-store newest_wins to deduplicate
        return self._apply_merge_policy(merged_by_store, MergePolicy.NEWEST_WINS)

    def _apply_merge_policy(
        self,
        records: list[SecondBrainRecord],
        policy: MergePolicy,
    ) -> list[SecondBrainRecord]:
        """Apply a specific merge policy to a list of records."""
        if not records:
            return []

        if policy == MergePolicy.UNION_ALL:
            # Keep all, deduplicate by id
            seen: set[str] = set()
            unique: list[SecondBrainRecord] = []
            for rec in records:
                if rec.id not in seen:
                    seen.add(rec.id)
                    unique.append(rec)
            return unique

        # NEWEST_WINS and SOURCE_AUTHORITY both group by entity
        by_entity: dict[str, list[SecondBrainRecord]] = {}
        for rec in records:
            # Use id as key for records with null entity to avoid collapsing them
            key = rec.entity if rec.entity else rec.id
            by_entity.setdefault(key, []).append(rec)

        results: list[SecondBrainRecord] = []
        for entity, recs in by_entity.items():
            winner: SecondBrainRecord | None = None
            if policy == MergePolicy.NEWEST_WINS:
                # Keep newest by timestamp
                recs.sort(key=lambda r: r.timestamp, reverse=True)
                winner = recs[0]
            elif policy == MergePolicy.SOURCE_AUTHORITY:
                # Keep lowest authority tier number (most authoritative)
                recs.sort(key=lambda r: r.source_authority.value)
                winner = recs[0]

            if winner:
                # Check if this winner is superseded by any other record in recs.
                # Build the supersedes chain: winner → winner.supersedes → ... → None
                superseded_ids: set[str] = set()
                current = winner.supersedes
                while current:
                    superseded_ids.add(current)
                    # Find the record with this id
                    parent = next((r for r in recs if r.id == current), None)
                    if parent:
                        current = parent.supersedes
                    else:
                        break
                # If any OTHER record in recs supersedes the winner, skip it
                other_ids = {r.id for r in recs if r.id != winner.id}
                if not (other_ids & superseded_ids):
                    results.append(winner)

        results.sort(key=lambda r: r.timestamp, reverse=True)
        return results

    def _apply_confirmation_flag(
        self,
        records: list[SecondBrainRecord],
        require_confirmation: bool = True,
    ) -> list[dict]:
        """Apply INFERRED confidence flag when require_confirmation is True.

        Returns list of record dicts with optional "_flags" metadata for
        INFERRED-confidence records.
        """
        flagged: list[dict] = []
        for rec in records:
            rec_dict = rec.to_dict()
            if require_confirmation and rec.confidence == Confidence.INFERRED:
                rec_dict.setdefault("metadata", {}).setdefault("_flags", []).append("UNCONFIRMED_INFERENCE")
            flagged.append(rec_dict)
        return flagged


class _RouterStore(AppendOnlyStore):
    """Store wrapper for the router with merge policy."""

    def __init__(self, name: str, store_dir, merge_policy):
        super().__init__(store_dir)
        self.STORE_NAME = name
        self.MERGE_POLICY = merge_policy
