"""AppendOnlyStore — base class for all Second Brain stores.

Each store is a directory containing:
  records.jsonl       — append-only history (one JSON dict per line)
  current_index.json  — {entity: record_id} for O(1) current-state lookup
  manifest.json       — {schema_version, merge_policy, record_count}

Invariant: records.jsonl is never modified after append. current_index.json
is a cache of the latest record per entity, rebuilt from records.jsonl on demand.
"""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import TYPE_CHECKING

from core.second_brain.types import MergePolicy, SecondBrainRecord

if TYPE_CHECKING:
    pass


class AppendOnlyStore:
    """Base class for a single Second Brain store."""

    STORE_NAME: str = "base"
    MERGE_POLICY: MergePolicy = MergePolicy.NEWEST_WINS
    SCHEMA_VERSION: int = 1

    def __init__(self, store_dir: str | Path):
        self.store_dir = Path(store_dir)
        self.records_file = self.store_dir / "records.jsonl"
        self.index_file = self.store_dir / "current_index.json"
        self.manifest_file = self.store_dir / "manifest.json"

    # ── Initialization ────────────────────────────────────────────────────────

    def ensure_exists(self) -> None:
        """Create store directory and files if they don't exist."""
        self.store_dir.mkdir(parents=True, exist_ok=True)
        if not self.records_file.exists():
            self.records_file.touch()
        self._write_index({})
        self._write_manifest()

    # ── Index ─────────────────────────────────────────────────────────────────

    def _read_index(self) -> dict[str, str]:
        """Load entity → record_id mapping. Rebuilds from records.jsonl if corrupted."""
        if not self.index_file.exists():
            return {}
        try:
            with open(self.index_file) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return self._rebuild_index()

    def _write_index(self, index: dict[str, str]) -> None:
        """Atomic write of current-state index."""
        tmp = self.index_file.with_suffix(f".tmp.{os.getpid()}")
        with open(tmp, "w") as f:
            json.dump(index, f)
        shutil.move(str(tmp), str(self.index_file))

    def _rebuild_index(self) -> dict[str, str]:
        """Rebuild current_index from records.jsonl. Used after corruption."""
        index: dict[str, str] = {}
        if not self.records_file.exists():
            return index
        with open(self.records_file) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    entity = rec.get("entity")
                    if entity:
                        # latest record by timestamp wins for a given entity
                        existing = index.get(entity)
                        ts = rec.get("timestamp", "")
                        if existing is None or ts > existing.split(":", 1)[1]:
                            index[entity] = f"{rec['id']}:{ts}"
                except json.JSONDecodeError:
                    continue
        # Strip timestamps from stored values (just keep id)
        clean_index = {k: v.split(":")[0] for k, v in index.items()}
        self._write_index(clean_index)
        return clean_index

    # ── Manifest ─────────────────────────────────────────────────────────────

    def _read_manifest(self) -> dict:
        if not self.manifest_file.exists():
            return {"schema_version": self.SCHEMA_VERSION, "merge_policy": self.MERGE_POLICY.value, "record_count": 0}
        with open(self.manifest_file) as f:
            return json.load(f)

    def _write_manifest(self, record_count: int | None = None) -> None:
        manifest = self._read_manifest()
        manifest["schema_version"] = self.SCHEMA_VERSION
        manifest["merge_policy"] = self.MERGE_POLICY.value
        if record_count is not None:
            manifest["record_count"] = record_count
        with open(self.manifest_file, "w") as f:
            json.dump(manifest, f, indent=2)

    # ── Write ────────────────────────────────────────────────────────────────

    def append(self, record: SecondBrainRecord) -> str:
        """Append a record to the append-only file. Updates current index atomically.

        Returns the record ID.
        """
        self.ensure_exists()
        index = self._read_index()
        rec_dict = record.to_dict()

        # Write to append-only file
        with open(self.records_file, "a") as f:
            f.write(json.dumps(rec_dict, default=str) + "\n")

        # Update current index if this entity has a latest record
        entity = record.entity
        if entity:
            old_latest_id = index.get(entity)
            if old_latest_id:
                # Mark the old record as superseded in the file
                self._mark_superseded(old_latest_id, record.id)
            index[entity] = record.id

        self._write_index(index)

        # Update manifest count
        manifest = self._read_manifest()
        manifest["record_count"] = manifest.get("record_count", 0) + 1
        self._write_manifest()

        return record.id

    def _mark_superseded(self, old_record_id: str, new_record_id: str) -> None:
        """Update superseded_by on the old record in records.jsonl."""
        tmp_file = self.records_file.with_suffix(f".tmp.supersede.{os.getpid()}")
        found = False
        with open(self.records_file) as f_in, open(tmp_file, "w") as f_out:
            for line in f_in:
                line = line.strip()
                if not line:
                    f_out.write("\n")
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    f_out.write(line + "\n")
                    continue
                if rec.get("id") == old_record_id:
                    rec["superseded_by"] = new_record_id
                    found = True
                f_out.write(json.dumps(rec, default=str) + "\n")
        if found:
            shutil.move(str(tmp_file), str(self.records_file))
        else:
            tmp_file.unlink()

    # ── Read ─────────────────────────────────────────────────────────────────

    def get_current(self, entity: str) -> SecondBrainRecord | None:
        """Get the latest non-superseded record for an entity. O(1) via index."""
        index = self._read_index()
        record_id = index.get(entity)
        if not record_id:
            return None
        return self._get_by_id(record_id)

    def _get_by_id(self, record_id: str) -> SecondBrainRecord | None:
        """Find a record by ID. Scans records.jsonl."""
        if not self.records_file.exists():
            return None
        with open(self.records_file) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    if rec.get("id") == record_id:
                        return SecondBrainRecord.from_dict(rec)
                except json.JSONDecodeError:
                    continue
        return None

    def scan(
        self,
        entity: str | None = None,
        memory_types: list[str] | None = None,
        time_start: str | None = None,
        time_end: str | None = None,
        limit: int = 100,
    ) -> list[SecondBrainRecord]:
        """Scan records.jsonl with optional filters. Returns newest-first."""
        results: list[SecondBrainRecord] = []
        if not self.records_file.exists():
            return results
        with open(self.records_file) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = SecondBrainRecord.from_dict(json.loads(line))
                except (json.JSONDecodeError, TypeError, KeyError):
                    continue
                # Skip superseded records
                if rec.superseded_by:
                    continue
                # Entity filter
                if entity and rec.entity != entity:
                    continue
                # Memory type filter
                if memory_types and rec.memory_type.value not in memory_types:
                    continue
                # Time range filter
                if time_start and rec.timestamp < time_start:
                    continue
                if time_end and rec.timestamp > time_end:
                    continue
                results.append(rec)
        # Sort by timestamp descending (newest first)
        results.sort(key=lambda r: r.timestamp, reverse=True)
        return results[:limit]

    def history(self, entity: str) -> list[SecondBrainRecord]:
        """Return full supersedes chain for an entity, oldest first."""
        records: list[SecondBrainRecord] = []
        current = self.get_current(entity)
        while current:
            records.append(current)
            if current.supersedes:
                current = self._get_by_id(current.supersedes)
            else:
                current = None
        records.reverse()
        return records
