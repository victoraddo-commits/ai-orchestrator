"""Starvation detection (spec §2): a build queued past 2× its phase timeout
while workers were nominally available means the pipeline is starved.
Response: temporary concurrency boost within a hard ceiling + one operator
alert per episode. State persists through core.memory (workforce_boost.json).
"""
from __future__ import annotations

import time
from datetime import datetime, timezone

from core.memory import load as _memory_load, save as _memory_save
from core.logger import info as _log

BOOST_FILE = "workforce_boost.json"
BOOST_STEP = 2                       # add 2 slots per detection
HARD_CEILING_EXTRA = 4               # never more than base+4 total
BOOST_TTL_SECONDS = 1800             # boost decays after 30min
STARVATION_MULTIPLIER = 2            # threshold = 2× phase timeout

_ALERTED_KEY = "starvation_alerted"


def _now_ts() -> float:
    return time.time()


def _load_boost() -> dict:
    data = _memory_load(BOOST_FILE)
    if isinstance(data, dict) and "boost" in data:
        return data
    return {"boost": 0, "expires_at": 0}


def _save_boost(state: dict) -> None:
    _memory_save(BOOST_FILE, state)


def grant_boost(amount: int) -> None:
    state = _load_boost()
    state["boost"] = min(int(state.get("boost", 0)) + amount,
                         HARD_CEILING_EXTRA)
    state["expires_at"] = _now_ts() + BOOST_TTL_SECONDS
    _save_boost(state)
    _log(f"workforce starvation: concurrency boost now +{state['boost']}")


def current_boost() -> int:
    state = _load_boost()
    if float(state.get("expires_at", 0)) < _now_ts():
        return 0
    return max(0, min(int(state.get("boost", 0)), HARD_CEILING_EXTRA))


def clear_boost() -> None:
    _save_boost({"boost": 0, "expires_at": 0})


def _notify_operator(message: str) -> None:
    try:
        from core.notifications import NotificationManager
        NotificationManager.enqueue(
            severity="important", title="Kai workforce",
            body=message, source="workforce_starvation")
    except Exception:
        pass  # notification failure must never break detection


def _queued_since(build, now: float):
    qs = build.get("_queued_since")
    if qs:
        return float(qs)
    updated = build.get("updated")
    if updated:
        try:
            dt = datetime.fromisoformat(updated)
            return dt.timestamp()
        except (ValueError, TypeError):
            pass
    return None


def detect(builds: list, now: float = None, phase_timeout_seconds: int = 2400) -> list:
    """Flag waiting builds past 2× phase timeout; first flag in an episode
    grants the boost and alerts once."""
    now = now if now is not None else _now_ts()
    threshold = STARVATION_MULTIPLIER * phase_timeout_seconds
    events = []
    episode_new = False

    for build in builds:
        if build.get("status") != "ARCHITECTURE_APPROVED":
            continue
        if build.get(_ALERTED_KEY):
            continue
        since = _queued_since(build, now)
        if since is None or (now - since) <= threshold:
            continue
        episode_new = True
        events.append({
            "action": "starvation_detected",
            "build_id": build.get("id"),
            "name": build.get("name"),
            "queued_seconds": int(now - since),
            "threshold_seconds": threshold,
        })

    if episode_new:
        grant_boost(BOOST_STEP)
        _notify_operator(
            f"Kai workforce: STARVATION — {len(events)} build(s) queued past "
            f"{threshold}s; concurrency boosted +{BOOST_STEP} "
            f"(ceiling +{HARD_CEILING_EXTRA})")

    # Expired boost self-heals on next current_boost() read.
    return events
