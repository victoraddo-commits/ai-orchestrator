"""Tests for core.research_session_logger (phase 18D)."""
from __future__ import annotations

import time

import pytest

from core.research_session_logger import (
    finalize_session,
    get_session,
    list_sessions,
    log_session,
)


def test_log_session_appends_and_returns_record():
    rec = log_session(
        purpose="investigate provider circuit breaker",
        operator="claude",
        tags=["debug", "providers"],
    )
    assert rec["session_id"] and len(rec["session_id"]) == 12
    assert rec["operator"] == "claude"
    assert rec["purpose"] == "investigate provider circuit breaker"
    assert rec["started_at"]  # iso8601
    assert rec["ended_at"] is None
    assert rec["outcome"] is None
    assert rec["artifacts"] == []
    assert rec["tags"] == ["debug", "providers"]

    # Reads back via get_session
    fetched = get_session(rec["session_id"])
    assert fetched == rec


def test_list_sessions_reverse_chronological():
    a = log_session("first", "op1")
    # Ensure distinct timestamps
    time.sleep(0.01)
    b = log_session("second", "op1")
    time.sleep(0.01)
    c = log_session("third", "op1")

    items = list_sessions()
    ids = [r["session_id"] for r in items]
    # Newest first
    assert ids[0] == c["session_id"]
    assert ids[1] == b["session_id"]
    assert ids[2] == a["session_id"]


def test_list_sessions_honors_limit():
    for i in range(5):
        log_session(f"purpose-{i}", "op1")
        time.sleep(0.005)
    items = list_sessions(limit=2)
    assert len(items) == 2


def test_get_session_returns_none_on_miss():
    log_session("just to make the store non-empty", "op1")
    assert get_session("does-not-exist") is None


def test_finalize_session_sets_ended_at_and_outcome():
    rec = log_session("do the thing", "op1")
    updated = finalize_session(rec["session_id"], "completed")
    assert updated is not None
    assert updated["outcome"] == "completed"
    assert updated["ended_at"]

    # Idempotent — a second finalize overwrites outcome/ended_at
    first_ended = updated["ended_at"]
    time.sleep(0.01)
    again = finalize_session(rec["session_id"], "failed")
    assert again is not None
    assert again["outcome"] == "failed"
    assert again["ended_at"] >= first_ended


def test_finalize_session_extends_artifacts_without_duplicates():
    rec = log_session("with artifacts", "op1", artifacts=["a.md"])
    updated = finalize_session(
        rec["session_id"], "completed", artifacts=["b.md", "a.md", "c.md"]
    )
    assert updated is not None
    assert updated["artifacts"] == ["a.md", "b.md", "c.md"]


def test_finalize_session_rejects_invalid_outcome():
    rec = log_session("bad outcome", "op1")
    with pytest.raises(ValueError):
        finalize_session(rec["session_id"], "not-a-real-outcome")


def test_finalize_session_missing_id_returns_none():
    assert finalize_session("no-such-session-id", "completed") is None


def test_list_sessions_since_iso_filters():
    a = log_session("before", "op1")
    time.sleep(0.02)
    cutoff = a["started_at"]  # everything at-or-after this timestamp
    time.sleep(0.02)
    b = log_session("after", "op1")

    items = list_sessions(since_iso=cutoff)
    ids = {r["session_id"] for r in items}
    # Only sessions with started_at >= cutoff (which includes a itself and b)
    assert b["session_id"] in ids

    # A tighter cutoff excludes 'a'
    future = b["started_at"]
    tight = list_sessions(since_iso=future)
    assert a["session_id"] not in {r["session_id"] for r in tight}
