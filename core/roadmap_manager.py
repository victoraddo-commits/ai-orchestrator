"""Phase 12L: Autonomous Engineering Manager.

Drives the roadmap forward -- proposes, plans, and generates changes for
each remaining phase -- but never approves anything on its own behalf.
core/build_manager.py's ARCHITECTURE_APPROVED and DEPLOY_APPROVAL gates are
untouched: they still require an explicit human action via
POST /builds/{id}/approve-architecture and .../approve-deploy. This module
calls neither. That split is the entire point of this phase: the system may
propose, test, and deploy improvements to itself, but it does not get
unrestricted authority to rewrite its own core code without approval.

Autonomous mode defaults to OFF. Even starting this loop is a deliberate
human action (POST /roadmap/autonomous/enable), not something that
activates just because this module is imported.
"""

from pathlib import Path

from core.memory import load, save
from core.roadmap_engine import get_next_phase, update_phase
from core.build_manager import create_build, get_build


SELF_PROJECT_PATH = Path(__file__).resolve().parent.parent

AUTONOMOUS_MODE_FILE = "autonomous_mode.json"

# A failed phase is never retried automatically -- it stops the loop for
# that phase and waits for a human to look at it, matching "not unrestricted
# authority." COMPLETED is the only status advance_roadmap() itself grants;
# every other forward-moving status (ARCHITECTURE_APPROVED, DEPLOYING,
# VERIFIED, COMPLETED) already happens through the existing, human-gated
# build_manager pipeline once a build exists.
STOPPING_BUILD_STATUSES = {"FAILED", "ROLLED_BACK"}


def is_autonomous_mode_enabled():
    return bool((load(AUTONOMOUS_MODE_FILE) or {}).get("enabled"))


def enable_autonomous_mode():
    save(AUTONOMOUS_MODE_FILE, {"enabled": True})


def disable_autonomous_mode():
    save(AUTONOMOUS_MODE_FILE, {"enabled": False})


def is_self_modifying(project_path):
    try:
        return Path(project_path).resolve() == SELF_PROJECT_PATH
    except OSError:
        return False


def _build_description(phase):
    criteria = "\n".join(f"- {c}" for c in phase.get("completion_criteria", []))
    return (
        f"{phase.get('description', '')}\n\n"
        f"This is a self-modifying change to the ai-orchestrator project itself "
        f"(roadmap phase {phase['id']}). Completion criteria:\n{criteria}"
    )


def advance_roadmap():
    if not is_autonomous_mode_enabled():
        return {"action": "disabled"}

    from core.roadmap_engine import load_roadmap

    in_progress = [p for p in load_roadmap()["phases"] if p["status"] == "in_progress" and p.get("build_id")]

    for phase in in_progress:
        build = get_build(phase["build_id"])

        if build is None:
            continue

        if build["status"] == "COMPLETED":
            update_phase(phase["id"], status="completed")
            return {"action": "phase_completed", "phase_id": phase["id"], "build_id": phase["build_id"]}

        if build["status"] in STOPPING_BUILD_STATUSES:
            update_phase(phase["id"], status="failed")
            return {
                "action": "phase_failed",
                "phase_id": phase["id"],
                "build_id": phase["build_id"],
                "reason": build.get("failure_reason"),
            }

    next_phase = get_next_phase()

    if next_phase is None:
        return {"action": "nothing_to_do"}

    build = create_build(
        name=next_phase["id"],
        description=_build_description(next_phase),
        project_path=str(SELF_PROJECT_PATH),
    )

    update_phase(next_phase["id"], status="in_progress", build_id=build["id"])

    return {"action": "started_phase", "phase_id": next_phase["id"], "build_id": build["id"]}
