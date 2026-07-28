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

import subprocess
import uuid
from pathlib import Path

from core.memory import load, save
from core.roadmap_engine import get_candidate_phases, update_phase
from core.build_manager import create_build, get_build, load_builds, save_builds, BUILD_TRANSITIONS
from core.lifecycle import transition
from core.build_learning import record_build_outcome, TERMINAL_STATUSES
# 13C's Improvement Proposal store -- the source of the risk/expected-benefit
# signal _phase_value_score() scores phases by. No cycle: core.kai.planner
# never imports core.roadmap_manager.
from core.kai.planner import load_proposals


SELF_PROJECT_PATH = Path(__file__).resolve().parent.parent

# Self-modifying builds must never operate directly on SELF_PROJECT_PATH --
# that's this session's own live working directory. Every self-modifying
# build tonight that checked out a branch there collided with interactive
# git commands and service restarts running concurrently (confirmed live:
# repeated "Read timed out" / "process exited with code 1" failures on
# builds whose branch happened to be checked out while an unrelated git or
# systemctl command ran). _create_isolated_self_clone() gives each build its
# own disposable clone instead; the live repo is only ever read from.
SELF_BUILD_WORKSPACE_ROOT = Path.home() / ".ai-orchestrator" / "self-build-workspaces"

AUTONOMOUS_MODE_FILE = "autonomous_mode.json"

# A failed phase is never retried automatically -- it stops the loop for
# that phase and waits for a human to look at it, matching "not unrestricted
# authority." COMPLETED is the only status advance_roadmap() itself grants;
# every other forward-moving status (ARCHITECTURE_APPROVED, DEPLOYING,
# VERIFIED, COMPLETED) already happens through the existing, human-gated
# build_manager pipeline once a build exists.
STOPPING_BUILD_STATUSES = {"FAILED", "ROLLED_BACK"}

# The fixed, known test command for THIS repo's own suite. Unlike the
# generic "what's the test command for an arbitrary generated app" problem
# Phase 12E left open (no automated TESTING logic exists for ordinary
# builds), a self-modifying build is always this same codebase, so the
# command never varies. Isolated self-build clones (_create_isolated_self_clone)
# are plain `git clone`s and never carry their own .venv (gitignored) --
# the interpreter comes from this live repo's venv, while `cwd` points at
# the clone actually being validated, so tests run against the generated
# code, not the live repo's own (unchanged) copy.
SELF_TEST_ARGS = ["tests/"]
SELF_TEST_TIMEOUT = 300
SELF_TEST_OUTPUT_LIMIT = 10000

# advance_roadmap() only ever creates self-modifying builds (isolated clones
# of SELF_PROJECT_PATH), so every build it tracks via phase["build_id"] is,
# by construction, a self-modifying build -- no separate is_self_modifying()
# check is needed at the test-execution hook below.


def _self_test_executable():
    return SELF_PROJECT_PATH / ".venv" / "bin" / "pytest"


