"""
Mission steering — pause, resume, stop, redirect, replan.
"""

from datetime import datetime

from core.kai.mission_store import (
    get_mission,
    update_mission,
    transition_mission,
    MISSION_TRANSITIONS,
    TERMINAL_STATES,
)

RUNNING_STATES = {"running"}


def pause_mission(mission_id, note=None):
    """Transition a running mission to paused. Returns the updated mission."""
    return transition_mission(mission_id, "paused", note=note)


def resume_mission(mission_id, note=None):
    """Transition a paused mission back to running. Returns the updated mission."""
    return transition_mission(mission_id, "running", note=note)


def stop_mission(mission_id, note=None):
    """Transition any non-terminal mission to stopped. Returns the updated mission."""
    mission = get_mission(mission_id)
    if mission is None:
        raise ValueError(f"Mission not found: {mission_id}")
    if mission.get("status") in TERMINAL_STATES:
        raise ValueError(f"Mission {mission_id} is already in terminal state")
    return transition_mission(mission_id, "stopped", note=note)


def redirect_mission(mission_id, new_objective, note=None):
    """Redirect a mission to a new objective.

    - Updates objective to new_objective
    - Preserves original_objective unchanged
    - Resets drift_score to 0.0
    - Clears pending_actions (new objective may invalidate old plan)
    - Preserves completed_actions
    - Appends to history
    """
    mission = get_mission(mission_id)
    if mission is None:
        raise ValueError(f"Mission not found: {mission_id}")

    changes = {
        "objective": new_objective,
        "drift_score": 0.0,
    }
    checkpoint = mission.get("checkpoint", {})
    checkpoint["pending_actions"] = []
    changes["checkpoint"] = checkpoint

    history = mission.get("history", [])
    history.append({
        "action": "redirected",
        "timestamp": datetime.now().isoformat(),
        "note": note or f"Redirected to: {new_objective}",
    })
    changes["history"] = history

    return update_mission(mission_id, changes)


def replan_mission(mission_id, new_plan, note=None):
    """Replace the mission plan with a new one.

    - Replaces checkpoint.plan and checkpoint.pending_actions with new_plan
    - Preserves checkpoint.completed_actions
    - Appends to history if note is provided
    """
    mission = get_mission(mission_id)
    if mission is None:
        raise ValueError(f"Mission not found: {mission_id}")

    checkpoint = mission.get("checkpoint", {})
    checkpoint["plan"] = new_plan
    checkpoint["pending_actions"] = list(new_plan)
    changes = {"checkpoint": checkpoint}

    if note:
        history = mission.get("history", [])
        history.append({
            "action": "replanned",
            "timestamp": datetime.now().isoformat(),
            "note": note,
        })
        changes["history"] = history

    return update_mission(mission_id, changes)
