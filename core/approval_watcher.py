"""Approval queue watcher — merged from telegra-approval-responder.
Watches memory/approval_queue.json for new pending items and status changes,
sending Telegram notifications via kai-telegram-core."""
import hashlib
import os
from pathlib import Path
from typing import Optional

QUEUE_PATH = Path("/project/ai-orchestrator/memory/approval_queue.json")

_last_hash: Optional[str] = None
_last_items: list = []

def _file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()

def _load_items() -> list:
    if not QUEUE_PATH.exists():
        return []
    import json
    try:
        data = json.loads(QUEUE_PATH.read_text())
        return data.get("records", [])
    except (json.JSONDecodeError, OSError):
        return []

def _pending_items(items: list) -> list:
    return [i for i in items if i.get("status") == "pending"]

def _status_changes(old: list, new: list) -> list:
    old_map = {i.get("id"): i for i in old}
    new_map = {i.get("id"): i for i in new}
    changes = []
    for id_, item in new_map.items():
        old_item = old_map.get(id_)
        if old_item is None:
            continue
        old_status = old_item.get("status")
        new_status = item.get("status")
        if old_status != new_status:
            changes.append({"id": id_, "old": old_status, "new": new_status,
                             "item": item})
    return changes

def poll_once():
    """Check the queue once. Returns dict with 'new_pending' and 'status_changes'."""
    global _last_hash, _last_items

    if not QUEUE_PATH.exists():
        return {"new_pending": [], "status_changes": []}

    current_hash = _file_hash(QUEUE_PATH)

    if current_hash == _last_hash:
        return {"new_pending": [], "status_changes": []}

    new_items = _load_items()
    new_pending = _pending_items(new_items)
    old_pending_ids = {i["id"] for i in _pending_items(_last_items)}
    fresh_pending = [i for i in new_pending if i["id"] not in old_pending_ids]

    changes = _status_changes(_last_items, new_items)

    _last_hash = current_hash
    _last_items = new_items

    return {"new_pending": fresh_pending, "status_changes": changes}
