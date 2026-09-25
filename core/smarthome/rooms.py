"""Room mapping for the KAI smart-home fabric.

Rooms are a thin, atomic store mapping a device to a named room. Devices with no
room render as "Unassigned" — we never invent a room.
"""
from __future__ import annotations

import fcntl
import json
import os
import shutil
from pathlib import Path

from core.smarthome.models import Room

ROOMS_FILENAME = "smarthome_rooms.json"


def _default_memory_dir() -> Path:
    override = os.environ.get("AI_ORCHESTRATOR_MEMORY_DIR")
    return Path(override) if override else Path("memory")


def _path() -> Path:
    return _default_memory_dir() / ROOMS_FILENAME


def _load() -> dict:
    p = _path()
    if not p.exists():
        return {"rooms": {}}
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


def upsert_room(room: Room) -> Room:
    def _do():
        data = _load()
        data["rooms"][room.id] = room.to_dict()
        _save(data)
        return room
    return _with_lock(_do)


def list_rooms() -> list[Room]:
    return [Room(**r) for r in _load()["rooms"].values()]


def delete_room(room_id: str) -> bool:
    def _do():
        data = _load()
        if data["rooms"].pop(room_id, None) is None:
            return False
        _save(data)
        return True
    return _with_lock(_do)


def group_by_room(devices: list) -> dict:
    """Group device records by room id; devices with no room go to ``__unassigned``."""
    out: dict[str, list] = {}
    for d in devices:
        out.setdefault(d.room or "__unassigned", []).append(d)
    return out
