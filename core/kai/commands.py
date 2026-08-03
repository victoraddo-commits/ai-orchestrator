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
from core.kai.planner import generate_proposal


FAILURE_STATUSES = {"FAILED", "ROLLED_BACK"}


def _handle_health():
    return {
        "findings": analyze_health(),
        "provider_health": provider_health.get_all_quota_snapshots(),
    }


def _handle_improvements():
    return generate_proposal()


def _handle_continue_roadmap():
    return advance_roadmap()


def _handle_review_failures():
    history = get_build_history()
    return [entry for entry in history if entry.get("status") in FAILURE_STATUSES]


def _handle_next_phase():
    return get_next_phase()


# (compiled pattern, handler, description)
# 17V: long-term memory handlers
def _handle_remember(text):
    from core.kai.conversation import remember_fact
    # Store the fact under a key derived from the first few words
    key = text[:60].strip()
    remember_fact(key, text)
    return {"reply": f"Got it — I'll remember that: {text[:200]}"}


def _handle_always_never(text):
    from core.kai.conversation import remember_directive
    remember_directive(text)
    return {"reply": f"Understood — directive stored: {text[:200]}"}


def _handle_forget(text):
    from core.kai.conversation import _read_long_term
    store = _read_long_term()
    # Try to find a matching fact
    text_lower = text.lower().strip()
    for fact in store.get("facts", []):
        if text_lower in fact.get("value", "").lower() or text_lower in fact.get("key", "").lower():
            store["facts"] = [f for f in store["facts"] if f != fact]
            from core.kai.conversation import _write_long_term
            _write_long_term(store)
            return {"reply": f"Removed: {fact['key']}"}
    return {"reply": f"I don't have anything matching '{text[:100]}'."}


def _handle_recall():
    from core.kai.conversation import get_long_term_context
    ctx = get_long_term_context()
    if not ctx:
        return {"reply": "I don't remember anything specific yet. Say 'Kai, remember that...' to store something."}
    return {"reply": f"Here's what I remember:\n\n{ctx}"}


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

# conversational aliases
COMMAND_PATTERNS += (
    (
        re.compile(r"^(?:kai,\s*)?(?:roadmap status|where are we|what is the roadmap status|current progress)\.?$", re.IGNORECASE),
        lambda: __import__("core.api", fromlist=["roadmap_endpoint"]).roadmap_endpoint(),
        "Show current roadmap status.",
    ),
    (
        re.compile(r"^(?:kai,\s*)?(?:what should we do next|next step|what is next)\.?$", re.IGNORECASE),
        _handle_next_phase,
        "Show next engineering phase.",
    ),
    # 17V: long-term memory commands
    (
        re.compile(r"^(?:kai,\s*)?remember\s+that\s+(.+)$", re.IGNORECASE | re.DOTALL),
        lambda match: _handle_remember(str(match.group(1)).strip()),
        "Remember that <fact> — store in Kai's long-term memory.",
    ),
    (
        re.compile(r"^(?:kai,\s*)?(?:always|never)\s+(.+)$", re.IGNORECASE | re.DOTALL),
        lambda match: _handle_always_never(str(match.group(0)).strip()),
        "Always/Never <directive> — store an operator directive.",
    ),
    (
        re.compile(r"^(?:kai,\s*)?(?:forget|remove)\s+(?:that\s+)?(.+)$", re.IGNORECASE | re.DOTALL),
        lambda match: _handle_forget(str(match.group(1)).strip()),
        "Forget <fact> — remove from long-term memory.",
    ),
    (
        re.compile(r"^(?:kai,\s*)?(?:what do you remember|recall|long-term memory)\.?$", re.IGNORECASE),
        lambda match: _handle_recall(),
        "What do you remember? — show long-term memory contents.",
    ),
)