def _run_self_build_tests(project_path):
    try:
        completed = subprocess.run(
            [str(_self_test_executable())] + SELF_TEST_ARGS,
            cwd=project_path,
            capture_output=True,
            text=True,
            timeout=SELF_TEST_TIMEOUT,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return {"passed": False, "returncode": None, "output": str(error)}

    output = (completed.stdout or "") + (completed.stderr or "")

    return {
        "passed": completed.returncode == 0,
        "returncode": completed.returncode,
        "output": output[-SELF_TEST_OUTPUT_LIMIT:],
    }


def _apply_self_build_test_result(build_id, result):
    # Reuses build_manager's own transition table and TERMINAL_STATUSES
    # bookkeeping (the same pattern _run_generation/_run_deployment use)
    # rather than duplicating it -- DEPLOY_APPROVAL -> FAILED is already a
    # legal transition, so failing tests never needs a new state or a path
    # around DEPLOY_APPROVAL/ARCHITECTURE_APPROVED.
    builds = load_builds()

    for build in builds:
        if build.get("id") != build_id:
            continue

        build["self_build_test_result"] = result

        if not result["passed"]:
            transition(build, "FAILED", BUILD_TRANSITIONS)
            build["failure_reason"] = (
                f"Self-build test suite failed ({_self_test_executable()} "
                f"{' '.join(SELF_TEST_ARGS)}), exit code {result['returncode']}"
            )
            if build["status"] in TERMINAL_STATUSES:
                record_build_outcome(build)

        save_builds(builds)
        return build

    return None


# Value-selection keyword vocabulary. Deliberately coarse (substring
# membership, not NLP): 13C's Improvement Proposal schema has no required
# structured risk/benefit fields, so proposals mostly carry plain-English
# rationale/description text written for a human reviewer. Structured
# fields (risk_level/risk_score, expected_benefit/benefit_score) are used
# whenever a proposal happens to carry them; this is only the fallback.
RISK_KEYWORDS = {
    "high": (
        "high risk", "risky", "breaking change", "irreversible", "destructive",
        "critical infrastructure", "security risk", "core code", "significant risk",
    ),
    "low": (
        "low risk", "safe", "isolated", "read-only", "non-breaking",
        "backward compatible", "backward-compatible", "minor change", "trivial change", "no risk",
    ),
}

BENEFIT_KEYWORDS = {
    "high": (
        "high value", "high impact", "significant improvement", "major improvement",
        "critical fix", "urgent", "essential", "big win", "high benefit",
    ),
    "low": (
        "low value", "low impact", "minor improvement", "nice to have",
        "cosmetic", "low priority", "low benefit",
    ),
}

LEVEL_SCORES = {"low": 1.0, "medium": 2.0, "high": 3.0}


def _level_to_score(value):
    default = LEVEL_SCORES["medium"]

    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        return LEVEL_SCORES.get(value.strip().lower(), default)

    return default


def _keyword_score(text, keyword_levels):
    lowered = text.lower()

    for level in ("high", "low"):
        if any(keyword in lowered for keyword in keyword_levels[level]):
            return LEVEL_SCORES[level]

    return LEVEL_SCORES["medium"]


def _find_originating_proposal(phase_id):
    for proposal in load_proposals():
        if proposal.get("roadmap_phase_id") == phase_id:
            return proposal

    return None


def _phase_value_score(phase):
    """Higher is better: expected-benefit score minus risk score, sourced
    from the phase's originating Improvement Proposal (13C) when one exists
    -- structured risk_level/expected_benefit fields if present, else a
    keyword scan of the proposal's own text, else a keyword scan of the
    phase's description for phases with no linked proposal at all (e.g.
    hand-authored roadmap phases)."""

    proposal = _find_originating_proposal(phase["id"]) or {}

    text = " ".join(str(value) for value in (
        phase.get("description", ""),
        proposal.get("rationale", ""),
        proposal.get("description", ""),
        proposal.get("suggested_action", ""),
    ))

    if "risk_level" in proposal or "risk_score" in proposal:
        risk = _level_to_score(proposal.get("risk_level", proposal.get("risk_score")))
    else:
        risk = _keyword_score(text, RISK_KEYWORDS)

    if "expected_benefit" in proposal or "benefit_score" in proposal:
        benefit = _level_to_score(proposal.get("expected_benefit", proposal.get("benefit_score")))
    else:
        benefit = _keyword_score(text, BENEFIT_KEYWORDS)

    return benefit - risk


def _select_next_phase():
    candidates = get_candidate_phases()

    if not candidates:
        return None

    # Highest value score wins; priority is the tie-breaker (matching
    # roadmap_engine.get_next_phase()'s plain-priority behavior) for phases
    # that score equally -- e.g. no linked proposal and no keyword hits on
    # either side nets a neutral 0 for every such candidate.
    return min(candidates, key=lambda p: (-_phase_value_score(p), p["priority"]))


def is_autonomous_mode_enabled():
    return bool((load(AUTONOMOUS_MODE_FILE) or {}).get("enabled"))


def enable_autonomous_mode():
    save(AUTONOMOUS_MODE_FILE, {"enabled": True})


def disable_autonomous_mode():
    save(AUTONOMOUS_MODE_FILE, {"enabled": False})


def is_self_modifying(project_path):
    try:
        resolved = Path(project_path).resolve()
    except OSError:
        return False

    if resolved == SELF_PROJECT_PATH:
        return True

    # A self-modifying build's project_path is (almost) never actually
    # SELF_PROJECT_PATH -- _create_isolated_self_clone() deliberately gives
    # it its own disposable clone instead (see that function's docstring).
    # Recognize those clones too, or every self-modifying build looks like a
    # normal one to callers that branch on this (e.g. deploy_build()).
    try:
        return resolved.parent == SELF_BUILD_WORKSPACE_ROOT.resolve()
    except OSError:
        return False


def _create_isolated_self_clone():
    workspace = SELF_BUILD_WORKSPACE_ROOT / uuid.uuid4().hex[:12]
    workspace.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "clone", "-q", str(SELF_PROJECT_PATH), str(workspace)],
        check=True,
    )
    return str(workspace)


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

    # Self-modifying builds all operate on this same repo's live working
    # directory (SELF_PROJECT_PATH), not an isolated clone -- running more
    # than one concurrently means two builds fighting over `git checkout`
    # on the same working tree. Confirmed live: this is what crashed the
    # first build's Claude process. Only one phase may be in flight at a
    # time, full stop, regardless of whether the roadmap's dependency graph
    # would otherwise allow another phase to start.
    for phase in in_progress:
        build = get_build(phase["build_id"])

        if build is None:
            continue

        # build_manager's own GENERATING handling (_run_generation, reused
        # as-is) runs generation through to SECURITY_REVIEW/DEPLOY_APPROVAL
        # in one synchronous call -- there's no separate at-rest GENERATING
        # state to intercept mid-step without editing build_manager.py
        # itself. DEPLOY_APPROVAL is the next state this loop actually
        # observes the build sitting in, so that's where the self-build test
        # suite runs: once, before a human is ever asked to approve deploy.
        # A failing run transitions the build straight to FAILED, which the
        # STOPPING_BUILD_STATUSES check just below then fails the phase for
        # exactly like any other build failure -- no new path around
        # ARCHITECTURE_APPROVED/DEPLOY_APPROVAL is added.
        if build["status"] == "DEPLOY_APPROVAL" and build.get("self_build_test_result") is None:
            test_result = _run_self_build_tests(build["project_path"])
            build = _apply_self_build_test_result(phase["build_id"], test_result) or build

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

        return {"action": "waiting_on_human", "phase_id": phase["id"], "build_id": phase["build_id"], "build_status": build["status"]}

    next_phase = _select_next_phase()

    if next_phase is None:
        return {"action": "nothing_to_do"}

    build = create_build(
        name=next_phase["id"],
        description=_build_description(next_phase),
        project_path=_create_isolated_self_clone(),
    )

    update_phase(next_phase["id"], status="in_progress", build_id=build["id"])

    return {"action": "started_phase", "phase_id": next_phase["id"], "build_id": build["id"]}
