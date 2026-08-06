"""Phase 12L: Autonomous Engineering Manager.

Drives the roadmap forward -- proposes, plans, and generates changes for
each remaining phase -- but never approves anything on its own behalf.
core/build_manager.py's ARCHITECTURE_APPROVED and DEPLOY_APPROVAL gates are
untouched: they still require an explicit human action via
POST /builds/{id}/approve-architecture and .../approve-deploy. This module
calls neither. That split is the entire point of this phase: the system may
propose, test, and deploy improvements to itself, but it does not get
unrestricted authority to rewrite its own core code without approval.

Autonomous mode defaults to Level 1 (observe + report). Starting the
roadmap-advance loop (Level 3+) is a deliberate human action -- setting
the autonomy level explicitly through PUT /api/autonomy/level or the
deprecated POST /roadmap/autonomous/enable shim -- not something that
activates just because this module is imported. See core/autonomy.py
for the full level catalog.
"""

import shutil
import subprocess
import uuid
from pathlib import Path

from core.memory import load, save
from core import autonomy as _autonomy
from core.autonomy import (
    # Re-exported so pre-13H callers (tests, internal helpers) keep working.
    is_autonomous_mode_enabled,
    enable_autonomous_mode,
    disable_autonomous_mode,
)
from core.roadmap_engine import get_candidate_phases, update_phase, load_roadmap as _load_roadmap_raw
from core.build_manager import (
    create_build,
    get_build,
    load_builds,
    save_builds,
    BUILD_TRANSITIONS,
    MAX_CONCURRENT_BUILDS,
    NON_TERMINAL_BUILD_STATUSES,
)
from core.lifecycle import transition
from core.build_learning import record_build_outcome, TERMINAL_STATUSES
# 13C's Improvement Proposal store -- the source of the risk/expected-benefit
# signal _phase_value_score() scores phases by. No cycle: core.kai.planner
# never imports core.roadmap_manager.
from core.kai.planner import load_proposals
from core.sandbox_manager import create_sandbox as _sandbox_create, self_build_repo_paths as _sb_repo_paths


SELF_PROJECT_PATH = Path(__file__).resolve().parent.parent

# V3: sandbox_manager handles the clone logic now — these paths are kept
# for backward compatibility with existing self_build_repo_paths() callers.
SELF_BUILD_WORKSPACE_ROOT = Path.home() / ".ai-orchestrator" / "sandboxes"
PLUGIN_PROJECT_PATH = Path("/project/src/ai-orchestrator-plugin")

ORCHESTRATOR_CLONE_DIRNAME = "ai-orchestrator"
PLUGIN_CLONE_DIRNAME = "ai-orchestrator-plugin"
PLUGIN_KEYWORD = "ai-orchestrator-plugin"

# 13H: replaced by core.autonomy's autonomy_level.json (6 numbered
# levels). The old binary file's name is still referenced here for
# backwards-compatible migration reads only -- no code in this module
# writes to it any more; see core.autonomy._migrate_from_legacy.
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
#
# -m "not external_api": confirmed live 2026-08-01, this exact gate failed
# FOUR separate self-builds in one night on the same two OpenRouter-hitting
# tests (test_openrouter_claude_sonnet_coding_path_against_real_api,
# test_call_openrouter_against_real_api) -- real external-API flakiness with
# zero relation to whether the generated code is correct. A test that
# depends on a third party's uptime has no business gating an automated
# deploy decision.
#
# Deliberately NOT "-m not integration": a security review of the first cut
# of this fix (which used "not integration") caught that test_sandbox.py and
# test_security_scanner.py are ALSO marked integration -- those exercise
# real Docker sandbox isolation and the real security scanners, exactly the
# checks that must NOT be silently skipped before a self-modifying deploy.
# external_api (pytest.ini) is a narrower, strict subset of integration:
# only tests that call a real third-party API get it, so those two files
# (and test_deployment_manager.py's local-Docker-only test) keep gating
# deploys while the genuinely third-party-dependent tests don't. All of
# these tests still run in the normal (non-self-build) `pytest tests/`
# suite -- only the self-build gate excludes external_api ones.
SELF_TEST_ARGS = ["tests/", "-m", "not external_api"]
SELF_TEST_TIMEOUT = 300
SELF_TEST_OUTPUT_LIMIT = 10000

