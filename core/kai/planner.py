"""Phase 13C: Kai Strategic Planner.

Aggregates roadmap state, system health, recent build failures, and AI
provider usage/quota, then asks the existing core.ai.ai_router.delegate()
(no new AI-calling code) to synthesize Improvement Proposal objects -- a
lifecycle.py-based object type (see core/lifecycle.py, already used by
core/build_manager.py for builds), persisted to
memory/improvement_proposals.json via core.memory (atomic, locked writes
courtesy of core/memory_manager.py).

Proposals are strictly advisory: generate_proposal() never calls
core.roadmap_engine.add_phase() or creates a build. Turning an *approved*
proposal into roadmap work is a separate, explicit action --
promote_proposal() -- and even that only calls add_phase() with its default
status of "proposed" (per 13A's design), so a promoted proposal still waits
on a human (or an explicit 13B command) to move it to "pending" before
core.roadmap_manager's autonomous loop will ever touch it.
"""

import json
import re

from core.memory import load, save
from core.lifecycle import new_object, transition, InvalidTransition
from core.roadmap_engine import load_roadmap, get_progress_summary, add_phase
from core.health import analyze as analyze_health
from core.build_learning import get_build_history
from core.build_manager import load_builds
from core.ai.ai_router import delegate, get_usage_history, AllProvidersFailed
from core.ai import provider_health
from core import autonomy as _autonomy


PROPOSALS_FILE = "improvement_proposals.json"

# Same set commands.py uses for "review recent failures" -- kept local
# rather than imported to avoid coupling planner.py's signal gathering to
# 13B's command table.
FAILURE_STATUSES = {"FAILED", "ROLLED_BACK"}

# Only recent entries are worth feeding to the LLM -- older history is
# already reflected in build_learning's aggregate success rates.
RECENT_BUILD_FAILURES_LIMIT = 20
RECENT_USAGE_FAILURES_LIMIT = 20

# proposed -> under_review -> approved/rejected -> implemented, exactly as
# specified for 13C. "rejected" and "implemented" are terminal; nothing
# resurrects a rejected proposal or revisits an implemented one.
PROPOSAL_TRANSITIONS = {
    "proposed": ["under_review"],
    "under_review": ["approved", "rejected"],
    "approved": ["implemented"],
    "rejected": [],
    "implemented": [],
}


def load_proposals():
    proposals = load(PROPOSALS_FILE)

    if not isinstance(proposals, list):
        proposals = []

    return proposals


def save_proposals(proposals):
    save(PROPOSALS_FILE, proposals)


def list_proposals(status=None):
    proposals = load_proposals()

    if status is None:
        return proposals

    return [p for p in proposals if p.get("status") == status]


def get_proposal(proposal_id):
    for proposal in load_proposals():
        if proposal.get("id") == proposal_id:
            return proposal

    return None


def update_proposal_status(proposal_id, new_status, note=None):
    proposals = load_proposals()

    for proposal in proposals:
        if proposal.get("id") == proposal_id:
            transition(proposal, new_status, PROPOSAL_TRANSITIONS, note=note)
            save_proposals(proposals)
            return proposal

    raise ValueError(f"Unknown proposal id: {proposal_id!r}")


def gather_signals():
    return _gather_signals()


def _gather_signals():
    remaining_work = [
        {"id": p["id"], "name": p["name"], "status": p["status"]}
        for p in load_roadmap()["phases"]
        if p["status"] != "completed"
    ]

    build_history = get_build_history()
    recent_failures = [
        {"build_id": e.get("build_id"), "name": e.get("name"), "template": e.get("template"), "failure_reason": e.get("failure_reason")}
        for e in build_history
        if e.get("status") in FAILURE_STATUSES
    ][-RECENT_BUILD_FAILURES_LIMIT:]

    usage_history = get_usage_history()
    recent_usage_failures = [
        {"provider": e.get("provider"), "task_type": e.get("task_type"), "error": e.get("error")}
        for e in usage_history
        if not e.get("success")
    ][-RECENT_USAGE_FAILURES_LIMIT:]

    return {
        "roadmap_progress": get_progress_summary(),
        "remaining_roadmap_work": remaining_work,
        "health_findings": analyze_health(),
        "recent_build_failures": recent_failures,
        "recent_ai_usage_failures": recent_usage_failures,
        "provider_quota": provider_health.get_all_quota_snapshots(),
        "application_builds": _application_build_signals(),
    }


