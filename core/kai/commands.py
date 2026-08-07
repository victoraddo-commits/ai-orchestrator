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


def _handle_add_task(description):
    """Add a task to the roadmap as a proposed phase.  Assigns a unique
    task-id prefixed with 'TK-' so it never collides with numbered phases."""
    import json, os, uuid
    from datetime import datetime, timezone

    roadmap_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "roadmap.json")

    with open(roadmap_path) as f:
        data = json.load(f)

    # Generate a unique task ID
    task_id = f"TK-{uuid.uuid4().hex[:8]}"

    new_phase = {
        "id": task_id,
        "name": description[:120],
        "description": description,
        "status": "proposed",
        "dependencies": [],
        "completion_criteria": [],
        "tests_required": False,
        "priority": 99,
        "note": f"Auto-added by operator via Kai command on {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC",
    }

    data["phases"].append(new_phase)

    with open(roadmap_path + ".tmp", "w") as f:
        json.dump(data, f, indent=2)
    os.replace(roadmap_path + ".tmp", roadmap_path)

    return {"reply": f"Added to roadmap: {task_id} — \"{description[:100]}\""}


def _handle_list_tasks():
    """List all proposed and pending tasks from the roadmap."""
    import json, os

    roadmap_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "roadmap.json")
    with open(roadmap_path) as f:
        data = json.load(f)

    open_tasks = [p for p in data["phases"] if p["status"] in ("proposed", "pending")]
    open_tasks.sort(key=lambda p: (p["status"], p.get("priority", 99)))

    if not open_tasks:
        return {"reply": "No open tasks. Everything is either completed, in progress, or failed."}

    lines = []
    for t in open_tasks:
        icon = {"proposed": "💡", "pending": "⏳"}.get(t["status"], "")
        lines.append(f"  {icon} {t['id']} — {t.get('name', t.get('description', ''))[:100]}")

    return {"reply": f"Open tasks ({len(open_tasks)}):\n" + "\n".join(lines)}


# 13K: Voice/Text workforce command handlers
def _handle_list_workers():
    from core.ai_provider import list_providers
    from core.ai.provider_health import get_all_snapshots

    providers = list_providers()
    quota_data = get_all_snapshots() or {}
    if not isinstance(quota_data, dict):
        quota_data = {}

    lines = []
    for name, info in sorted(providers.items()):
        enabled = info.get("enabled", True)
        available = info.get("available", False)
        caps = ", ".join(info.get("capabilities", []))
        qs = quota_data.get(name, {})
        qstatus = qs.get("status", "ok") if isinstance(qs, dict) else "ok"

        status_icon = "⏸" if not enabled else "✅" if available else "❌"
        quota_icon = {"ok": "🟢", "error": "🟡", "quota_exceeded": "🔴"}.get(qstatus, "⚪")
        lines.append(f"{status_icon}{quota_icon} **{name}** — {caps} ({info.get('cost_tier','?')})")

    return {"reply": f"AI Workforce ({len(lines)} workers):\n" + "\n".join(lines)}


def _handle_provider_ranking():
    from core.weighted_routing import get_weighted_routing_report
    report = get_weighted_routing_report()
    sorted_weights = report.get("sorted", [])
    if not sorted_weights:
        return {"reply": "No performance data yet. Providers are ranked by default priority order."}

    lines = []
    for i, (name, weight) in enumerate(sorted_weights[:10]):
        bar = "█" * int(weight * 10) + "░" * (10 - int(weight * 10))
        lines.append(f"{i+1}. **{name}** [{bar}] {weight}")
    return {"reply": "Provider Performance:\n" + "\n".join(lines)}


def _handle_available_providers():
    from core.ai_provider import list_providers
    from core.ai.provider_health import get_all_snapshots

    providers = list_providers()
    quota_data = get_all_snapshots() or {}

    online = []
    offline = []
    for name, info in providers.items():
        if not info.get("enabled", True):
            offline.append(f"⏸ {name} (disabled)")
        elif info.get("available", False):
            qs = quota_data.get(name, {}) if isinstance(quota_data, dict) else {}
            qstatus = qs.get("status", "ok") if isinstance(qs, dict) else "ok"
            online.append(f"✅ {name} — {info.get('description','')[:60]}")
        else:
            offline.append(f"❌ {name} (unavailable)")

    return {"reply": f"Available ({len(online)}):\n" + "\n".join(online[:8]) + "\n\nOffline:\n" + "\n".join(offline[:5])}


def _handle_provider_status(name):
    from core.ai_provider import get_provider
    from core.ai.provider_health import get_quota_snapshot
    from core.weighted_routing import get_provider_weights

    provider = get_provider(name)
    if not provider:
        return {"reply": f"Provider '{name}' not found. Try: qwen4_coding, opencode_claude, gemini, groq, deepseek_native_flash"}

    info = {
        "name": name,
        "available": bool(provider.get("available_fn", lambda: False)()),
        "enabled": provider.get("enabled", True),
        "capabilities": provider.get("capabilities", []),
        "cost_tier": provider.get("cost_tier", "?"),
        "description": provider.get("description", "")[:100],
    }

    quota = get_quota_snapshot(name)
    weights = get_provider_weights()
    weight = weights.get(name, "no data")

    lines = [
        f"**{name}**",
        f"Status: {'✅ Available' if info['available'] else '❌ Unavailable'}",
        f"Enabled: {'Yes' if info['enabled'] else 'No (operator disabled)'}",
        f"Cost: {info['cost_tier']}",
        f"Capabilities: {', '.join(info['capabilities']) or 'none'}",
        f"Performance weight: {weight}",
    ]
    if quota:
        lines.append(f"Quota: {quota.get('status','ok')}")

    return {"reply": "\n".join(lines)}

