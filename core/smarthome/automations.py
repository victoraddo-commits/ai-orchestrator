"""Automations for the KAI smart-home fabric (Phase F).

An automation is ``trigger -> condition* -> action*``. Evaluation reuses the
existing Event Bus for state changes and the Command Bus + AgentGuard for every
action, so no action can bypass policy and all actions are audited.

This module is pure and unit-testable: triggers/conditions/actions are data, and
``evaluate`` takes an injected ``state_provider`` and ``act`` callable.
"""
from __future__ import annotations

import fcntl
import json
import os
import shutil
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

AUTOMATIONS_FILENAME = "smarthome_automations.json"

# Trigger types
TRIGGER_STATE = "state"          # device_id, to (on/off/any)
TRIGGER_TIME = "time"            # at "HH:MM"

# Condition operators
_OPS = {"eq": lambda a, b: a == b, "ne": lambda a, b: a != b,
        "gt": lambda a, b: a > b, "lt": lambda a, b: a < b}


@dataclass
class Automation:
    id: str
    name: str
    enabled: bool
    trigger: dict
    conditions: list[dict] = field(default_factory=list)
    actions: list[dict] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    last_run: float | None = None
    run_count: int = 0

    @classmethod
    def new(cls, name: str, trigger: dict, conditions=None, actions=None,
            enabled: bool = True) -> "Automation":
        return cls(id="aut_" + uuid.uuid4().hex[:10], name=name, enabled=enabled,
                   trigger=trigger, conditions=list(conditions or []),
                   actions=list(actions or []))

    def to_dict(self) -> dict:
        return asdict(self)


def _default_memory_dir() -> Path:
    override = os.environ.get("AI_ORCHESTRATOR_MEMORY_DIR")
    return Path(override) if override else Path("memory")


def _path() -> Path:
    return _default_memory_dir() / AUTOMATIONS_FILENAME


def _load() -> dict:
    p = _path()
    if not p.exists():
        return {"automations": {}}
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


def add(automation: Automation) -> Automation:
    def _do():
        data = _load()
        data["automations"][automation.id] = automation.to_dict()
        _save(data)
        return automation
    return _with_lock(_do)


def list_automations() -> list[Automation]:
    return [Automation(**a) for a in _load()["automations"].values()]


def get(automation_id: str) -> Automation | None:
    raw = _load()["automations"].get(automation_id)
    return Automation(**raw) if raw else None


def delete(automation_id: str) -> bool:
    def _do():
        data = _load()
        if data["automations"].pop(automation_id, None) is None:
            return False
        _save(data)
        return True
    return _with_lock(_do)


def set_enabled(automation_id: str, enabled: bool) -> Automation | None:
    def _do():
        data = _load()
        if automation_id not in data["automations"]:
            return None
        data["automations"][automation_id]["enabled"] = enabled
        _save(data)
        return Automation(**data["automations"][automation_id])
    return _with_lock(_do)


def _record_run(automation_id: str) -> None:
    def _do():
        data = _load()
        a = data["automations"].get(automation_id)
        if a:
            a["last_run"] = time.time()
            a["run_count"] = int(a.get("run_count", 0)) + 1
            _save(data)
    _with_lock(_do)


def trigger_matches(trigger: dict, event: dict) -> bool:
    """Does an event (e.g. {'type':'state','device_id':..,'to':'on'}) fire this?"""
    ttype = trigger.get("type")
    if ttype == TRIGGER_STATE:
        if event.get("type") != "state":
            return False
        if trigger.get("device_id") and trigger["device_id"] != event.get("device_id"):
            return False
        to = trigger.get("to", "any")
        return to == "any" or to == event.get("to")
    if ttype == TRIGGER_TIME:
        return event.get("type") == "time" and trigger.get("at") == event.get("at")
    return False


def conditions_hold(conditions: list[dict], state_provider) -> bool:
    """All conditions must hold. A missing device/field fails closed (False)."""
    for c in conditions:
        st = state_provider(c.get("device_id")) or {}
        actual = st.get("state", st.get(c.get("field"))) if c.get("field") else st.get("state")
        if c.get("field"):
            actual = st.get(c["field"])
        op = _OPS.get(c.get("op", "eq"))
        if op is None or not op(actual, c.get("value")):
            return False
    return True


def run(automation: Automation, state_provider, act) -> dict:
    """Execute an automation if its trigger already matched. Returns a report."""
    if not automation.enabled:
        return {"id": automation.id, "ran": False, "reason": "disabled"}
    if not conditions_hold(automation.conditions, state_provider):
        return {"id": automation.id, "ran": False, "reason": "conditions"}
    results = []
    for action in automation.actions:
        try:
            results.append({"action": action, "result": act(action)})
        except Exception as e:  # noqa: BLE001
            results.append({"action": action, "error": str(e)})
    _record_run(automation.id)
    return {"id": automation.id, "ran": True, "results": results}


def dispatch_event(event: dict, state_provider, act) -> list[dict]:
    """Fire every enabled automation whose trigger matches the event."""
    out = []
    for a in list_automations():
        if trigger_matches(a.trigger, event):
            out.append(run(a, state_provider, act))
    return out
