"""
Mission execution engine — create, approve, checkpoint, drift detection, execution loop.
"""

import re
import time
import logging
import threading
from datetime import datetime

from core.kai.mission_store import (
    new_mission,
    transition_mission,
    get_mission,
    update_mission,
    load_missions,
    save_missions,
)

logger = logging.getLogger(__name__)

MISSION_DRIFT_THRESHOLD = 0.4
MISSION_EXECUTION_LOOP_DELAY_SEC = 5


def create_mission(
    objective,
    context=None,
    models=None,
    tools=None,
    constraints=None,
    success_criteria=None,
    risk="low",
    budget=None,
    deadline=None,
):
    """Create a new mission and store it. Returns the mission dict."""
    return new_mission(
        objective=objective,
        context=context,
        models=models,
        tools=tools,
        constraints=constraints,
        success_criteria=success_criteria,
        risk=risk,
        budget=budget,
        deadline=deadline,
    )


def approve_mission(mission_id):
    """Transition a mission from 'proposed' to 'approved'."""
    return transition_mission(mission_id, "approved")


def checkpoint_mission(mission_id, completed_action, artifacts=None, worker_states=None):
    """Append a completed action, update artifacts and worker states, increment
    checkpoint_sequence. Returns the updated checkpoint dict."""
    mission = get_mission(mission_id)
    if mission is None:
        raise ValueError(f"Mission not found: {mission_id}")

    checkpoint = mission.get("checkpoint", {})
    checkpoint["sequence"] = checkpoint.get("sequence", 0) + 1
    checkpoint.setdefault("completed_actions", []).append(completed_action)

    pending = checkpoint.get("pending_actions", [])
    if pending and completed_action in pending:
        pending.remove(completed_action)
    checkpoint["pending_actions"] = pending

    if artifacts:
        checkpoint.setdefault("artifacts", []).extend(artifacts)
    if worker_states:
        checkpoint.setdefault("worker_states", {}).update(worker_states)

    total = len(checkpoint.get("completed_actions", [])) + len(pending)
    completed_count = len(checkpoint.get("completed_actions", []))
    progress = int((completed_count / total * 100) if total > 0 else 0)

    update_mission(mission_id, {"checkpoint": checkpoint, "progress_pct": progress})
    return checkpoint


def compute_drift_score(original_objective, current_objective):
    """Jaccard word-overlap similarity between two objective strings.

    Returns a float 0.0 (identical) to 1.0 (completely different).
    """

    def tokenize(text):
        tokens = re.sub(r"[^\w\s]", " ", text.lower()).split()
        return set(tokens)

    orig_set = tokenize(original_objective)
    curr_set = tokenize(current_objective)
    if not orig_set or not curr_set:
        return 0.0
    jaccard = len(orig_set & curr_set) / len(orig_set | curr_set)
    return round(1 - jaccard, 3)


def check_drift(mission_id):
    """Compare current objective to original. If drift_score >= threshold,
    transition mission to 'drifted'. Returns the updated mission."""
    mission = get_mission(mission_id)
    if mission is None:
        raise ValueError(f"Mission not found: {mission_id}")

    original = mission.get("original_objective", "")
    current = mission.get("objective", "")
    score = compute_drift_score(original, current)
    update_mission(mission_id, {"drift_score": score})

    if score >= MISSION_DRIFT_THRESHOLD and mission.get("status") == "running":
        return transition_mission(mission_id, "drifted", note=f"drift_score={score}")

    return get_mission(mission_id)


def finalize_mission(mission_id, status, note=None):
    """Transition a mission to a terminal or restartable state.
    Valid statuses: completed, failed, stopped, paused."""
    valid = {"completed", "failed", "stopped", "paused"}
    if status not in valid:
        raise ValueError(
            f"finalize_mission: status must be one of {valid}, got {status!r}"
        )
    return transition_mission(mission_id, status, note=note)