# New handlers for worker health and provider statistics

def _handle_worker_health():
    """Display health status of AI workers (quota, errors)."""
    from core.ai.provider_health import get_all_quota_snapshots

    snapshots = get_all_quota_snapshots() or {}
    if not snapshots:
        return {"reply": "No worker health data available."}
    lines = []
    for name, data in snapshots.items():
        status = data.get("status", "unknown")
        quota = data.get("quota", "N/A")
        remaining = data.get("remaining", "N/A")
        lines.append(f"**{name}** — status: {status}, quota: {quota}, remaining: {remaining}")
    return {"reply": "\n".join(lines)}


def _handle_provider_statistics():
    """Show provider performance statistics (success rate, queue depth, avg duration)."""
    from core.ai.ai_router import get_provider_dashboard

    dashboard = get_provider_dashboard() or {}
    if not dashboard:
        return {"reply": "No provider statistics available."}
    lines = []
    for name, info in dashboard.items():
        available = "✅" if info.get("available") else "❌"
        enabled = "enabled" if info.get("enabled") else "disabled"
        success_rate = info.get("success_rate")
        if success_rate is not None:
            success_str = f"{success_rate*100:.1f}%"
        else:
            success_str = "N/A"
        queue = info.get("queue_depth", 0)
        avg = info.get("avg_duration_ms", 0)
        lines.append(
            f"{available} **{name}** ({enabled}) — success {success_str}, queue {queue}, avg dur {avg}ms"
        )
    return {"reply": "\n".join(lines)}



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
    (
        re.compile(r"^(?:kai,\s*)?(?:worker\s+health|health\s+of\s+workers?)\.?$", re.IGNORECASE),
        lambda match: _handle_worker_health(),
        "Show AI worker health — quota, errors, and status.",
    ),
    (
        re.compile(r"^(?:kai,\s*)?(?:provider\s+statistics|stats\s+for\s+providers?)\.?$", re.IGNORECASE),
        lambda match: _handle_provider_statistics(),
        "Show provider performance statistics — success rate, queue depth, avg duration.",
    ),
)


def dispatch(text):
    cleaned = (text or "").strip()

    for pattern, handler, description in COMMAND_PATTERNS:
        match = pattern.match(cleaned)
        if match:
            try:
                import inspect
                if inspect.signature(handler).parameters:
                    result = handler(match)
                else:
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
        # Auto-task: add tasks to the roadmap so nothing is forgotten
    (
        re.compile(r"^(?:kai,\s*)?(?:task|todo|add\s+to\s+roadmap)[:\s]+(.+)$", re.IGNORECASE | re.DOTALL),
        lambda match: _handle_add_task(str(match.group(1)).strip()),
        "Add a task to the roadmap — 'Kai, task: description' or 'Kai, todo: description'."
    ),
    (
        re.compile(r"^(?:kai,\s*)?(?:show\s+open\s+tasks|pending\s+tasks|what\s+tasks\s+are\s+open)\.?$", re.IGNORECASE),
        lambda match: _handle_list_tasks(),
        "Show open tasks — returns all proposed/pending roadmap items."
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
    # 13K: Voice/Text workforce commands
    (
        re.compile(r"^(?:kai,\s*)?(?:list\s+(?:ai\s+)?workers?|show\s+(?:ai\s+)?workforce|what\s+workers?\s+(?:are|do we)\s+have)\.?$", re.IGNORECASE),
        lambda match: _handle_list_workers(),
        "List AI workers — shows all registered providers with status and capabilities.",
    ),
    (
        re.compile(r"^(?:kai,\s*)?(?:which\s+provider\s+is\s+(?:fastest|best|most\s+reliable)|compare\s+providers?|provider\s+(?:rank|performance))\.?$", re.IGNORECASE),
        lambda match: _handle_provider_ranking(),
        "Show provider performance rankings.",
    ),
    (
        re.compile(r"^(?:kai,\s*)?(?:what\s+(?:providers?|workers?)\s+are\s+(?:available|online|running|active|up))\.?$", re.IGNORECASE),
        lambda match: _handle_available_providers(),
        "Show available/online providers with status.",
    ),
    (
        re.compile(r"^(?:kai,\s*)?(?:how\s+(?:is|are)\s+)?([a-z0-9_-]+)\s+(?:doing|status|health)\s*\.?$", re.IGNORECASE),
        lambda match: _handle_provider_status(str(match.group(1)).strip()),
        "Check a specific provider's status — 'Kai, how is qwen4_coding doing?'",
    ),
)
