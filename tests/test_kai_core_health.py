"""Tests for phase 22A — core/kai_core_health.py."""
from __future__ import annotations

import pytest


def test_get_health_returns_expected_keys(monkeypatch):
    from core import kai_core_health as _kch
    # Neutralize the journal read so this doesn't depend on host state.
    monkeypatch.setattr(_kch, "_read_journal", lambda *a, **k: "")
    out = _kch.get_health()
    assert "generated_at" in out
    assert "cycle" in out
    for k in ("last_started_at", "last_completed_at", "running_now",
              "avg_duration_sec", "cycles_seen_last_hour"):
        assert k in out["cycle"]
    assert "stalls_last_24h" in out
    assert "advance_builds_timeout_env" in out
    assert "stale_warn_cycles_env" in out


def test_empty_journal_defaults(monkeypatch):
    from core import kai_core_health as _kch
    monkeypatch.setattr(_kch, "_read_journal", lambda *a, **k: "")
    out = _kch.get_health()
    assert out["cycle"]["last_started_at"] is None
    assert out["cycle"]["last_completed_at"] is None
    assert out["cycle"]["cycles_seen_last_hour"] == 0
    assert out["stalls_last_24h"] == 0


def test_cycle_pairing_and_avg(monkeypatch):
    from core import kai_core_health as _kch
    fake = (
        "2026-09-11 22:00:00 === orchestrator cycle started ===\n"
        "2026-09-11 22:01:00 === orchestrator cycle completed ===\n"
        "2026-09-11 22:03:00 === orchestrator cycle started ===\n"
        "2026-09-11 22:03:30 === orchestrator cycle completed ===\n"
        "2026-09-11 22:05:00 === orchestrator cycle started ===\n"
    )
    monkeypatch.setattr(_kch, "_read_journal", lambda *a, **k: fake)
    out = _kch.get_health()
    assert out["cycle"]["last_started_at"] is not None
    assert out["cycle"]["last_completed_at"] is not None
    # 60s + 30s = 90s / 2 = 45s
    assert out["cycle"]["avg_duration_sec"] == 45.0
    # 3 starts total
    assert out["cycle"]["cycles_seen_last_hour"] == 3
    # unmatched trailing start → running_now True
    assert out["cycle"]["running_now"] is True


def test_running_now_false_after_completion(monkeypatch):
    from core import kai_core_health as _kch
    fake = (
        "2026-09-11 22:00:00 === orchestrator cycle started ===\n"
        "2026-09-11 22:00:30 === orchestrator cycle completed ===\n"
    )
    monkeypatch.setattr(_kch, "_read_journal", lambda *a, **k: fake)
    out = _kch.get_health()
    assert out["cycle"]["running_now"] is False


def test_stall_count_from_journal(monkeypatch):
    from core import kai_core_health as _kch
    fake = (
        "2026-09-11 20:00:00 WARNING: advance_builds timed out after 300s — cycle continues, builds still in flight\n"
        "2026-09-11 20:05:00 WARNING: build abcdef123456 (21A) stuck in GENERATING for 4 cycles — last update 2026-09-11T20:00:00\n"
        "2026-09-11 20:10:00 info: cycle completed findings=9 incidents=4 decisions=1\n"
        "2026-09-11 20:15:00 WARNING: advance_builds timed out after 300s — cycle continues, builds still in flight\n"
    )
    monkeypatch.setattr(_kch, "_read_journal", lambda *a, **k: fake)
    out = _kch.get_health()
    assert out["stalls_last_24h"] == 3


def test_malformed_lines_do_not_crash(monkeypatch):
    from core import kai_core_health as _kch
    fake = (
        "no timestamp on this line\n"
        "2026-99-99 99:99:99 === orchestrator cycle started ===\n"  # invalid ts
        "2026-09-11 22:00:00 unrelated log\n"
    )
    monkeypatch.setattr(_kch, "_read_journal", lambda *a, **k: fake)
    out = _kch.get_health()
    # No cycles matched, no stalls, but call must return a full dict
    assert out["cycle"]["cycles_seen_last_hour"] == 0
    assert out["stalls_last_24h"] == 0


def test_env_defaults_reflected(monkeypatch):
    from core import kai_core_health as _kch
    monkeypatch.setattr(_kch, "_read_journal", lambda *a, **k: "")
    monkeypatch.setenv("KAI_ADVANCE_BUILDS_TIMEOUT", "180")
    monkeypatch.setenv("KAI_STALE_WARN_CYCLES", "5")
    out = _kch.get_health()
    assert out["advance_builds_timeout_env"] == 180
    assert out["stale_warn_cycles_env"] == 5
