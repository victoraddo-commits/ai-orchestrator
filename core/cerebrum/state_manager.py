"""World state management for Cerebrum Simulation Engine."""
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

_MEMORY_DIR = Path(__file__).resolve().parent.parent / "memory"


def snapshot_world_state(context_entities: Optional[List[str]] = None) -> Dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()
    snapshot = {
        "snapshot_id": str(uuid.uuid4()),
        "captured_at": now,
        "system": {},
        "incidents": {"recent_count": 0},
        "learning": [],
        "builds": [],
        "context_entities": context_entities or [],
        "entity_state": {},
    }

    # Load system state
    ss_path = _MEMORY_DIR / "system_state.json"
    if ss_path.exists():
        try:
            with open(ss_path) as f:
                data = json.load(f)
            snapshot["system"] = data
        except Exception:
            pass

    # Load incidents
    inc_path = _MEMORY_DIR / "incidents.json"
    if inc_path.exists():
        try:
            with open(inc_path) as f:
                data = json.load(f)
            records = data.get("records", [])
            snapshot["incidents"] = {"recent_count": len(records[-10:]) if records else 0}
        except Exception:
            pass

    # Load learning
    learn_path = _MEMORY_DIR / "learning_lessons.json"
    if learn_path.exists():
        try:
            with open(learn_path) as f:
                data = json.load(f)
            snapshot["learning"] = data.get("records", [])[-20:] if data.get("records") else []
        except Exception:
            pass

    # Load builds
    builds_path = _MEMORY_DIR / "builds.json"
    if builds_path.exists():
        try:
            with open(builds_path) as f:
                data = json.load(f)
            snapshot["builds"] = data.get("records", [])[-50:] if data.get("records") else []
        except Exception:
            pass

    # Entity state for context entities
    if context_entities:
        for entity in context_entities:
            snapshot["entity_state"][entity] = _extract_entity_state(entity, snapshot["incidents"], snapshot["learning"])

    return snapshot


def _extract_entity_state(entity_name: str, incidents: Dict[str, Any] = None,
                          learning: List[Dict[str, Any]] = None) -> Dict[str, Any]:
    if incidents is None:
        incidents = {}
    if learning is None:
        learning = []

    return {
        "entity": entity_name,
        "recent_incidents_count": incidents.get("recent_count", 0) if isinstance(incidents, dict) else 0,
        "current_state": {},
    }
