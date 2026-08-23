"""Starvation: queued-too-long builds trigger boost + operator alert."""
import time
from unittest import mock

from core.workforce import starvation, registry


def _reset_boost():
    registry._save_all({"schema_version": 1, "records": []})
    starvation.clear_boost()


def test_no_starvation_when_nothing_waiting():
    _reset_boost()
    events = starvation.detect([], now=time.time())
    assert events == []


def test_starved_build_triggers_event_and_boost():
    _reset_boost()
    builds = [{
        "id": "b1", "name": "starved-one", "status": "ARCHITECTURE_APPROVED",
        "updated": "2026-08-22T00:00:00",
        "_queued_since": time.time() - 3 * 3600,   # 3h queued >> 2×40min
    }]
    notified = []
    with mock.patch.object(starvation, "_notify_operator",
                           side_effect=lambda m: notified.append(m)):
        events = starvation.detect(builds, now=time.time(),
                                   phase_timeout_seconds=2400)
    assert len(events) == 1
    assert events[0]["action"] == "starvation_detected"
    assert starvation.current_boost() == starvation.BOOST_STEP
    assert len(notified) == 1


def test_boost_capped_and_expires():
    _reset_boost()
    starvation.grant_boost(starvation.HARD_CEILING_EXTRA)
    assert starvation.current_boost() == starvation.HARD_CEILING_EXTRA
    starvation.grant_boost(99)                      # cannot exceed ceiling
    assert starvation.current_boost() == starvation.HARD_CEILING_EXTRA
    # expiry
    starvation._save_boost({"boost": 4, "expires_at": time.time() - 10})
    assert starvation.current_boost() == 0


def test_clear_boost():
    _reset_boost()
    starvation.grant_boost(2)
    starvation.clear_boost()
    assert starvation.current_boost() == 0
