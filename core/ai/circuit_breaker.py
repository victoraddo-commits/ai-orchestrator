"""Phase 17R: circuit-breaker pattern for AI provider calls.

Tracks consecutive errors per provider. When the failure threshold is reached,
the circuit breaker trips and the provider is bypassed for a cooldown period
rather than retried on every incoming call where it would keep failing.
"""

import time
from datetime import datetime

from core.memory import load, save


CIRCUIT_BREAKER_FILE = "circuit_breaker.json"

CIRCUIT_BREAKER_FAILURE_THRESHOLD = 3

CIRCUIT_BREAKER_COOLDOWN_SECONDS = 60


def _load_state():
    return load(CIRCUIT_BREAKER_FILE) or {}


def _save_state(state):
    save(CIRCUIT_BREAKER_FILE, state)


def record_success(provider):
    state = _load_state()
    state.pop(provider, None)
    _save_state(state)


def record_failure(provider):
    state = _load_state()
    entry = state.get(provider, {})
    consecutive = entry.get("consecutive_failures", 0) + 1
    entry["consecutive_failures"] = consecutive
    entry["last_failure_at"] = datetime.now().isoformat()

    if consecutive >= CIRCUIT_BREAKER_FAILURE_THRESHOLD:
        entry["state"] = "open"
        entry["tripped_at"] = datetime.now().isoformat()
    else:
        entry["state"] = "closed"

    state[provider] = entry
    _save_state(state)
    return entry


def is_open(provider):
    state = _load_state()
    entry = state.get(provider)
    if entry is None:
        return False

    if entry.get("state") != "open":
        return False

    tripped_at = entry.get("tripped_at")
    try:
        elapsed = (datetime.now() - datetime.fromisoformat(tripped_at)).total_seconds()
    except (TypeError, ValueError):
        return False

    if elapsed >= CIRCUIT_BREAKER_COOLDOWN_SECONDS:
        entry["state"] = "half_open"
        state[provider] = entry
        _save_state(state)
        return False

    return True


def clear_breaker(provider):
    state = _load_state()
    state.pop(provider, None)
    _save_state(state)


def get_breaker_snapshot(provider):
    state = _load_state()
    entry = state.get(provider)
    if entry is None:
        return None
    return dict(entry)