# 17J: chat-triggered application builds (core/api.py's POST /kai/chat) are
# ordinary, non-self-modifying builds -- they don't appear in
# remaining_roadmap_work at all (that's roadmap phases only), so without
# this, a chat question like "how's the website build going" had nothing
# real to ground an answer in. Local import of is_self_modifying to avoid
# the existing core.roadmap_manager <-> core.kai.planner import cycle
# (roadmap_manager already imports load_proposals from this module).
_APP_BUILD_TERMINAL_STATUSES = {"COMPLETED", "FAILED", "ROLLED_BACK"}
RECENT_APPLICATION_BUILDS_LIMIT = 10


def _application_build_signals():
    from core.roadmap_manager import is_self_modifying

    app_builds = [
        b for b in load_builds()
        if not is_self_modifying(b.get("project_path", ""))
    ]

    active = [b for b in app_builds if b.get("status") not in _APP_BUILD_TERMINAL_STATUSES]
    recent_terminal = [b for b in app_builds if b.get("status") in _APP_BUILD_TERMINAL_STATUSES][-RECENT_APPLICATION_BUILDS_LIMIT:]

    return [
        {"id": b.get("id"), "name": b.get("name"), "status": b.get("status"), "project_path": b.get("project_path")}
        for b in active + recent_terminal
    ]


# 2026-08-02 operator directive: "tell kai to prioritise everything that
# will help qwen3 work better" -- qwen3_coding (self-hosted Qwen3-Coder on a
# pay-per-use RunPod GPU) is the roadmap's primary builder as of the same
# day (see core.ai.ai_router.ROLE_PROVIDERS["coding"]). Surfaced in every
# proposal-generation cycle rather than a one-off chat message, since
# Kai's chat history isn't fed into these signals/this prompt -- a durable
# directive here is the only way it actually shapes future proposals.
OPERATOR_PRIORITY = (
    "Operator priority: when weighing which improvements to propose, favor "
    "ones that make qwen3_coding (the self-hosted Qwen3-Coder route, now "
    "the roadmap's primary builder, billed per-second on a RunPod GPU) "
    "more reliable or more capable -- e.g. monitoring its success/failure "
    "rate in recent_ai_usage_failures, hardening its opencode tool-calling "
    "integration, expanding what it can safely handle, or catching "
    "regressions before they cost pay-per-use GPU time. This is a "
    "standing priority, not a signal that only applies when qwen3_coding "
    "appears in the signals below."
)


def _build_prompt(signals):
    return (
        "You are Kai, the AI Orchestrator's continuous-improvement planner. "
        "Do NOT write, edit, or modify any files, and do NOT run commands that "
        "change anything -- only analyze the signals below and respond with text.\n\n"
        f"{OPERATOR_PRIORITY}\n\n"
        f"Current system signals (JSON):\n{json.dumps(signals, indent=2, default=str)}\n\n"
        "Based on these signals, propose 0-3 concrete improvements to the "
        "system (e.g. addressing a recurring build failure, a health "
        "finding, an AI provider quota/reliability problem, or a gap in the "
        "roadmap). If nothing currently warrants a proposal, propose none.\n\n"
        "Respond with ONLY a JSON array (no prose, no markdown fences). Each "
        "element must be an object with exactly these keys:\n"
        '- "title": short string\n'
        '- "description": the problem or opportunity, in plain terms\n'
        '- "suggested_action": the concrete change to make\n'
        '- "rationale": why this matters, referencing the signals above\n'
        '- "source_signals": array of short strings naming which signals '
        'motivated this (e.g. "health:docker_unavailable", '
        '"build_failures:fastapi_template")\n'
        '- "target_roadmap_phase_draft": either null, or an object with '
        '"name", "description", "dependencies" (array of existing roadmap '
        'phase ids this would depend on), and "priority" (integer), '
        "suitable for a future core.roadmap_engine.add_phase() call\n"
        "Respond with [] if no proposal is warranted."
    )


def _strip_code_fence(text):
    stripped = text.strip()
    match = re.match(r"^```(?:json)?\s*(.*?)\s*```$", stripped, re.DOTALL)
    return match.group(1) if match else stripped


