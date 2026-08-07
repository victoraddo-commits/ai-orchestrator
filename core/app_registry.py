"""Application Registry storage layer.

Atomic read-modify-write on memory/app_registry.json, with fcntl.flock
for multi-process safety, schema-versioned files, and migrations.

Phase 19R: Kai Software Factory: Application Registry.
"""

import fcntl
import json
import logging
import os
import shutil
from pathlib import Path
from typing import Callable, Optional

from core.app_registry_models import (
    AppRecord,
    AppCreate,
    AppUpdate,
    ChangeRecord,
    RegistryFile,
    RegistryStatus,
    _now_iso,
)
from core.id_generator import generate_id

logger = logging.getLogger(__name__)

CURRENT_SCHEMA_VERSION = 1
REGISTRY_FILENAME = "app_registry.json"

_event_hooks: dict[str, list[Callable]] = {
    "on_create": [],
    "on_update": [],
    "on_status_change": [],
    "on_delete": [],
}


def register_hook(event: str, callback: Callable):
    if event in _event_hooks:
        _event_hooks[event].append(callback)


def _fire_hooks(event: str, record: AppRecord, **extra):
    for callback in _event_hooks.get(event, []):
        try:
            callback(record, **extra)
        except Exception:
            logger.exception("registry: hook %s failed for app %s", event, record.id)


def _default_memory_dir() -> Path:
    override = os.environ.get("AI_ORCHESTRATOR_MEMORY_DIR")
    return Path(override) if override else Path("memory")


def _registry_path() -> Path:
    return _default_memory_dir() / REGISTRY_FILENAME


def _lock_path(path: Path) -> Path:
    return path.with_suffix(path.suffix + ".lock")


def _backup_path(path: Path) -> Path:
    return path.with_suffix(path.suffix + ".bak")


def _tmp_path(path: Path) -> Path:
    return path.with_suffix(path.suffix + f".tmp.{os.getpid()}")


MIGRATIONS = {}


def _load_registry_file(path: Path) -> RegistryFile:

    if not path.exists():
        return RegistryFile(schema_version=CURRENT_SCHEMA_VERSION, records=[])

    try:
        with open(path, "r") as f:
            raw = json.load(f)
    except (json.JSONDecodeError, OSError):
        backup = _backup_path(path)
        if backup.exists():
            try:
                with open(backup, "r") as f:
                    raw = json.load(f)
            except (json.JSONDecodeError, OSError):
                return RegistryFile(schema_version=CURRENT_SCHEMA_VERSION, records=[])
        else:
            return RegistryFile(schema_version=CURRENT_SCHEMA_VERSION, records=[])

    file_version = raw.get("schema_version", 0)
    records_raw = raw.get("records", [])

    while file_version < CURRENT_SCHEMA_VERSION:
        migrator = MIGRATIONS.get(file_version)
        if migrator is None:
            break
        records_raw = migrator(records_raw)
        file_version += 1

    records = [AppRecord(**r) for r in records_raw]
    return RegistryFile(schema_version=CURRENT_SCHEMA_VERSION, records=records)


def _save_registry_file(registry: RegistryFile, path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)

    lock = _lock_path(path)

    with open(lock, "w") as lock_file:
        fcntl.flock(lock_file, fcntl.LOCK_EX)
        try:
            _write_locked(registry, path)
        finally:
            fcntl.flock(lock_file, fcntl.LOCK_UN)