def steer_mission(mission_id, action, objective=None, note=None):
    """Apply an operator steering action to a mission and persist it.

    Actions (roadmap 20D): ``pause``/``resume`` move the mission between
    ``running`` and ``paused``; ``stop`` finalises it as ``stopped``;
    ``redirect`` replaces the objective and records the resulting drift.

    Raises ``LookupError`` when the mission is unknown, ``ValueError`` for an
    unknown action or a redirect without an objective, and
    ``core.lifecycle.InvalidTransition`` when the state machine forbids it.
    """
    mission = get_mission(mission_id)
    if mission is None:
        raise LookupError(f"mission not found: {mission_id}")

    action = (action or "").strip().lower()
    if action in ("pause", "resume"):
        target = {"pause": "paused", "resume": "running"}[action]
        return transition_mission(mission_id, target, note=note or f"steer:{action}")
    if action == "stop":
        return transition_mission(mission_id, "stopped", note=note or "steer:stop")
    if action == "redirect":
        new_objective = (objective or "").strip()
        if not new_objective:
            raise ValueError("redirect requires a new objective")
        drift = compute_drift_score(mission.get("original_objective", ""), new_objective)
        history = list(mission.get("history") or [])
        history.append({
            "status": "redirected",
            "timestamp": datetime.now().isoformat(),
            "note": note or new_objective,
        })
        return update_mission(mission_id, {
            "objective": new_objective,
            "drift_score": drift,
            "history": history,
        })
    raise ValueError(f"unknown steering action: {action!r}")


def _execute_action(mission_id, action):
    """Phase 1 placeholder: log the action and return a placeholder result."""
    logger.info(
        "[mission_engine] mission=%s executing action: %s", mission_id, action
    )
    return f"[Phase 1 placeholder] executed: {action.get('action', 'unknown')}"


def execute_mission(mission_id):
    """Synchronous execution loop for an approved mission.

    1. Transition 'approved' -> 'running'
    2. Loop while running:
       a. Load latest mission state (re-fetch each iteration)
       b. Get next pending action from checkpoint plan
       c. If no pending actions -> finalize as 'completed'
       d. Execute action via _execute_action
       e. Checkpoint the result
       f. Check drift — break if drifted
       g. Sleep MISSION_EXECUTION_LOOP_DELAY_SEC
    3. Return final mission dict
    """
    mission = get_mission(mission_id)
    if mission is None:
        raise ValueError(f"Mission not found: {mission_id}")

    if mission.get("status") == "approved":
        transition_mission(mission_id, "running")

    while True:
        mission = get_mission(mission_id)
        if mission is None or mission.get("status") != "running":
            break

        checkpoint = mission.get("checkpoint", {})
        pending = checkpoint.get("pending_actions", [])

        if not pending:
            finalize_mission(mission_id, "completed", note="All actions completed")
            break

        action = pending[0]
        result = _execute_action(mission_id, action)
        checkpoint_mission(mission_id, action, artifacts=[result])

        mission = check_drift(mission_id)
        if mission and mission.get("status") == "drifted":
            break

        time.sleep(MISSION_EXECUTION_LOOP_DELAY_SEC)

    return get_mission(mission_id)


def recover_running_missions():
    """On startup, scan for running missions and resume them from checkpoints.

    Uses threading to avoid blocking the startup sequence.
    """
    running = load_missions(status="running")
    if not running:
        return
    for mission in running:
        mission_id = mission["id"]
        logger.info("[MissionEngine] Recovering running mission %s", mission_id)
        t = threading.Thread(
            target=_run_mission_thread,
            args=(mission_id,),
            daemon=True,
            name=f"mission-recover-{mission_id}",
        )
        t.start()


def _run_mission_thread(mission_id):
    """Target for threading — runs execute_mission in a background thread."""
    try:
        execute_mission(mission_id)
    except Exception as e:
        logger.error("[MissionEngine] Recovered mission %s failed: %s", mission_id, e)
