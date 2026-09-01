"""SecondBrainRouter — stateless query fan-out and result merging for the Second Brain."""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from core.second_brain.base_store import AppendOnlyStore
from core.second_brain.types import (
    MEMORY_TYPE_STORE,
    MergePolicy,
    MemoryType,
    QueryResult,
    SecondBrainRecord,
)

logger = logging.getLogger(__name__)

#: Stores managed by the router (legal_supplemental is passthrough — skipped in Phase 5).
ROUTER_STORE_NAMES = [
    "operational",
    "cognitive",
    "conversational",
    "project",
    "relationship",
]

#: Timeout for each store query in seconds.
_STORE_QUERY_TIMEOUT = 30


class SecondBrainRouter:
    """Single stateless entry point for all Second Brain queries.

    Fans out to the relevant stores in parallel and merges results using the
    store's declared merge policy (or an override).
    """

    def __init__(self, stores_base: str = "core/second_brain/stores") -> None:
        self.stores_base = stores_base
        self._stores: dict[str, AppendOnlyStore] = {}

    # --- public API ---

    def query(self, query_dict: dict[str, Any]) -> list[SecondBrainRecord]:
        """Decompose query, fan out to relevant stores in parallel, merge results.

        Args:
            query_dict: {
                "entity": str | None,
                "memory_types": list[MemoryType] | None,   # or "*" for all
                "time_range": {"start": ISO, "end": ISO} | None,  # dict, not tuple
                "require_confirmation": bool,
                "limit": int,
                "merge_policy_override": MergePolicy | None,
            }

        Returns:
            Merged list of SecondBrainRecord, newest-first.
        """
        entity: str | None = query_dict.get("entity")
        memory_types: list[MemoryType] | None = query_dict.get("memory_types")
        time_range: dict[str, str] | None = query_dict.get("time_range")
        require_confirmation: bool = query_dict.get("require_confirmation", False)
        limit: int = query_dict.get("limit", 100)
        merge_policy_override: MergePolicy | None = query_dict.get("merge_policy_override")

        # Determine which stores to query
        stores_to_query = self._stores_for_types(memory_types)

        # Convert time_range dict → tuple for store.scan()
        time_range_tuple: tuple[str, str] | None = None
        if time_range is not None:
            time_range_tuple = (time_range.get("start", ""), time_range.get("end", ""))

        # Fan out in parallel
        results: list[QueryResult] = []
        if not stores_to_query:
            return []
        with ThreadPoolExecutor(max_workers=len(stores_to_query)) as executor:
            futures = {
                executor.submit(
                    self._query_store,
                    store_name,
                    entity,
                    memory_types,
                    time_range_tuple,
                    limit,
                    merge_policy_override,
                ): store_name
                for store_name in stores_to_query
            }
            for future in as_completed(futures):
                store_name = futures[future]
                try:
                    result = future.result(timeout=_STORE_QUERY_TIMEOUT)
                    results.append(result)
                except Exception as exc:  # pragma: no cover — defensive
                    logger.warning("Store %s query failed: %s", store_name, exc)

        # Merge all results
        merged = self._merge(results, merge_policy_override)

        # Apply require_confirmation flagging
        if require_confirmation:
            merged = self._apply_confirmation_flag(merged)

        # Apply limit (approximate — each store returned up to limit, merge may add more)
        return merged[:limit]

    # --- internal helpers ---

    def _stores_for_types(
        self, memory_types: list[MemoryType] | None
    ) -> list[str]:
        """Map memory_type list → deduplicated list of relevant store names.

        Skips legal_supplemental (passthrough — core/legal_brain/ not modified in Phase 5).
        """
        if memory_types is None or "*" in memory_types:
            # Query all router-managed stores
            return [s for s in ROUTER_STORE_NAMES if s != "legal_supplemental"]

        store_names: list[str] = []
        seen: set[str] = set()
        for mt in memory_types:
            if mt not in MEMORY_TYPE_STORE:
                continue
            store_name, _ = MEMORY_TYPE_STORE[mt]
            if store_name == "legal_supplemental":
                continue  # Phase 5: skip passthrough store
            if store_name not in seen:
                seen.add(store_name)
                store_names.append(store_name)

        return store_names

    def _query_store(
        self,
        store_name: str,
        entity: str | None,
        memory_types: list[MemoryType] | None,
        time_range: tuple[str, str] | None,
        limit: int,
        merge_policy_override: MergePolicy | None,
    ) -> QueryResult:
        """Query a single store and return a QueryResult."""
        store = self._get_store(store_name)

        # Determine effective merge policy for this store
        if merge_policy_override is not None:
            effective_policy = merge_policy_override
        else:
            # Look up from STORE_MERGE_POLICIES via registry — imported lazily to avoid cycle
            from core.second_brain.registry import STORE_MERGE_POLICIES

            effective_policy = STORE_MERGE_POLICIES.get(store_name, MergePolicy.NEWEST_WINS)

        # Filter by memory_type if provided (otherwise scan all types in this store)
        mt_filter: MemoryType | None = None
        if memory_types and len(memory_types) == 1:
            mt_filter = memory_types[0]

        records = store.scan(
            memory_type=mt_filter,
            entity=entity,
            time_range=time_range,
            limit=limit,
        )

        return QueryResult(records=records, store=store_name, merge_policy=effective_policy)

    def _get_store(self, store_name: str) -> AppendOnlyStore:
        """Lazily instantiate and cache a store adapter."""
        if store_name not in self._stores:
            from core.second_brain.base_store import AppendOnlyStore

            store_dir = f"{self.stores_base}/{store_name}"
            self._stores[store_name] = AppendOnlyStore(store_dir)
        return self._stores[store_name]

    def _merge(
        self,
        results: list[QueryResult],
        merge_policy_override: MergePolicy | None,
    ) -> list[SecondBrainRecord]:
        """Apply merge policy to deduplicate and rank results.

        - NEWEST_WINS: keep record with latest timestamp per entity
        - SOURCE_AUTHORITY: keep record with lowest source_authority tier number
        - UNION_ALL: keep all records (deduplicate by id)
        """
        # Collect all records with their effective policy
        all_records: list[tuple[SecondBrainRecord, MergePolicy]] = []
        for result in results:
            policy = merge_policy_override if merge_policy_override else result.merge_policy
            for record in result.records:
                all_records.append((record, policy))

        if not all_records:
            return []

        # Group by entity (or by id for null-entity records so they don't collapse into one)
        by_entity: dict[str, list[tuple[SecondBrainRecord, MergePolicy]]] = {}
        for record, policy in all_records:
            if record.entity is None:
                # Null-entity records are keyed by id so they don't collapse into one
                key = f"__null_entity__{record.id}"
            else:
                key = record.entity
            by_entity.setdefault(key, []).append((record, policy))

        merged: list[SecondBrainRecord] = []

        for key, record_policy_pairs in by_entity.items():
            # Apply merge strategy per entity group
            if not record_policy_pairs:
                continue

            # Collect unique policies in this group (prefer override)
            effective_policy = merge_policy_override if merge_policy_override else record_policy_pairs[0][1]

            if effective_policy == MergePolicy.UNION_ALL:
                # Deduplicate by id
                seen_ids: set[str] = set()
                for record, _ in record_policy_pairs:
                    if record.id not in seen_ids:
                        seen_ids.add(record.id)
                        merged.append(record)

            elif effective_policy == MergePolicy.SOURCE_AUTHORITY:
                # Keep lowest tier number (lowest = highest authority)
                best: SecondBrainRecord | None = None
                for record, _ in record_policy_pairs:
                    if best is None or record.source_authority.value < best.source_authority.value:
                        best = record
                if best is not None:
                    merged.append(best)

            else:  # NEWEST_WINS (default)
                # Keep latest timestamp
                newest: SecondBrainRecord | None = None
                for record, _ in record_policy_pairs:
                    if newest is None or record.timestamp > newest.timestamp:
                        newest = record
                if newest is not None:
                    merged.append(newest)

        # Sort newest-first
        merged.sort(key=lambda r: r.timestamp, reverse=True)
        return merged

    def _apply_confirmation_flag(
        self, records: list[SecondBrainRecord]
    ) -> list[SecondBrainRecord]:
        """Add UNCONFIRMED_INFERENCE flag to INFERRED records that require confirmation."""
        from core.second_brain.types import Confidence

        flagged: list[SecondBrainRecord] = []
        for record in records:
            if record.confidence == Confidence.INFERRED:
                # Copy with flag
                metadata = dict(record.metadata)
                flags = list(metadata.get("_flags", []))
                if "UNCONFIRMED_INFERENCE" not in flags:
                    flags.append("UNCONFIRMED_INFERENCE")
                metadata["_flags"] = flags
                flagged_record = SecondBrainRecord(
                    **{**record.__dict__, "metadata": metadata}
                )
                flagged.append(flagged_record)
            else:
                flagged.append(record)
        return flagged
