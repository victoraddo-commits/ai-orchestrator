"""AppendOnlyStore — base class for all second brain stores."""
from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any

from core.second_brain.types import (
    MemoryType,
    SecondBrainRecord,
)


class AppendOnlyStore:
    """Base class for all second brain stores.

    Provides append-only JSONL storage with an eager entity->record_id index
    for O(1) current-state lookups, full history in the JSONL, and atomic
    index updates via rename-overwrite.
    """

    def __init__(self, store_dir: str) -> None:
        self.store_dir = store_dir
        self.records_file = os.path.join(store_dir, "records.jsonl")
        self.current_index_file = os.path.join(store_dir, "current_index.json")
        self.manifest_file = os.path.join(store_dir, "manifest.json")
        self.ensure_exists()

    # --- manifest ---

    def _read_manifest(self) -> dict[str, Any]:
        with open(self.manifest_file, "r", encoding="utf-8") as f:
            return json.load(f)

    def _write_manifest(self, manifest: dict[str, Any]) -> None:
        tmp = self.manifest_file + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(manifest, f)
        os.replace(tmp, self.manifest_file)

    # --- internal helpers ---

    def _write_index(self, index: dict[str, str]) -> None:
        """Atomic: write to temp + rename."""
        tmp = self.current_index_file + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(index, f)
        os.replace(tmp, self.current_index_file)

    def _read_index(self) -> dict[str, str]:
        """Read current_index.json, return entity->record_id mapping."""
        if not os.path.exists(self.current_index_file):
            return {}
        with open(self.current_index_file, "r", encoding="utf-8") as f:
            return json.load(f)

    def _rebuild_index(self) -> None:
        """Walk full JSONL, rebuild current_index from scratch."""
        index: dict[str, str] = {}
        if os.path.exists(self.records_file):
            with open(self.records_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    record = SecondBrainRecord.from_dict(json.loads(line))
                    if record.entity:
                        # Keep the record with the latest timestamp for each entity
                        existing_id = index.get(record.entity)
                        if existing_id is None:
                            index[record.entity] = record.id
                        else:
                            # Load existing record to compare timestamps
                            existing_record = self._load_record_by_id(existing_id)
                            if existing_record is None or record.timestamp > existing_record.timestamp:
                                index[record.entity] = record.id
        self._write_index(index)

    def _load_record_by_id(self, record_id: str) -> SecondBrainRecord | None:
        """Scan JSONL for a record with the given id."""
        if not os.path.exists(self.records_file):
            return None
        with open(self.records_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                record = SecondBrainRecord.from_dict(json.loads(line))
                if record.id == record_id:
                    return record
        return None

    # --- lifecycle ---

    def ensure_exists(self) -> None:
        """Create store dir + all required files if they don't exist. Idempotent."""
        os.makedirs(self.store_dir, exist_ok=True)

        if not os.path.exists(self.manifest_file):
            manifest = {"schema_version": 1, "record_count": 0, "merge_policy": "newest_wins"}
            self._write_manifest(manifest)

        if not os.path.exists(self.current_index_file):
            self._write_index({})

        if not os.path.exists(self.records_file):
            with open(self.records_file, "w", encoding="utf-8") as f:
                pass  # create empty file

    # --- core operations ---

    def append(self, record: SecondBrainRecord) -> None:
        """Append record to JSONL, update current_index atomically."""
        # Serialize record
        record_dict = record.to_dict()

        # Append to JSONL
        with open(self.records_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(record_dict) + "\n")

        # Update manifest count
        manifest = self._read_manifest()
        manifest["record_count"] = manifest.get("record_count", 0) + 1
        self._write_manifest(manifest)

        # Atomically update index: read -> modify -> write temp -> rename
        index = self._read_index()
        if not index and not os.path.exists(self.current_index_file):
            # Index missing entirely — rebuild from scratch
            self._rebuild_index()
            index = self._read_index()
        # Update entry for this entity (newer records replace older in the index)
        if record.entity:
            existing_id = index.get(record.entity)
            if existing_id is None:
                index[record.entity] = record.id
            else:
                existing_record = self._load_record_by_id(existing_id)
                if existing_record is None or record.timestamp >= existing_record.timestamp:
                    index[record.entity] = record.id
        self._write_index(index)

    def get_current(self, entity: str) -> SecondBrainRecord | None:
        """Look up entity in O(1) via current_index, return record or None."""
        index = self._read_index()
        record_id = index.get(entity)
        if record_id is None:
            return None
        return self._load_record_by_id(record_id)

    def scan(
        self,
        memory_type: MemoryType | None = None,
        entity: str | None = None,
        time_range: tuple[str, str] | None = None,
        limit: int = 100,
    ) -> list[SecondBrainRecord]:
        """Scan JSONL, filter by memory_type + entity + time_range, return newest-first."""
        results: list[SecondBrainRecord] = []
        start_iso, end_iso = time_range if time_range else (None, None)

        if not os.path.exists(self.records_file):
            return results

        with open(self.records_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                record = SecondBrainRecord.from_dict(json.loads(line))

                if memory_type is not None and record.memory_type != memory_type:
                    continue
                if entity is not None and record.entity != entity:
                    continue
                if start_iso is not None and record.timestamp < start_iso:
                    continue
                if end_iso is not None and record.timestamp > end_iso:
                    continue

                results.append(record)

        # Sort newest-first
        results.sort(key=lambda r: r.timestamp, reverse=True)
        return results[:limit]

    def history(self, entity: str) -> list[SecondBrainRecord]:
        """Walk all records for entity, return all versions oldest-first."""
        records: list[SecondBrainRecord] = []

        if not os.path.exists(self.records_file):
            return records

        with open(self.records_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                record = SecondBrainRecord.from_dict(json.loads(line))
                if record.entity == entity:
                    records.append(record)

        # Sort oldest-first by timestamp
        records.sort(key=lambda r: r.timestamp)
        return records

    def _mark_superseded(self, record_id: str, new_record_id: str) -> None:
        """No-op: JSONL is append-only; superseded_by is not updated in-place.

        The current_index is the source of truth for "current" state.
        The history() method walks all records by entity to build the supersedes chain.
        """
        # Intentionally a no-op: append-only design means we never modify old records.
        # Callers that need superseded traversal use history() which returns all versions.
        pass
