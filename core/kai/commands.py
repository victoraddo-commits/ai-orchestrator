"""Rule-based phrase matching for Kai voice/text commands.

Deliberately not an LLM intent classifier -- a fixed table of phrases mapped
to existing functions, so a command either matches exactly (mod whitespace/
case/punctuation) or it doesn't. No new AI-calling code lives here.
"""

import re

from core.health import analyze as analyze_health
from core.ai import provider_health
from core.roadmap_manager import advance_roadmap
from core.build_learning import get_build_history
from core.roadmap_engine import get_next_phase


FAILURE_STATUSES = {"FAILED", "ROLLED_BACK"}


def _handle_health():
    return {
        "findings": analyze_health(),
        "provider_health": provider_health.get_all_quota_snapshots(),
    }


def _handle_improvements():
    # 13C (Improvement Proposal planner) depends on this phase and is not
    # built yet -- hook in via a safe import so this command starts working
    # the moment 13C lands, without requiring a change here.
    try:
        from core.kai.planner import generate_proposal
    except ImportError:
        return {"status": "pending_13c", "message": "13C improvement planner not yet available."}

    return generate_proposal()


def _handle_continue_roadmap():
    return advance_roadmap()


def _handle_review_failures():
    history = get_build_history()
    return [entry for entry in history if entry.get("status") in FAILURE_STATUSES]


def _handle_next_phase():
    return get_next_phase()


# (compiled pattern, handler, description)
COMMAND_PATTERNS = (
    (
        re.compile(r"^kai,\s*analyze\s+system\s+health\.?$", re.IGNORECASE),
        _handle_health,
        "Analyze system health and AI provider status.",
    ),
    (
        re.compile(r"^kai,\s*(?:find\s+improvements|create\s+an?\s+improvement\s+proposal)\.?$", re.IGNORECASE),
        _handle_improvements,
        "Find improvements / create an improvement proposal.",
    ),
    (
        re.compile(r"^kai,\s*continue\s+roadmap\.?$", re.IGNORECASE),
        _handle_continue_roadmap,
        "Advance the active roadmap.",
    ),
    (
        re.compile(r"^kai,\s*review\s+recent\s+failures\.?$", re.IGNORECASE),
        _handle_review_failures,
        "Review recent build failures/rollbacks.",
    ),
    (
        re.compile(r"^kai,\s*prepare\s+the\s+next\s+engineering\s+phase\.?$", re.IGNORECASE),
        _handle_next_phase,
        "Prepare the next engineering phase.",
    ),
)


def dispatch(text):
    cleaned = (text or "").strip()

    for pattern, handler, description in COMMAND_PATTERNS:
        if pattern.match(cleaned):
            try:
                result = handler()
            except Exception as e:
                return {"matched": True, "description": description, "result": None, "error": str(e)}

            return {"matched": True, "description": description, "result": result, "error": None}

    return {"matched": False, "description": None, "result": None, "error": f"No matching command pattern for: {text!r}"}
