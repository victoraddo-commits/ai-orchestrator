"""UPS state machine + event history (Phase P).

Pure, hardware-independent transition logic so it is fully unit-testable. The
live reader (``ups.py``) feeds snapshots; this module decides the normalized
state, records transitions to an append-only event log, and emits them to the
existing Event Bus.

Critical rule (directive §47): a *communication* failure is NEVER reported as a
utility failure — ``COMMUNICATION LOST`` is a distinct state.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

# Normalized states
ONLINE = "ONLINE"
ON_BATTERY = "ON BATTERY"
LOW_BATTERY = "LOW BATTERY"
CHARGING = "CHARGING"
OVERLOAD = "OVERLOAD"
SHUTDOWN_PENDING = "SHUTDOWN PENDING"
SHUTDOWN = "SHUTDOWN"
RECOVERING = "RECOVERING"
COMM_LOST = "COMMUNICATION LOST"
UNKNOWN = "UNKNOWN"

EVENTS_FILENAME = "ups_events.jsonl"


def normalize(status_flags: str, reachable: bool = True) -> str:
    """Map NUT ``ups.status`` flags to ONE normalized state.

    Severity order (most severe wins): comm-lost handled separately by caller.
    """
    if not reachable:
        return COMM_LOST
    flags = set((status_flags or "").split())
    if not flags:
        return UNKNOWN
    if "FSD" in flags:
        return SHUTDOWN_PENDING
    if "LB" in flags or "LOW" in flags:
        return LOW_BATTERY
    if "OVER" in flags:
        return OVERLOAD
    if "OB" in flags:
        return ON_BATTERY
    if "RB" in flags or "CHRG" in flags:
        return CHARGING
    if "OL" in flags:
        return ONLINE
    return UNKNOWN


def _default_memory_dir() -> Path:
    override = os.environ.get("AI_ORCHESTRATOR_MEMORY_DIR")
    return Path(override) if override else Path("memory")


def _events_path() -> Path:
    return _default_memory_dir() / EVENTS_FILENAME


@dataclass
class Transition:
    frm: str
    to: str
    at: float
    status_flags: str = ""
    duration_s: float | None = None
    detail: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


class UpsStateMachine:
    """Tracks the normalized UPS state across polls and records transitions."""

    def __init__(self, publish=None):
        self.state = UNKNOWN
        self.since = time.time()
        self._publish = publish  # Optional[Callable[[Transition], None]]

    def update(self, status_flags: str, reachable: bool = True) -> Transition | None:
        """Feed a fresh reading; return a Transition if the state changed."""
        new = normalize(status_flags, reachable)
        if new == self.state:
            return None
        now = time.time()
        tr = Transition(frm=self.state, to=new, at=now,
                        status_flags=status_flags,
                        duration_s=round(now - self.since, 1))
        self.state = new
        self.since = now
        self._record(tr)
        if self._publish:
            try:
                self._publish(tr)
            except Exception:  # noqa: BLE001
                pass
        return tr

    def _record(self, tr: Transition) -> None:
        p = _events_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(tr.to_dict()) + "\n")


def read_events(limit: int = 100) -> list[dict]:
    """Return the most recent transitions (newest last)."""
    p = _events_path()
    if not p.exists():
        return []
    lines = p.read_text(encoding="utf-8").splitlines()[-limit:]
    out = []
    for ln in lines:
        try:
            out.append(json.loads(ln))
        except ValueError:
            continue
    return out


def _event_bus_publish(tr: Transition) -> None:
    try:
        from core.eventbus.bus import publish_event
        publish_event("power.ups.state_changed", "smarthome.ups",
                      {"from": tr.frm, "to": tr.to, "at": tr.at,
                       "status_flags": tr.status_flags, "duration_s": tr.duration_s})
    except Exception:  # noqa: BLE001
        pass


def default_machine() -> UpsStateMachine:
    return UpsStateMachine(publish=_event_bus_publish)


def poll_and_update() -> dict:
    """Read the live UPS, advance the state machine, and return a snapshot."""
    from core.smarthome import ups
    snap = ups.status()
    tr = None
    m = _GLOBAL
    if m is not None:
        tr = m.update(snap.get("status_flags", ""), bool(snap.get("reachable")))
        snap["state"] = m.state
        snap["state_since"] = m.since
    return {"snapshot": snap, "transition": tr.to_dict() if tr else None}


_GLOBAL: UpsStateMachine | None = None


def init() -> UpsStateMachine:
    global _GLOBAL
    _GLOBAL = default_machine()
    return _GLOBAL
