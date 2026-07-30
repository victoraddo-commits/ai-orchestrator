"""13V: Chief Architect -- thin wrapper around ai_router.delegate() for the
dedicated architecture fallback chain.

Callers use delegate_chief_architect(description) to route architecture
work through the ROLE_PROVIDERS["architecture"] priority list (Claude ->
Gemini -> DeepSeek -> OpenRouter Claude -> OpenAI) exactly as delegate()
does, without duplicating any fallback-walking logic.  Every call --
success or failure -- appends a record of shape
{task, primary, fallback_used, reason, result} to
memory/chief_architect_history.json, using the existing core.memory
primitives for atomic read-modify-write.
"""

from core.ai.ai_router import delegate, AllProvidersFailed
from core.memory import load, update

ARCHITECTURE_HISTORY_FILE = "chief_architect_history.json"
ARCHITECTURE_ROLE = "architecture"
_PRIMARY = "claude"  # always the first candidate in the architecture chain

_ERROR_TYPE_MESSAGES = {
    "quota_exceeded": "{provider} quota exhausted",
    "timeout": "{provider} timeout",
    "unavailable": "{provider} unavailable",
    "degraded_health": "{provider} degraded health",
    "error": "{provider} error",
}


def _build_reason(attempt):
    provider = attempt["provider"]
    error_type = attempt.get("error_type", "error")
    template = _ERROR_TYPE_MESSAGES.get(error_type, "{provider} {error_type}")
    return template.format(provider=provider, error_type=error_type)


def delegate_chief_architect(description, **kwargs):
    success = False
    fallback_used = None
    reason = ""

    try:
        result = delegate(description, task_type=ARCHITECTURE_ROLE, return_attempts=True, **kwargs)
        success = True

        attempts = result.get("attempts", [])
        if attempts:
            fallback_used = result["provider"]
            reason = _build_reason(attempts[0])

        _append_record({
            "task": "architecture planning",
            "primary": _PRIMARY,
            "fallback_used": fallback_used,
            "reason": reason,
            "result": "success",
        })
        return result

    except AllProvidersFailed as exc:
        attempts = exc.attempts
        reason = _build_reason(attempts[-1]) if attempts else "all providers failed"

        _append_record({
            "task": "architecture planning",
            "primary": _PRIMARY,
            "fallback_used": fallback_used,
            "reason": reason,
            "result": "failure",
        })
        raise


def _append_record(record):
    def mutate(data):
        if not isinstance(data, dict):
            data = {}
        data.setdefault("schema_version", 1)
        data.setdefault("records", [])
        data["records"].append(record)
        return data

    update(ARCHITECTURE_HISTORY_FILE, mutate)


def get_architecture_history():
    data = load(ARCHITECTURE_HISTORY_FILE)
    if not isinstance(data, dict):
        return []
    return data.get("records", [])