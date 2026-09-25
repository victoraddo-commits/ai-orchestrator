"""Canonical Smart Home device registry.

Atomic read-modify-write over ``memory/smarthome_registry.json`` guarded by
``fcntl.flock`` (same pattern as ``core/app_registry.py``). ONE registry for
smart-home devices; the mobile registry in ``core/device_registry.py`` is a
different concern and is not touched.
"""
from __future__ import annotations

import fcntl
import json
import logging
import os
import shutil
from pathlib import Path
from typing import Callable

from core.smarthome.models import DeviceCreate, DeviceRecord, _now_iso

logger = logging.getLogger(__name__)

CURRENT_SCHEMA_VERSION = 1
REGISTRY_FILENAME = "smarthome_registry.json"

_hooks: dict[str, list[Callable]] = {"on_create": [], "on_update": [], "on_delete": []}


def register_hook(event: str, callback: Callable) -> None:
    if event in _hooks:
        _hooks[event].append(callback)


def _fire(event: str, rec: DeviceRecord) -> None:
    for cb in _hooks.get(event, []):
        try:
            cb(rec)
        except Exception:  # noqa: BLE001
            logger.exception("smarthome registry hook %s failed", event)


def _default_memory_dir() -> Path:
    override = os.environ.get("AI_ORCHESTRATOR_MEMORY_DIR")
    return Path(override) if override else Path("memory")


def _path() -> Path:
    return _default_memory_dir() / REGISTRY_FILENAME


def _load() -> dict:
    p = _path()
    if not p.exists():
        return {"schema_version": CURRENT_SCHEMA_VERSION, "devices": {}}
    with p.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _save(data: dict) -> None:
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, sort_keys=True)
    shutil.move(str(tmp), str(p))


def _with_lock(fn):
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    lock = p.with_suffix(".json.lock")
    with lock.open("w") as lf:
        fcntl.flock(lf, fcntl.LOCK_EX)
        try:
            return fn()
        finally:
            fcntl.flock(lf, fcntl.LOCK_UN)


def _by_provider_key(data: dict, provider: str, provider_id: str) -> str | None:
    for did, raw in data["devices"].items():
        if raw.get("provider") == provider and raw.get("provider_id") == provider_id:
            return did
    return None


def upsert(create: DeviceCreate) -> DeviceRecord:
    def _do():
        data = _load()
        existing = _by_provider_key(data, create.provider, create.provider_id)
        if existing:
            rec = DeviceRecord.from_dict(data["devices"][existing])
            rec.name = create.name or rec.name
            if create.kind.value != "unknown":
                rec.kind = create.kind
            if create.room is not None:
                rec.room = create.room
            if create.capabilities:
                rec.capabilities = list(create.capabilities)
            rec.address = create.address or rec.address
            rec.mac = create.mac or rec.mac
            rec.vendor = create.vendor or rec.vendor
            rec.updated_at = _now_iso()
            rec.last_seen = rec.updated_at
            data["devices"][existing] = rec.to_dict()
            _save(data)
            _fire("on_update", rec)
            return rec
        rec = DeviceRecord.new(
            name=create.name, provider=create.provider,
            provider_id=create.provider_id, kind=create.kind, room=create.room,
            capabilities=create.capabilities, address=create.address,
            mac=create.mac, vendor=create.vendor)
        rec.last_seen = rec.created_at
        data["devices"][rec.id] = rec.to_dict()
        _save(data)
        _fire("on_create", rec)
        return rec
    return _with_lock(_do)


def get(device_id: str) -> DeviceRecord | None:
    raw = _load()["devices"].get(device_id)
    return DeviceRecord.from_dict(raw) if raw else None


def list_devices() -> list[DeviceRecord]:
    return [DeviceRecord.from_dict(v) for v in _load()["devices"].values()]


def set_state(device_id: str, state: dict, fresh: bool = True) -> DeviceRecord | None:
    def _do():
        data = _load()
        if device_id not in data["devices"]:
            return None
        rec = DeviceRecord.from_dict(data["devices"][device_id])
        rec.state = state
        rec.state_fresh = fresh
        rec.last_seen = _now_iso()
        data["devices"][device_id] = rec.to_dict()
        _save(data)
        _fire("on_update", rec)
        return rec
    return _with_lock(_do)


def set_room(device_id: str, room: str | None) -> DeviceRecord | None:
    """Assign or clear a device's room. Never invents a room."""
    def _do():
        data = _load()
        if device_id not in data["devices"]:
            return None
        rec = DeviceRecord.from_dict(data["devices"][device_id])
        rec.room = room
        rec.updated_at = _now_iso()
        data["devices"][device_id] = rec.to_dict()
        _save(data)
        _fire("on_update", rec)
        return rec
    return _with_lock(_do)


def delete(device_id: str) -> bool:
    def _do():
        data = _load()
        raw = data["devices"].pop(device_id, None)
        if raw is None:
            return False
        _save(data)
        _fire("on_delete", DeviceRecord.from_dict(raw))
        return True
    return _with_lock(_do)