def _parse_proposals(response_text):
    cleaned = _strip_code_fence(response_text or "")

    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\[.*\]", cleaned, re.DOTALL)
        if match is None:
            return None
        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError:
            return None

    return data if isinstance(data, list) else None


def _new_proposal(item, provider):
    return new_object(
        "proposed",
        title=item.get("title") or "Untitled improvement",
        description=item.get("description", ""),
        suggested_action=item.get("suggested_action", ""),
        rationale=item.get("rationale", ""),
        source_signals=item.get("source_signals") or [],
        target_roadmap_phase_draft=item.get("target_roadmap_phase_draft"),
        synthesized_by=provider,
        roadmap_phase_id=None,
    )


def generate_proposal():
    # 13H gate: writing an improvement proposal (persisting to
    # memory/improvement_proposals.json) is a Level-2-or-higher activity.
    # Below Level 2, the planner is limited to "observe + report"; it
    # can still be *invoked* (this function returns rather than raising
    # so that operator UI paths hitting a rate-limited /kai/commands do
    # not surface a scary traceback) but no proposal is persisted.
    if not _autonomy.can_create_proposals():
        current = _autonomy.get_autonomy_level()
        return {
            "status": "disabled",
            "reason": (
                f"autonomy level {current['level']} does not permit creating proposals; "
                "raise to level 2 or higher"
            ),
            "created": [],
        }

    signals = _gather_signals()

    try:
        result = delegate(_build_prompt(signals), task_type="planning")
    except AllProvidersFailed as error:
        return {"status": "error", "message": str(error), "created": []}

    parsed = _parse_proposals(result["response"])

    if parsed is None:
        # The provider didn't return parseable JSON -- keep its analysis
        # instead of silently discarding it, wrapped as a single proposal.
        parsed = [{
            "title": "Improvement analysis (unstructured response)",
            "description": result["response"],
            "suggested_action": "",
            "rationale": "",
            "source_signals": [],
            "target_roadmap_phase_draft": None,
        }]

    proposals = load_proposals()
    created = [_new_proposal(item, result["provider"]) for item in parsed]
    proposals.extend(created)
    save_proposals(proposals)

    return {"status": "ok", "provider": result["provider"], "created": created}


def promote_proposal(proposal_id, phase_id):
    # 13H gate: promoting an approved proposal into a new roadmap phase
    # is "phase-generation logic" per the spec's Level 5 row. Below
    # Level 5, an approved proposal can still exist as advisory text --
    # the operator must add the phase manually (or raise autonomy to
    # Level 5 for continuous roadmap management). ARCHITECTURE_APPROVED
    # / DEPLOY_APPROVAL on any resulting build are ALWAYS still required;
    # Level 5 does not bypass those.
    if not _autonomy.can_manage_roadmap_continuously():
        current = _autonomy.get_autonomy_level()
        raise PermissionError(
            f"autonomy level {current['level']} does not permit promoting "
            "proposals into new roadmap phases; raise to level 5"
        )

    proposal = get_proposal(proposal_id)

    if proposal is None:
        raise ValueError(f"Unknown proposal id: {proposal_id!r}")

    if proposal.get("status") != "approved":
        raise InvalidTransition(
            f"cannot promote proposal {proposal_id!r} with status "
            f"{proposal['status']!r} (must be 'approved')"
        )

    draft = proposal.get("target_roadmap_phase_draft") or {}

    # status intentionally omitted -- add_phase() defaults to "proposed",
    # never "pending", so this new phase still waits on a human before
    # core.roadmap_manager's autonomous loop will act on it.
    phase = add_phase(
        id=phase_id,
        name=draft.get("name") or proposal["title"],
        description=draft.get("description") or proposal["description"],
        dependencies=draft.get("dependencies") or [],
        priority=draft.get("priority") if draft.get("priority") is not None else 100,
    )

    proposals = load_proposals()
    for p in proposals:
        if p.get("id") == proposal_id:
            p["roadmap_phase_id"] = phase_id
            transition(p, "implemented", PROPOSAL_TRANSITIONS, note=f"promoted to roadmap phase {phase_id}")
            break
    save_proposals(proposals)

    return {"proposal": get_proposal(proposal_id), "roadmap_phase": phase}
