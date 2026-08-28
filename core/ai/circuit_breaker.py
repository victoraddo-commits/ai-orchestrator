"""Phase 17R / AI-3: circuit-breaker pattern for AI provider calls.

Tracks consecutive errors per provider. When the failure threshold is reached,
the circuit breaker trips (OPEN) and the provider is bypassed for a cooldown
period rather than retried on every incoming call where it would keep failing.

After the cooldown elapses the breaker transitions to HALF_OPEN: one probe
request is allowed through. If it succeeds the breaker resets (CLOSED); if it
fails the breaker returns to OPEN and the cooldown restarts.

Configuration (overridable via set_threshold / set_cooldown):
  FAILURE_THRESHOLD = 3   — consecutive failures to trip
  COOLDOWN_SECONDS   = 300 — seconds to wait before half-open probe
"""

import time
from datetime import datetime
from typing import Optional

from core.memory import load, save


CIRCUIT_BREAKER_FILE = "circuit_breaker.json"

# Defaults — can be overridden per-provider via configuration
_DEFAULT_FAILURE_THRESHOLD = 3
_DEFAULT_COOLDOWN_SECONDS = 300  # AI-3: 5 minutes per roadmap spec

# Backward-compatible aliases for tests and consumers that reference
# the old hardcoded constants directly.
CIRCUIT_BREAKER_FAILURE_THRESHOLD = _DEFAULT_FAILURE_THRESHOLD
CIRCUIT_BREAKER_COOLDOWN_SECONDS = _DEFAULT_COOLDOWN_SECONDS


def _load_state() -> dict:
    return load(CIRCUIT_BREAKER_FILE) or {}


def _save_state(state: dict) -> None:
    save(CIRCUIT_BREAKER_FILE, state)


# ---------------------------------------------------------------------------
# Core breaker operations
# ---------------------------------------------------------------------------

def record_success(provider: str) -> None:
    """Clear the breaker for *provider* — success resets everything."""
    state = _load_state()
    if provider in state:
        state.pop(provider, None)
        _save_state(state)


def record_failure(provider: str) -> dict:
    """Record a failure and trip the breaker if the threshold is crossed.

    Returns the provider's current breaker entry.
    """
    state = _load_state()
    entry = state.get(provider, {})
    consecutive = entry.get("consecutive_failures", 0) + 1
    threshold = entry.get("threshold", _DEFAULT_FAILURE_THRESHOLD)

    entry["consecutive_failures"] = consecutive
    entry["last_failure_at"] = datetime.now().isoformat()

    if consecutive >= threshold:
        entry["state"] = "open"
        entry["tripped_at"] = datetime.now().isoformat()
    else:
        entry["state"] = "closed"

    state[provider] = entry
    _save_state(state)
    return dict(entry)


def is_open(provider: str) -> bool:
    """Return True if the breaker is OPEN (calls should be skipped).

    Side effect: if the breaker is OPEN and the cooldown has elapsed,
    transitions to HALF_OPEN (returns False — the next call is allowed
    through as a probe).
    """
    state = _load_state()
    entry = state.get(provider)
    if entry is None:
        return False

    if entry.get("state") != "open":
        return False

    cooldown = entry.get("cooldown_seconds", _DEFAULT_COOLDOWN_SECONDS)
    tripped_at = entry.get("tripped_at")
    try:
        elapsed = (datetime.now() - datetime.fromisoformat(tripped_at)).total_seconds()
    except (TypeError, ValueError):
        return False

    if elapsed > cooldown:
        # Transition to half-open — allow one probe through
        entry["state"] = "half_open"
        entry["half_open_at"] = datetime.now().isoformat()
        state[provider] = entry
        _save_state(state)
        return False

    return True


def is_half_open(provider: str) -> bool:
    """Return True if the breaker is in HALF_OPEN state.

    Side effect: if the breaker has been half_open for longer than the cooldown
    period (meaning the probe request never came), transitions it back to CLOSED.
    This prevents half_open entries from getting stuck when the provider is not
    in any active routing chain and record_success() is never called.
    """
    state = _load_state()
    entry = state.get(provider)
    if entry is None:
        return False

    if entry.get("state") != "half_open":
        return False

    # half_open probe window = one cooldown period. If no probe came in that
    # time, return to closed rather than stay stuck indefinitely.
    cooldown = entry.get("cooldown_seconds", _DEFAULT_COOLDOWN_SECONDS)
    half_open_at = entry.get("half_open_at")
    try:
        elapsed = (datetime.now() - datetime.fromisoformat(half_open_at)).total_seconds()
    except (TypeError, ValueError):
        return True  # malformed timestamp — treat as half-open

    if elapsed > cooldown:
        # Probe window expired without a call to is_open() — transition to closed
        state.pop(provider, None)
        _save_state(state)
        return False

    return True


def clear_breaker(provider: str) -> None:
    """Manually reset a provider's circuit breaker."""
    state = _load_state()
    state.pop(provider, None)
    _save_state(state)


def get_breaker_snapshot(provider: str) -> Optional[dict]:
    """Return the current breaker entry for *provider*, or None."""
    state = _load_state()
    entry = state.get(provider)
    return dict(entry) if entry else None


# ---------------------------------------------------------------------------
# Bulk operations (API-facing)
# ---------------------------------------------------------------------------

def list_all_breakers() -> list[dict]:
    """Return all breaker entries with provider names attached."""
    state = _load_state()
    return [{"provider": name, **dict(entry)} for name, entry in state.items()]


def reset_all_breakers() -> int:
    """Clear every circuit breaker. Returns the count that were cleared."""
    state = _load_state()
    count = len(state)
    _save_state({})
    return count


def trip_breaker_manually(provider: str, detail: str = "manual trip") -> dict:
    """Force a breaker into OPEN state (for testing/admin)."""
    state = _load_state()
    entry = state.get(provider, {})
    entry["state"] = "open"
    entry["consecutive_failures"] = entry.get("threshold", _DEFAULT_FAILURE_THRESHOLD)
    entry["tripped_at"] = datetime.now().isoformat()
    entry["last_failure_at"] = datetime.now().isoformat()
    entry["detail"] = detail
    state[provider] = entry
    _save_state(state)
    return dict(entry)


# ---------------------------------------------------------------------------
# Configuration (per-provider overrides)
# ---------------------------------------------------------------------------

def set_threshold(provider: str, threshold: int) -> None:
    """Set a per-provider failure threshold. Must be >= 1."""
    if threshold < 1:
        raise ValueError(f"threshold must be >= 1, got {threshold}")
    state = _load_state()
    entry = state.get(provider, {})
    entry["threshold"] = threshold
    state[provider] = entry
    _save_state(state)


def set_cooldown(provider: str, cooldown_seconds: int) -> None:
    """Set a per-provider cooldown in seconds.

    Values below 1 are allowed for testing but will make the breaker
    effectively immediate — not recommended for production use.
    """
    if cooldown_seconds < 0:
        raise ValueError(f"cooldown must be >= 0, got {cooldown_seconds}")
    state = _load_state()
    entry = state.get(provider, {})
    entry["cooldown_seconds"] = cooldown_seconds
    state[provider] = entry
    _save_state(state)


def get_effective_config(provider: str) -> dict:
    """Return the effective threshold and cooldown for a provider."""
    state = _load_state()
    entry = state.get(provider, {})
    return {
        "threshold": entry.get("threshold", _DEFAULT_FAILURE_THRESHOLD),
        "cooldown_seconds": entry.get("cooldown_seconds", _DEFAULT_COOLDOWN_SECONDS),
    }
