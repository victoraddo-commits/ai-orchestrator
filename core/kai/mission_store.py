"""
Mission persistence layer.

Manages CRUD for missions via core.memory (atomic JSON writes).
State machine transitions use core.lifecycle.transition().
"""

from datetime import datetime

from core.memory import load, save
from core.lifecycle import transition
from core.id_generator import generate_id

TERMINAL_STATES = {"failed", "stopped", "completed"}

MISSION_TRANSITIONS = {
    "proposed": ["approved"],
    "approved": ["running", "completed"],
    "running": ["paused", "completed", "failed", "drifted", "stopped"],
    "paused": ["running", "failed"],
    "completed": [],
    "failed": ["drifted"],
    "drifted": ["stopped"],
    "stopped": [],
}


def _ensure_file():
    """Create missions.json with empty list if it doesn't exist."""
    data = load("missions.json")
    if data is None:
        save("missions.json", [])


def load_missions(status=None):
    """Load all missions, optionally filtered by status."""
    _ensure_file()
    missions = load("missions.json")
    if not isinstance(missions, list):
        missions = []
    if status is not None:
        missions = [m for m in missions if m.get("status") == status]
    return missions


def save_missions(missions):
    """Atomic write of missions list via core.memory."""
    save("missions.json", missions)


def get_mission(mission_id):
    """Return a single mission by id, or None if not found."""
    missions = load_missions()
    for m in missions:
        if m.get("id") == mission_id:
            return m
    return None


def new_mission(
    objective,
    context=None,
    models=None,
    tools=None,
    constraints=None,
    success_criteria=None,
    risk="low",
    budget=None,
    deadline=None,
    priority=5,
):
    """Create a new mission with 'proposed' status and all schema fields."""
    now = datetime.now().isoformat()
    mission = {
        "id": generate_id(),
        "objective": objective,
        "original_objective": objective,
        "context": context,
        "status": "proposed",
        "risk": risk,
        "priority": priority,
        "models": models or [],
        "tools": tools or [],
        "constraints": constraints or [],
        "success_criteria": success_criteria or [],
        "budget": budget,
        "deadline": deadline,
        "drift_score": 0.0,
        "progress_pct": 0,
        "current_phase": None,
        "checkpoint": {
            "sequence": 0,
            "plan": [],
            "pending_actions": [],
            "completed_actions": [],
            "artifacts": [],
            "worker_states": {},
        },
        "history": [{"action": "created", "timestamp": now}],
        "created": now,
        "updated": now,
    }
    missions = load_missions()
    missions.append(mission)
    save_missions(missions)
    return mission


def update_mission(mission_id, changes):
    """Apply changes dict to mission, update 'updated' timestamp. Returns mutated mission."""
    missions = load_missions()
    for m in missions:
        if m.get("id") == mission_id:
            m.update(changes)
            m["updated"] = datetime.now().isoformat()
            save_missions(missions)
            return m
    return None


def transition_mission(mission_id, new_status, note=None):
    """Validate state machine, record history, persist. Returns mutated mission."""
    missions = load_missions()
    for m in missions:
        if m.get("id") == mission_id:
            current = m.get("status")
            if current in TERMINAL_STATES:
                from core.lifecycle import InvalidTransition

                raise InvalidTransition(
                    f"cannot transition from terminal state {current!r}"
                )
            transition(m, new_status, MISSION_TRANSITIONS, note=note)
            m["updated"] = datetime.now().isoformat()
            save_missions(missions)
            return m
    return None