# advance_roadmap() only ever creates self-modifying builds (isolated clones
# of SELF_PROJECT_PATH), so every build it tracks via phase["build_id"] is,
# by construction, a self-modifying build -- no separate is_self_modifying()
# check is needed at the test-execution hook below.


def _self_test_executable():
    return SELF_PROJECT_PATH / ".venv" / "bin" / "pytest"


def _sync_clone_with_live_repo(repo_path):
    # Root-caused live 2026-08-01: three separate self-builds (17A, 17K x2)
    # failed their test gate on the exact same already-fixed test bug,
    # because each build's isolated clone was made (git clone from
    # SELF_PROJECT_PATH) before the fix landed on live main and never
    # refreshed afterward -- wasting a full generate/review/test cycle each
    # time on a false positive, not a real defect in the generated code.
    # `origin` in every clone is SELF_PROJECT_PATH itself (a local path, see
    # _create_isolated_self_clone), so fetch+merge here always picks up
    # whatever is current on live main right before the test run that
    # gates deploy approval -- the same "don't trust a stale base" principle
    # 17A applies at merge time, applied one step earlier at test time too.
    try:
        fetch = subprocess.run(
            ["git", "fetch", "-q", "origin"],
            cwd=repo_path, capture_output=True, text=True, timeout=60,
        )
        if fetch.returncode != 0:
            return False, (fetch.stdout or "") + (fetch.stderr or "")

        merge = subprocess.run(
            ["git", "merge", "--no-edit", "-q", "origin/main"],
            cwd=repo_path, capture_output=True, text=True, timeout=60,
        )
        if merge.returncode != 0:
            subprocess.run(["git", "merge", "--abort"], cwd=repo_path, capture_output=True, text=True, timeout=30)
            return False, (merge.stdout or "") + (merge.stderr or "")
        return True, None
    except (OSError, subprocess.TimeoutExpired) as error:
        return False, str(error)