def _write_locked(registry: RegistryFile, path: Path):
    if path.exists():
        try:
            shutil.copyfile(path, _backup_path(path))
        except OSError:
            pass

    tmp = _tmp_path(path)
    try:
        payload = registry.model_dump(mode="json")
        with open(tmp, "w") as f:
            json.dump(payload, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


def _update_registry(mutate_fn) -> list[AppRecord]:
    path = _registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    lock = _lock_path(path)

    with open(lock, "w") as lock_file:
        fcntl.flock(lock_file, fcntl.LOCK_EX)
        try:
            registry = _load_registry_file(path)
            registry = mutate_fn(registry)
            _write_locked(registry, path)
            return registry.records
        finally:
            fcntl.flock(lock_file, fcntl.LOCK_UN)


def _build_searchable_text(record: AppRecord) -> str:
    parts = [
        record.app_name,
        record.description,
        record.version,
        record.repo,
        record.status.value,
    ]
    if record.deployed_url:
        parts.append(record.deployed_url)
    for v in record.metadata.values():
        if isinstance(v, str):
            parts.append(v)
    return " ".join(parts).lower()


def _record_change(record: AppRecord, field: str, old_value, new_value, now: str):
    old_str = str(old_value) if old_value is not None else None
    new_str = str(new_value) if new_value is not None else None
    if old_str == new_str:
        return
    record.history.append(ChangeRecord(
        field=field,
        old_value=old_str,
        new_value=new_str,
        timestamp=now,
    ))


def _str_value(val) -> Optional[str]:
    if val is None:
        return None
    if isinstance(val, dict):
        return json.dumps(val, sort_keys=True)
    return str(val)


def create_entry(app: AppCreate) -> AppRecord:

    app_id = generate_id()
    now = _now_iso()

    record = AppRecord(
        id=app_id,
        app_name=app.app_name,
        description=app.description,
        version=app.version,
        repo=app.repo,
        status=app.status,
        deployed_url=app.deployed_url,
        metadata=app.metadata,
        created_at=now,
        updated_at=now,
    )

    def _create(registry: RegistryFile) -> RegistryFile:
        existing = any(r.app_name == app.app_name and r.status != RegistryStatus.archived for r in registry.records)
        if existing:
            raise DuplicateAppName(app.app_name)
        registry.records.append(record)
        return registry

    _update_registry(_create)
    logger.info("registry: created app %s (%s)", record.id, record.app_name)
    _fire_hooks("on_create", record)
    return record


def get_entry(app_id: str) -> Optional[AppRecord]:

    path = _registry_path()
    if not path.exists():
        return None

    registry = _load_registry_file(path)
    for record in registry.records:
        if record.id == app_id:
            return record
    return None


def list_entries(
    status: Optional[str] = None,
    search: Optional[str] = None,
    metadata_filter: Optional[dict] = None,
    limit: int = 100,
    offset: int = 0,
    sort: str = "created_at",
) -> list[AppRecord]:

    path = _registry_path()
    if not path.exists():
        return []

    registry = _load_registry_file(path)
    records = registry.records

    if status:
        records = [r for r in records if r.status.value == status]

    if search:
        search_lower = search.lower()
        records = [r for r in records if search_lower in _build_searchable_text(r)]

    if metadata_filter:
        records = [
            r for r in records
            if all(r.metadata.get(k) == v for k, v in metadata_filter.items())
        ]

    if sort in ("created_at", "updated_at"):
        records = sorted(records, key=lambda r: getattr(r, sort), reverse=True)

    return records[offset:offset + limit]


def update_entry(app_id: str, update: AppUpdate) -> Optional[AppRecord]:

    updated_record = None
    old_status = None

    def _update(registry: RegistryFile) -> RegistryFile:
        nonlocal updated_record, old_status
        now = _now_iso()
        for i, record in enumerate(registry.records):
            if record.id == app_id:
                old_status = record.status
                if update.description is not None:
                    _record_change(record, "description", record.description, update.description, now)
                    record.description = update.description
                if update.version is not None:
                    _record_change(record, "version", record.version, update.version, now)
                    record.version = update.version
                if update.status is not None:
                    _record_change(record, "status", record.status.value, update.status.value, now)
                    record.status = update.status
                if update.deployed_url is not None:
                    _record_change(record, "deployed_url", record.deployed_url, update.deployed_url, now)
                    record.deployed_url = update.deployed_url
                if update.repo is not None:
                    _record_change(record, "repo", record.repo, update.repo, now)
                    record.repo = update.repo
                if update.metadata is not None:
                    _record_change(record, "metadata", record.metadata, update.metadata, now)
                    for k, v in update.metadata.items():
                        record.metadata[k] = v
                record.updated_at = now
                updated_record = record
                return registry
        raise AppNotFound(app_id)

    _update_registry(_update)
    logger.info("registry: updated app %s", app_id)

    if updated_record is not None:
        if old_status is not None and old_status != updated_record.status:
            _fire_hooks("on_status_change", updated_record, old_status=old_status.value, new_status=updated_record.status.value)
        _fire_hooks("on_update", updated_record)

    return updated_record


def delete_entry(app_id: str) -> AppRecord:

    record = get_entry(app_id)
    result = update_entry(app_id, AppUpdate(status=RegistryStatus.archived))
    if record is not None:
        _fire_hooks("on_delete", record)
    logger.info("registry: archived app %s", app_id)
    return result


def update_status(app_id: str, status: RegistryStatus) -> Optional[AppRecord]:
    return update_entry(app_id, AppUpdate(status=status))


def set_deployed_url(app_id: str, url: str) -> Optional[AppRecord]:
    return update_entry(app_id, AppUpdate(deployed_url=url))


def update_metadata(app_id: str, metadata: dict) -> Optional[AppRecord]:
    return update_entry(app_id, AppUpdate(metadata=metadata))


def health_check() -> bool:
    path = _registry_path()
    if not path.exists():
        return True
    try:
        _load_registry_file(path)
        return True
    except Exception:
        return False


class AppRegistryError(Exception):
    pass


class DuplicateAppName(AppRegistryError):
    def __init__(self, app_name: str):
        self.app_name = app_name
        super().__init__(f"app_name {app_name!r} already exists")


class AppNotFound(AppRegistryError):
    def __init__(self, app_id: str):
        self.app_id = app_id
        super().__init__(f"app {app_id!r} not found")
