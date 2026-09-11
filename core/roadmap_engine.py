import json
import os
import shutil
import time
from pathlib import Path


# Overridable so test/isolated runs never touch the production roadmap
# (AI_ORCHESTRATOR_MEMORY_DIR does NOT cover this file — a 2026-09-10 test
# snippet relied on that and wiped production; see save_roadmap guard).
ROADMAP_PATH = Path(
    os.environ.get(
        "AI_ORCHESTRATOR_ROADMAP_PATH",
        str(Path(__file__).resolve().parent.parent / "roadmap.json"),
    )
)

VALID_STATUSES = {"proposed", "pending", "in_progress", "completed", "failed", "blocked"}


def load_roadmap():
    if not ROADMAP_PATH.exists():
        raise FileNotFoundError(f"No roadmap found at {ROADMAP_PATH}")

    return json.loads(ROADMAP_PATH.read_text())


def save_roadmap(roadmap):
    # A non-atomic write_text here let an ancient 1-phase copy silently
    # replace all 181 phases on 2026-09-10. Guard: snapshot the old file
    # before any drastic phase-count shrink, and always write atomically.
    new_count = len(roadmap.get("phases", []))
    if ROADMAP_PATH.exists():
        try:
            old_count = len(json.loads(ROADMAP_PATH.read_text()).get("phases", []))
        except (json.JSONDecodeError, OSError):
            old_count = 0
        if old_count >= 10 and new_count < old_count // 2:
            snapshot = ROADMAP_PATH.with_name(
                f"roadmap.json.pre-shrink-{time.strftime('%Y%m%d-%H%M%S')}"
            )
            shutil.copy2(ROADMAP_PATH, snapshot)
    tmp = ROADMAP_PATH.with_name("roadmap.json.tmp")
    tmp.write_text(json.dumps(roadmap, indent=2) + "\n")
    os.replace(tmp, ROADMAP_PATH)


def get_phase(phase_id):
    for phase in load_roadmap()["phases"]:
        if phase["id"] == phase_id:
            return phase

    return None


def get_remaining_work():
    return [p for p in load_roadmap()["phases"] if p["status"] != "completed"]


def get_candidate_phases():
    """Pending phases whose dependencies are all completed -- i.e. everything
    eligible to start next, before any ordering/selection is applied. Split
    out from get_next_phase() so callers that need a different selection
    strategy (e.g. core.roadmap_manager's value-based scoring) can reuse the
    same eligibility filter instead of duplicating it."""

    roadmap = load_roadmap()
    completed_ids = {p["id"] for p in roadmap["phases"] if p["status"] == "completed"}

    return [
        p for p in roadmap["phases"]
        if p["status"] == "pending" and all(dep in completed_ids for dep in p.get("dependencies", []))
    ]


def get_next_phase():
    candidates = get_candidate_phases()

    if not candidates:
        return None

    return min(candidates, key=lambda p: p["priority"])


def mark_phase_status(phase_id, status):
    if status not in VALID_STATUSES:
        raise ValueError(f"Unknown status {status!r}, expected one of {sorted(VALID_STATUSES)}")

    roadmap = load_roadmap()

    for phase in roadmap["phases"]:
        if phase["id"] == phase_id:
            phase["status"] = status
            save_roadmap(roadmap)
            return phase

    raise ValueError(f"Unknown phase id: {phase_id!r}")


def add_phase(id, name, description, dependencies, priority, status="proposed", **fields):
    # Defaults to "proposed", not "pending" -- get_next_phase() only ever
    # picks up "pending" phases, so a human must explicitly promote a
    # proposed phase before the autonomous manager will act on it.
    roadmap = load_roadmap()
    existing_ids = {p["id"] for p in roadmap["phases"]}

    if id in existing_ids:
        raise ValueError(f"Phase id already exists: {id!r}")

    unresolved = [dep for dep in dependencies if dep not in existing_ids]
    if unresolved:
        raise ValueError(f"Unresolvable dependencies: {unresolved}")

    if status not in VALID_STATUSES:
        raise ValueError(f"Unknown status {status!r}, expected one of {sorted(VALID_STATUSES)}")

    phase = {
        "id": id,
        "name": name,
        "description": description,
        "status": status,
        "dependencies": dependencies,
        "priority": priority,
        **fields,
    }

    roadmap["phases"].append(phase)
    save_roadmap(roadmap)

    return phase


def update_phase(phase_id, **fields):
    roadmap = load_roadmap()

    for phase in roadmap["phases"]:
        if phase["id"] == phase_id:
            phase.update(fields)
            save_roadmap(roadmap)
            return phase

    raise ValueError(f"Unknown phase id: {phase_id!r}")


def get_progress_summary():
    phases = load_roadmap()["phases"]

    summary = {"total": len(phases)}
    for status in VALID_STATUSES:
        summary[status] = len([p for p in phases if p["status"] == status])

    summary["percent_complete"] = round(summary["completed"] / summary["total"] * 100, 1) if phases else 0.0

    return summary
