"""Provider quota/credit health tracking.

Deliberately honest about what each provider actually exposes rather than
fabricating a uniform percentage for all of them. The fabric is local-only
(owner directive: zero third-party providers), so a quota snapshot is
normally just a health/error record for a self-hosted node; if a provider
does return rate-limit headers it is still captured reactively.
"""

from datetime import datetime

from core.memory import load, save


QUOTA_STATE_FILE = "provider_quota.json"

# 2026-08-02 operator directive ("make sure fallbacks kick in the moment
# credit limit is hit"): quota_exceeded previously persisted forever once
# set -- confirmed live, gemini and the whole opencode_claude family were
# still being silently skipped by ai_router.delegate() hours (opencode_claude:
# over a day) after their quota_exceeded flags were set, even after gemini's
# credit was reloaded and opencode_claude's real calls were succeeding all
# day, because nothing ever re-checked or expired the flag. A quota_exceeded
# snapshot now naturally expires after this window -- get_quota_snapshot()
# reports an expired one as "unknown" instead of "quota_exceeded", so
# delegate() (and core.build_manager's advisory code review) gives the
# provider a real retry instead of trusting a possibly-outdated (or
# outright misclassified -- see ai_router._classify_failure_reason's
# marker-matching) verdict indefinitely. See also clear_quota_exceeded():
# a real success clears it immediately, well before this window elapses.
QUOTA_EXCEEDED_EXPIRY_SECONDS = 3600


def _load_state():
    return load(QUOTA_STATE_FILE) or {}


def _save_state(state):
    save(QUOTA_STATE_FILE, state)


def record_quota_snapshot(provider, status, percent_remaining=None, detail=None, **extra):
    state = _load_state()

    state[provider] = {
        "status": status,
        "percent_remaining": percent_remaining,
        "detail": detail,
        "checked_at": datetime.now().isoformat(),
        **extra,
    }

    _save_state(state)

    return state[provider]


def _apply_quota_exceeded_expiry(snapshot):
    if snapshot is None or snapshot.get("status") != "quota_exceeded":
        return snapshot

    checked_at = snapshot.get("checked_at")
    try:
        age_seconds = (datetime.now() - datetime.fromisoformat(checked_at)).total_seconds()
    except (TypeError, ValueError):
        return snapshot

    if age_seconds < QUOTA_EXCEEDED_EXPIRY_SECONDS:
        return snapshot

    return {**snapshot, "status": "unknown", "expired_quota_exceeded_detail": snapshot.get("detail")}


def get_quota_snapshot(provider):
    return _apply_quota_exceeded_expiry(_load_state().get(provider))


def get_all_quota_snapshots():
    return {name: _apply_quota_exceeded_expiry(snapshot) for name, snapshot in _load_state().items()}


def prune_stale_snapshots(active_providers):
    """Drop snapshots for providers that are no longer registered.

    Keeps the heartbeat honest: a deregistered or renamed provider must not
    linger as error/degraded forever. Returns the list of removed names.
    """

    active = set(active_providers or ())

    state = _load_state()

    removed = sorted(name for name in state.keys() if name not in active)

    if removed:
        for name in removed:
            state.pop(name, None)
        _save_state(state)

    return removed


def clear_quota_exceeded(provider):
    # Called from delegate()'s (and the advisory code review's) success
    # path -- a real success is stronger, more immediate evidence than the
    # QUOTA_EXCEEDED_EXPIRY_SECONDS safety-net expiry above, so this clears
    # the flag right away rather than waiting out the window. A no-op when
    # the provider isn't currently marked quota_exceeded, so callers can
    # call this unconditionally after every success.
    state = _load_state()
    if state.get(provider, {}).get("status") != "quota_exceeded":
        return None

    return record_quota_snapshot(provider, status="ok", percent_remaining=None, detail="cleared after a real success")


def capture_from_response_headers(provider, headers):
    remaining_tokens = headers.get("x-ratelimit-remaining-tokens")
    limit_tokens = headers.get("x-ratelimit-limit-tokens")

    if remaining_tokens is not None and limit_tokens is not None:
        remaining_tokens = int(remaining_tokens)
        limit_tokens = int(limit_tokens)
        percent = round(remaining_tokens / limit_tokens * 100, 1) if limit_tokens else None

        return record_quota_snapshot(
            provider,
            status="ok",
            percent_remaining=percent,
            detail="live rate-limit headers from provider",
            remaining_tokens=remaining_tokens,
            limit_tokens=limit_tokens,
            remaining_requests=int(headers["x-ratelimit-remaining-requests"]) if "x-ratelimit-remaining-requests" in headers else None,
            limit_requests=int(headers["x-ratelimit-limit-requests"]) if "x-ratelimit-limit-requests" in headers else None,
        )

    return record_quota_snapshot(
        provider,
        status="ok",
        percent_remaining=None,
        detail="last request succeeded, but this provider returns no quota data in its response headers",
    )


def capture_quota_exceeded(provider, detail):
    return record_quota_snapshot(provider, status="quota_exceeded", percent_remaining=0, detail=detail)


def capture_provider_error(provider, detail):
    # Deliberately not classified as "quota_exceeded" -- we can't verify the
    # exact wording an upstream uses when a usage limit is hit (no documented
    # error string to match against), so this surfaces the raw error verbatim
    # instead of guessing what it means.
    return record_quota_snapshot(provider, status="error", percent_remaining=None, detail=detail)
