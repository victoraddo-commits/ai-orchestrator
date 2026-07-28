import json
from pathlib import Path


ROADMAP_PATH = Path(__file__).resolve().parent.parent / "roadmap.json"

VALID_STATUSES = {"proposed", "pending", "in_progress", "completed", "failed", "blocked"}


def load_roadmap():
    if not ROADMAP_PATH.exists():
        raise FileNotFoundError(f"No roadmap found at {ROADMAP_PATH}")

    return json.loads(ROADMAP_PATH.read_text())


def save_roadmap(roadmap):
    ROADMAP_PATH.write_text(json.dumps(roadmap, indent=2) + "\n")


def get_phase(phase_id):
    for phase in load_roadmap()["phases"]:
        if phase["id"] == phase_id:
            return phase

    return None


def get_remaining_work():
    return [p for p in load_roadmap()["phases"] if p["status"] != "completed"]


def get_next_phase():
    roadmap = load_roadmap()
    completed_ids = {p["id"] for p in roadmap["phases"] if p["status"] == "completed"}

    candidates = [
        p for p in roadmap["phases"]
        if p["status"] == "pending" and all(dep in completed_ids for dep in p.get("dependencies", []))
    ]

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
