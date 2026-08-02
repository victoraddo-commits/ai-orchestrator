"""13J: unit tests for provider dashboard extension and scheduler snapshot."""

import pytest

import core.build_manager as build_manager
from core.ai.ai_router import get_provider_dashboard, _derive_health
from core.build_manager import get_scheduler_snapshot


# ── get_provider_dashboard (extended) ────────────────────────────────────────

def test_provider_dashboard_includes_health_field(isolated_memory):
    dashboard = get_provider_dashboard()
    for name, info in dashboard.items():
        assert "health" in info, f"provider {name} missing 'health'"
        assert info["health"] in {
            "healthy", "degraded", "quota_exceeded", "error", "idle",
            "busy", "unknown",
        }, f"provider {name} has unexpected health: {info['health']}"


def test_provider_dashboard_includes_success_rate(isolated_memory):
    dashboard = get_provider_dashboard()
    for info in dashboard.values():
        assert "success_rate" in info
        sr = info["success_rate"]
        assert sr is None or isinstance(sr, float)


def test_provider_dashboard_includes_current_job_fields(isolated_memory):
    dashboard = get_provider_dashboard()
    for info in dashboard.values():
        assert "current_job" in info
        assert "current_job_name" in info
        assert "queue_depth" in info
        assert isinstance(info["queue_depth"], int)


def test_provider_dashboard_current_job_is_none_without_active_builds(isolated_memory):
    dashboard = get_provider_dashboard()
    for info in dashboard.values():
        assert info["current_job"] is None, (
            "no build should be generating without test data"
        )


def test_provider_dashboard_queue_depth_counts_active_builds(isolated_memory, monkeypatch):
    fake_builds = [
        {"id": "b1", "name": "build-1", "status": "REQUESTED", "generated_by": None},
        {"id": "b2", "name": "build-2", "status": "PLANNING", "generated_by": None},
        {"id": "b3", "name": "build-3", "status": "ARCHITECTURE_APPROVED", "generated_by": None},
        {"id": "b4", "name": "build-4", "status": "COMPLETED", "generated_by": None},
    ]
    monkeypatch.setattr(build_manager, "load_builds", lambda: fake_builds)

    dashboard = get_provider_dashboard()

    for info in dashboard.values():
        assert info["queue_depth"] == 3, (
            "3 actionable builds (REQUESTED, PLANNING, ARCHITECTURE_APPROVED)"
        )


def test_provider_dashboard_shows_current_job_when_generating(isolated_memory, monkeypatch):
    fake_builds = [
        {"id": "b1", "name": "Test App", "status": "GENERATING", "generated_by": "claude"},
    ]
    monkeypatch.setattr(build_manager, "load_builds", lambda: fake_builds)

    dashboard = get_provider_dashboard()

    claude = dashboard.get("claude", {})
    assert claude.get("current_job") == "b1"
    assert claude.get("current_job_name") == "Test App"
    assert claude.get("health") == "busy"


# ── _derive_health ───────────────────────────────────────────────────────────


def test_derive_health_unknown_when_not_available():
    info = {"available": False}
    quota = {}
    assert _derive_health("test", info, [], None, quota, None) == "unknown"


def test_derive_health_quota_exceeded():
    info = {"available": True}
    quota = {"status": "quota_exceeded", "detail": "out of credits"}
    assert _derive_health("test", info, [], None, quota, None) == "quota_exceeded"


def test_derive_health_degraded_on_error():
    info = {"available": True}
    quota = {"status": "error", "detail": "something went wrong"}
    assert _derive_health("test", info, [], None, quota, None) == "degraded"


def test_derive_health_busy_with_current_job():
    info = {"available": True}
    quota = {}
    assert _derive_health("test", info, [], None, quota, {"id": "b1"}) == "busy"


def test_derive_health_idle_with_no_attempts():
    info = {"available": True}
    quota = {}
    assert _derive_health("test", info, [], None, quota, None) == "idle"


def test_derive_health_healthy_at_80_percent():
    info = {"available": True}
    quota = {}
    assert _derive_health("test", info, ["a", "b", "c"], 0.8, quota, None) == "healthy"


def test_derive_health_degraded_below_80_percent():
    info = {"available": True}
    quota = {}
    assert _derive_health("test", info, ["a", "b", "c"], 0.5, quota, None) == "degraded"


def test_derive_health_error_below_50_percent():
    info = {"available": True}
    quota = {}
    assert _derive_health("test", info, ["a"], 0.3, quota, None) == "error"


def test_derive_health_healthy_if_no_success_rate():
    info = {"available": True}
    quota = {}
    assert _derive_health("test", info, ["a"], None, quota, None) == "healthy"


# ── get_scheduler_snapshot ───────────────────────────────────────────────────


def test_scheduler_snapshot_structure(isolated_memory):
    snapshot = get_scheduler_snapshot()
    assert "waiting_builds" in snapshot
    assert "running_builds" in snapshot
    assert "worker_assignments" in snapshot
    assert "parallel_capacity" in snapshot
    assert "parallel_enabled" in snapshot
    assert "total_builds" in snapshot
    assert isinstance(snapshot["parallel_capacity"], int)
    assert isinstance(snapshot["parallel_enabled"], bool)


def test_scheduler_snapshot_waiting_builds(isolated_memory, monkeypatch):
    fake_builds = [
        {"id": "b1", "name": "pending-1", "status": "REQUESTED", "created_at": "2026-01-01T00:00:00"},
        {"id": "b2", "name": "pending-2", "status": "ARCHITECTURE_APPROVED", "created_at": "2026-01-01T00:01:00"},
        {"id": "b3", "name": "running-1", "status": "GENERATING", "created_at": "2026-01-01T00:02:00", "generated_by": "claude"},
        {"id": "b4", "name": "done-1", "status": "COMPLETED", "created_at": "2026-01-01T00:03:00"},
    ]
    monkeypatch.setattr(build_manager, "load_builds", lambda: fake_builds)

    snapshot = get_scheduler_snapshot()

    assert len(snapshot["waiting_builds"]) == 2
    waiting_ids = {b["id"] for b in snapshot["waiting_builds"]}
    assert waiting_ids == {"b1", "b2"}


def test_scheduler_snapshot_running_builds(isolated_memory, monkeypatch):
    fake_builds = [
        {"id": "b1", "name": "gen-1", "status": "GENERATING", "created_at": "2026-01-01T00:00:00", "generated_by": "claude"},
        {"id": "b2", "name": "plan-1", "status": "PLANNING", "created_at": "2026-01-01T00:01:00"},
    ]
    monkeypatch.setattr(build_manager, "load_builds", lambda: fake_builds)

    snapshot = get_scheduler_snapshot()

    assert len(snapshot["running_builds"]) == 2
    running_ids = {b["id"] for b in snapshot["running_builds"]}
    assert running_ids == {"b1", "b2"}


def test_scheduler_snapshot_worker_assignments(isolated_memory, monkeypatch):
    fake_builds = [
        {"id": "b1", "name": "gen-1", "status": "GENERATING", "created_at": "2026-01-01T00:00:00", "generated_by": "claude"},
    ]
    monkeypatch.setattr(build_manager, "load_builds", lambda: fake_builds)

    snapshot = get_scheduler_snapshot()

    assert snapshot["worker_assignments"] == {"claude": "b1"}


def test_scheduler_snapshot_empty(isolated_memory, monkeypatch):
    monkeypatch.setattr(build_manager, "load_builds", lambda: [])

    snapshot = get_scheduler_snapshot()

    assert snapshot["waiting_builds"] == []
    assert snapshot["running_builds"] == []
    assert snapshot["worker_assignments"] == {}
