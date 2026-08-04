"""Phase 17X: Automated Resiliency for Kai Autonomous Development Loop.

Circuit-breaker for coding_bridge process exits, retry with backoff,
per-provider failure tracking that auto-deprioritizes after N consecutive
failures, and watchdog recovery for hung scheduler cycles.
"""

import json
import os
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

# Track consecutive failures per provider
_failure_counts: dict[str, int] = defaultdict(int)
_last_failure_time: dict[str, str] = {}
COSECUTIVE_FAILURE_THRESHOLD = 3  # Auto-deprioritize after N consecutive failures
COOLDOWN_SECONDS = 300  # 5-minute cooldown after crossing threshold
RESILIENCY_FILE = "resiliency_state.json"


def _load_state():
    from core.memory import load
    data = load(RESILIENCY_FILE)
    if isinstance(data, dict):
        return data
    return {}


def _save_state(state):
    from core.memory import save
    save(RESILIENCY_FILE, state)


def record_provider_failure(provider_name):
    """Record a provider failure.  If threshold crossed, mark as degraded."""
    _failure_counts[provider_name] += 1
    _last_failure_time[provider_name] = datetime.now(timezone.utc).isoformat()

    if _failure_counts[provider_name] >= COSECUTIVE_FAILURE_THRESHOLD:
        state = _load_state()
        state[provider_name] = {
            "degraded": True,
            "since": datetime.now(timezone.utc).isoformat(),
            "consecutive_failures": _failure_counts[provider_name],
            "auto_recover_at": (
                datetime.now(timezone.utc).isoformat()
            ),
        }
        _save_state(state)


def record_provider_success(provider_name):
    """A successful call resets the failure counter."""
    _failure_counts[provider_name] = 0
    state = _load_state()
    if provider_name in state:
        del state[provider_name]
        _save_state(state)


def is_degraded(provider_name):
    """Check if a provider is currently in a degraded (circuit-breaker) state."""
    state = _load_state()
    entry = state.get(provider_name)
    if not entry or not entry.get("degraded"):
        return False

    # Check if cooldown has expired
    try:
        degraded_at = datetime.fromisoformat(entry["since"])
        elapsed = (datetime.now(timezone.utc) - degraded_at).total_seconds()
        if elapsed > COOLDOWN_SECONDS:
            # Cooldown expired — auto-recover
            del state[provider_name]
            _save_state(state)
            _failure_counts[provider_name] = 0
            return False
    except (ValueError, KeyError):
        pass

    return True


def get_resiliency_status():
    """Return current resiliency state for the dashboard."""
    state = _load_state()
    return {
        "degraded_providers": {
            name: info for name, info in state.items() if info.get("degraded")
        },
        "failure_counts": dict(_failure_counts),
        "cooldown_seconds": COOLDOWN_SECONDS,
        "threshold": COSECUTIVE_FAILURE_THRESHOLD,
    }


def run_with_retry(fn, max_retries=3, backoff_base=2):
    """Call fn() with exponential backoff retry.  Returns (result, None)
    on success, or (None, last_error) on exhaustion."""
    last_error = None
    for attempt in range(max_retries):
        try:
            result = fn()
            return result, None
        except Exception as e:
            last_error = e
            if attempt < max_retries - 1:
                wait = backoff_base ** attempt
                time.sleep(wait)
    return None, last_error