def _run_self_build_tests(project_path):
    # A dual-repo workspace's project_path is the parent directory holding
    # both clones -- this repo's own test suite lives in the orchestrator
    # clone, so that's where pytest must run. Single-repo workspaces are the
    # repo root already (unchanged behavior).
    repo_path = orchestrator_repo_path(project_path)

    synced, sync_error = _sync_clone_with_live_repo(repo_path)
    if not synced:
        return {
            "passed": False,
            "returncode": None,
            "output": f"Stale base: could not sync build clone with live main before testing -- {sync_error}",
        }

    try:
        completed = subprocess.run(
            [str(_self_test_executable())] + SELF_TEST_ARGS,
            cwd=repo_path,
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
            _invalidate_pending_deploy_approval(build_id, build["failure_reason"])

        save_builds(builds)
        return build

    return None


def _invalidate_pending_deploy_approval(build_id, reason):
    # build_manager._create_deploy_approval runs inside advance_builds(),
    # which run_cycle() always calls just before advance_roadmap() (see
    # core.orchestrator_cycle) -- so a build's deploy Approval can already
    # exist by the time this self-test gate fails it, in the very same
    # cycle. Reject it directly via transition_request() rather than
    # core.approval.reject(), which would try to re-transition the build
    # through reject_deploy() -- already FAILED by this different,
    # legitimate path -- and raise InvalidTransition. This just keeps the
    # Approval Center from showing a stale "pending" request for a build
    # that's already dead.
    from core.approval import load_requests, transition_request

    for request in load_requests():
        if (
            request.get("build_id") == build_id
            and request.get("approval_type") == "deploy"
            and request.get("status") == "pending"
        ):
            transition_request(request["id"], "rejected", note=reason)


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


def is_autonomous_mode_enabled_local():
    # Kept as a private symbol for callers inside this module that used
    # to read the module-level function; the public export
    # ``is_autonomous_mode_enabled`` is the shim imported from
    # core.autonomy (see the top-of-file import).
    return _autonomy.is_autonomous_mode_enabled()


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
    # V3: sandboxes are at SANDBOX_ROOT / build_id / ai-orchestrator, so the
    # grandparent (not parent) must match the workspace root.
    try:
        ws_root = SELF_BUILD_WORKSPACE_ROOT.resolve()
        return resolved == ws_root or ws_root in resolved.parents
    except OSError:
        return False


def phase_requires_plugin(phase):
    """Whether a self-modifying phase's work needs the CloudCLI plugin repo
    too. Explicit `requires_plugin: true` on the phase wins; otherwise fall
    back to a case-insensitive scan of the phase's own text for the plugin
    repo's name -- so historical/implicit phases (13G's Control Center tab,
    13J's dashboard, ...) get the dual-repo workspace without anyone
    hand-editing roadmap.json."""

    if phase.get("requires_plugin") is True:
        return True

    text = " ".join([
        str(phase.get("name", "")),
        str(phase.get("description", "")),
        " ".join(str(c) for c in phase.get("completion_criteria") or []),
    ])

    return PLUGIN_KEYWORD in text.lower()


def is_dual_repo_workspace(project_path):
    """True when `project_path` is a dual-repo workspace parent: a plain
    directory holding both the ai-orchestrator and ai-orchestrator-plugin
    clones as siblings (the layout _create_isolated_self_clone(include_
    plugin=True) produces)."""

    workspace = Path(project_path)
    return (
        (workspace / ORCHESTRATOR_CLONE_DIRNAME / ".git").exists()
        and (workspace / PLUGIN_CLONE_DIRNAME / ".git").exists()
    )


def self_build_repo_paths(project_path):
    """V3: delegated to sandbox_manager."""
    return _sb_repo_paths(project_path)


def orchestrator_repo_path(project_path):
    """Where the ai-orchestrator codebase itself lives inside a build
    workspace -- the workspace root for single-repo builds, the
    ai-orchestrator sibling clone for dual-repo ones."""

    if is_dual_repo_workspace(project_path):
        return str(Path(project_path) / ORCHESTRATOR_CLONE_DIRNAME)

    return str(project_path)


def _copy_memory_snapshot(repo_dir):
    # memory/ is entirely gitignored (mutable runtime state -- tracking it
    # would cause spurious diffs/merge conflicts on every build), so a plain
    # `git clone` leaves a self-build workspace with no memory/*.json files
    # at all. Confirmed live 2026-07-29: 13T's task was to analyze
    # memory/ai_usage_history.json and record a lesson in
    # memory/learning_lessons.json -- with neither file present, every
    # coding-capable provider failed on "File not found" before even
    # attempting the task. Copy a point-in-time snapshot of the live repo's
    # memory/ into the fresh clone so read/analyze tasks have real data to
    # work with. Still gitignored inside the clone (the cloned .gitignore
    # already excludes it), so this can never leak into a build's own
    # diff/commit -- purely a read-time convenience, not a live/writable
    # link back to the real state store.
    src = SELF_PROJECT_PATH / "memory"
    if src.is_dir():
        shutil.copytree(src, Path(repo_dir) / "memory", dirs_exist_ok=True)


def _create_isolated_self_clone(include_plugin=False):
    workspace = SELF_BUILD_WORKSPACE_ROOT / uuid.uuid4().hex[:12]
    workspace.parent.mkdir(parents=True, exist_ok=True)

    if not include_plugin:
        # Single-repo workspace: exactly today's behavior -- the workspace
        # itself is the clone, and project_path/--dir points straight at it.
        subprocess.run(
            ["git", "clone", "-q", str(SELF_PROJECT_PATH), str(workspace)],
            check=True,
        )
        _copy_memory_snapshot(workspace)
        return str(workspace)

    # Dual-repo workspace: the workspace is a plain parent directory with
    # both repos cloned as siblings, so a single --dir/project_path gives
    # the coding agent simultaneous read/write access to both trees. The
    # parent itself is deliberately NOT a git repo.
    workspace.mkdir()
    subprocess.run(
        ["git", "clone", "-q", str(SELF_PROJECT_PATH), str(workspace / ORCHESTRATOR_CLONE_DIRNAME)],
        check=True,
    )
    _copy_memory_snapshot(workspace / ORCHESTRATOR_CLONE_DIRNAME)
    subprocess.run(
        ["git", "clone", "-q", str(PLUGIN_PROJECT_PATH), str(workspace / PLUGIN_CLONE_DIRNAME)],
        check=True,
    )
    return str(workspace)


def _build_description(phase):
    criteria = "\n".join(f"- {c}" for c in phase.get("completion_criteria", []))
    description = (
        f"{phase.get('description', '')}\n\n"
        f"This is a self-modifying change to the ai-orchestrator project itself "
        f"(roadmap phase {phase['id']}). Completion criteria:\n{criteria}"
    )

    if phase_requires_plugin(phase):
        description += (
            f"\n\nThis build's working directory contains two sibling git "
            f"repositories: {ORCHESTRATOR_CLONE_DIRNAME}/ (the orchestrator "
            f"backend) and {PLUGIN_CLONE_DIRNAME}/ (the CloudCLI plugin "
            f"frontend). Make your changes in whichever repositories the work "
            f"requires and commit in each repository you touch."
        )

    return description


def _phase_build_uses_isolated_clone(build):
    """A self-modifying build is safe to run concurrently with others only
    when its project_path is an isolated clone under SELF_BUILD_WORKSPACE_ROOT
    (K3's per-build isolation). A build whose project_path resolves to
    SELF_PROJECT_PATH itself is the old, unsafe pattern -- it would share
    the live working directory with anything else in flight, so it must
    still be serialized (the pre-17A single-in-flight behavior, kept for
    exactly this case)."""

    project_path = build.get("project_path")
    if not project_path:
        return False

    try:
        resolved = Path(project_path).resolve()
    except OSError:
        return False

    if resolved == SELF_PROJECT_PATH:
        return False

    # V3: sandboxes are at SANDBOX_ROOT / build_id / ai-orchestrator
    try:
        ws_root = SELF_BUILD_WORKSPACE_ROOT.resolve()
        return ws_root in resolved.parents
    except OSError:
        return False


def _requires_exclusive(phase, build):
    """A phase is exclusive -- must run with nothing else in flight -- if
    either the roadmap entry carries an explicit ``"exclusive": true``
    (human-settable escape hatch for a phase known unsafe to parallelize,
    e.g. 17A itself, which changes the concurrency rules it would otherwise
    run under), or the build's project_path resolves to SELF_PROJECT_PATH
    directly rather than an isolated clone (the exact unsafe condition the
    pre-17A single-in-flight guard existed to prevent).

    build may be None -- e.g. when deciding whether to start a new phase
    that has no build yet, only the phase's own ``"exclusive"`` flag can
    be checked at that point."""

    if phase.get("exclusive") is True:
        return True

    if build is not None and not _phase_build_uses_isolated_clone(build):
        return True

    return False


# Ordering of event actions by significance -- when advance_roadmap()
# collects per-phase events across all in-flight phases in one cycle, the
# top-level "action" it returns is the highest-significance event observed.
# Preserves the pre-17A single-return-value contract for consumers that
# only inspect ``result["action"]`` while still exposing every per-phase
# outcome for anyone that wants them via ``result["events"]``.
_EVENT_SIGNIFICANCE = {
    "phase_failed": 4,
    "phase_completed": 3,
    "started_phase": 2,
    "waiting_on_human": 1,
    "nothing_to_do": 0,
}


def _select_top_event(events):
    if not events:
        return {"action": "nothing_to_do"}
    return max(events, key=lambda e: _EVENT_SIGNIFICANCE.get(e.get("action"), -1))


def _process_in_progress_phase(phase):
    """Advance a single in-flight phase as far as this cycle can without
    starting a new phase. Returns the per-phase event dict (same vocabulary
    as pre-17A: phase_completed / phase_failed / waiting_on_human), or None
    if the phase has no live build to look at (already-cleaned-up state)."""

    build = get_build(phase["build_id"])
    if build is None:
        return None

    # build_manager's own GENERATING handling (_run_generation, reused
    # as-is) runs generation through to SECURITY_REVIEW/
    # WAITING_FOR_DEPLOY_APPROVAL in one synchronous call -- there's no
    # separate at-rest GENERATING state to intercept mid-step without
    # editing build_manager.py itself. WAITING_FOR_DEPLOY_APPROVAL is the
    # next state this loop actually observes the build sitting in, so
    # that's where the self-build test suite runs: once, before a human
    # is ever asked to approve deploy.
    if build["status"] == "WAITING_FOR_DEPLOY_APPROVAL" and build.get("self_build_test_result") is None:
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

    return {
        "action": "waiting_on_human",
        "phase_id": phase["id"],
        "build_id": phase["build_id"],
        "build_status": build["status"],
    }


def advance_roadmap():
    # 13H gate: creating branches / builds is a Level-3-or-higher
    # activity. V3: same gate preserved.
    if not _autonomy.can_create_branches_and_builds():
        return {"action": "disabled"}

    roadmap = _load_roadmap_raw()

    # V3: stale roadmap protection — if a phase references a build_id
    # that doesn't exist in builds.json, auto-fail the phase.
    all_builds = load_builds(include_terminal=True)
    all_build_ids = {b.get("id") for b in all_builds}
    events = []

    for phase in roadmap.get("phases", []):
        bid = phase.get("build_id")
        if bid and phase.get("status") == "in_progress" and bid not in all_build_ids:
            update_phase(
                phase["id"],
                status="failed",
                failure_reason=(
                    f"Build {bid} missing from builds.json "
                    f"(deleted or stale reference)"
                ),
            )
            events.append({
                "action": "stale_reference_failed",
                "phase_id": phase["id"],
                "missing_build_id": bid,
            })

    in_progress_phases = [
        p for p in roadmap["phases"]
        if p["status"] == "in_progress" and p.get("build_id")
        and p.get("build_id") in all_build_ids  # V3: skip stale refs already handled
    ]

    events = []
    still_active_phases = []
    for phase in in_progress_phases:
        event = _process_in_progress_phase(phase)
        if event is None:
            continue
        events.append(event)
        if event["action"] not in ("phase_completed", "phase_failed"):
            still_active_phases.append((phase, get_build(phase["build_id"])))

    any_exclusive_in_flight = any(
        _requires_exclusive(phase, build) for phase, build in still_active_phases
    )
    any_in_flight = bool(still_active_phases)

    if any_exclusive_in_flight:
        return _finalize(events)

    spawned = 0
    spawned_ids = set()  # V3 fix: prevent same-phase re-selection within this cycle
    while len(still_active_phases) + spawned < MAX_CONCURRENT_BUILDS:
        next_phase = _select_next_phase()
        if next_phase is None:
            break

        if next_phase.get("exclusive") is True and (any_in_flight or spawned > 0):
            break

        # V3 fix: if _select_next_phase returns a phase we already spawned
        # this cycle (possible when update_phase hasn't persisted or source
        # is out of sync), skip it so we don't create runaway sandboxes.
        if next_phase["id"] in spawned_ids:
            # Mark as in_progress so the next iteration skips it even if
            # _select_next_phase returns it again.
            update_phase(next_phase["id"], status="in_progress",
                         _note="skipped: already spawned this cycle")
            continue

        # V3 fix: check for existing build BEFORE creating sandbox.
        # create_build() has duplicate prevention, but the sandbox was
        # being created regardless — now we short-circuit early.
        all_nonterminal = load_builds(include_terminal=False)
        existing = next(
            (b for b in all_nonterminal
             if b.get("name") == next_phase["id"]
             and b.get("status") in NON_TERMINAL_BUILD_STATUSES),
            None,
        )
        if existing is not None:
            # Already has an active build — mark phase but don't spawn
            update_phase(next_phase["id"], status="in_progress",
                         build_id=existing["id"])
            spawned_ids.add(next_phase["id"])
            spawned += 1
            any_in_flight = True
            continue

        # V3: use sandbox_manager for isolated workspace
        project_path = _sandbox_create(
            build_id=next_phase["id"],
            include_plugin=phase_requires_plugin(next_phase),
        )

        build = create_build(
            name=next_phase["id"],
            description=_build_description(next_phase),
            project_path=project_path,
        )

        update_phase(next_phase["id"], status="in_progress", build_id=build["id"])
        events.append({"action": "started_phase", "phase_id": next_phase["id"], "build_id": build["id"]})
        spawned_ids.add(next_phase["id"])
        spawned += 1
        any_in_flight = True

    return _finalize(events)


def _finalize(events):
    top = dict(_select_top_event(events))
    top["events"] = events
    return top
