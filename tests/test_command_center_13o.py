"""13O: unit tests for the Kai Command Center summary endpoint."""

import pytest
from fastapi.testclient import TestClient

from core.api import app

client = TestClient(app)


# ── Endpoint exists ─────────────────────────────────────────────────────────

def test_command_center_summary_returns_200():
    response = client.get("/api/command-center/summary")
    assert response.status_code == 200


def test_command_center_summary_is_json():
    response = client.get("/api/command-center/summary")
    assert response.headers["content-type"].startswith("application/json")


# ── Top-level structure ─────────────────────────────────────────────────────

def test_command_center_summary_has_all_sections():
    body = client.get("/api/command-center/summary").json()
    assert "workforce" in body
    assert "build_queue" in body
    assert "provider_health" in body
    assert "kai_status" in body
    assert "approval_feed" in body
    assert "utilization" in body
    assert "performance" in body
    assert "build_timelines" in body
    assert "learning_summary" in body


# ── Workforce section ───────────────────────────────────────────────────────

def test_workforce_includes_all_registered_providers():
    body = client.get("/api/command-center/summary").json()
    workforce = body["workforce"]
    assert isinstance(workforce, dict)
    assert len(workforce) > 0, "at least one provider must be registered"


def test_workforce_each_entry_has_required_fields():
    body = client.get("/api/command-center/summary").json()
    for name, info in body["workforce"].items():
        for field in ("name", "kind", "model", "roles", "capabilities",
                      "coding_agent", "available", "cost_tier", "status",
                      "health", "success_rate", "average_duration_ms",
                      "queue_depth", "total_attempts"):
            assert field in info, f"workforce[{name}] missing {field}"


def test_workforce_available_is_bool():
    body = client.get("/api/command-center/summary").json()
    for name, info in body["workforce"].items():
        assert isinstance(info["available"], bool), \
            f"workforce[{name}].available must be bool, got {type(info['available'])}"


def test_workforce_queue_depth_is_int():
    body = client.get("/api/command-center/summary").json()
    for name, info in body["workforce"].items():
        assert isinstance(info["queue_depth"], int), \
            f"workforce[{name}].queue_depth must be int"


# ── Build Queue section ─────────────────────────────────────────────────────

def test_build_queue_has_grouping_keys():
    body = client.get("/api/command-center/summary").json()
    bq = body["build_queue"]
    for key in ("running", "waiting", "waiting_for_approval", "failed",
                "recently_completed"):
        assert key in bq, f"build_queue missing {key}"
        assert isinstance(bq[key], list), f"build_queue.{key} must be a list"


def test_build_queue_entries_have_required_fields():
    body = client.get("/api/command-center/summary").json()
    bq = body["build_queue"]
    all_entries = (bq["running"] + bq["waiting"] +
                   bq["waiting_for_approval"] + bq["failed"] +
                   bq["recently_completed"])
    for entry in all_entries:
        for field in ("id", "name", "status", "phase", "assigned_worker",
                      "start_time", "elapsed_seconds", "elapsed_display"):
            assert field in entry, f"build entry missing {field}"


# ── Provider Health section ─────────────────────────────────────────────────

def test_provider_health_includes_all_registered_providers():
    body = client.get("/api/command-center/summary").json()
    ph = body["provider_health"]
    assert isinstance(ph, dict)
    assert len(ph) > 0


def test_provider_health_each_entry_has_required_fields():
    body = client.get("/api/command-center/summary").json()
    for name, info in body["provider_health"].items():
        for field in ("name", "health", "health_score", "last_failure",
                      "consecutive_failures", "quota_status", "quota_detail",
                      "percent_remaining", "average_latency_ms", "cooldown_until"):
            assert field in info, f"provider_health[{name}] missing {field}"


def test_provider_health_score_is_int_between_0_and_100():
    body = client.get("/api/command-center/summary").json()
    for name, info in body["provider_health"].items():
        score = info["health_score"]
        assert isinstance(score, int), \
            f"provider_health[{name}].health_score must be int"
        assert 0 <= score <= 100, \
            f"provider_health[{name}].health_score={score} out of range [0,100]"


def test_provider_health_consecutive_failures_is_int():
    body = client.get("/api/command-center/summary").json()
    for name, info in body["provider_health"].items():
        assert isinstance(info["consecutive_failures"], int), \
            f"provider_health[{name}].consecutive_failures must be int"


# ── Kai Status section ──────────────────────────────────────────────────────

def test_kai_status_has_required_fields():
    body = client.get("/api/command-center/summary").json()
    ks = body["kai_status"]
    for field in ("name", "identity", "current_objective", "active_builds",
                  "active_build_id", "roadmap_phase", "task", "waiting_on",
                  "next_planned_action", "last_completed_action"):
        assert field in ks, f"kai_status missing {field}"


def test_kai_status_name_is_kai():
    body = client.get("/api/command-center/summary").json()
    assert body["kai_status"]["name"] == "Kai"


# ── Approval Feed section ───────────────────────────────────────────────────

def test_approval_feed_is_list():
    body = client.get("/api/command-center/summary").json()
    assert isinstance(body["approval_feed"], list)


def test_approval_feed_entries_have_required_fields():
    body = client.get("/api/command-center/summary").json()
    for entry in body["approval_feed"]:
        for field in ("id", "build_id", "approval_type", "title", "status",
                      "created_at"):
            assert field in entry, f"approval entry missing {field}"


# ── Utilization section ─────────────────────────────────────────────────────

def test_utilization_includes_all_registered_providers():
    body = client.get("/api/command-center/summary").json()
    util = body["utilization"]
    assert isinstance(util, dict)
    assert len(util) > 0


# ── Performance section ─────────────────────────────────────────────────────

def test_performance_includes_all_registered_providers():
    body = client.get("/api/command-center/summary").json()
    perf = body["performance"]
    assert isinstance(perf, dict)
    assert len(perf) > 0


def test_performance_each_entry_has_required_fields():
    body = client.get("/api/command-center/summary").json()
    for name, info in body["performance"].items():
        for field in ("name", "tasks_completed", "total_tasks", "success_rate",
                      "failure_rate", "avg_runtime_ms", "avg_retries",
                      "last_execution"):
            assert field in info, f"performance[{name}] missing {field}"


# ── Build Timelines section ─────────────────────────────────────────────────

def test_build_timelines_is_list():
    body = client.get("/api/command-center/summary").json()
    assert isinstance(body["build_timelines"], list)


def test_build_timelines_entries_have_required_fields():
    body = client.get("/api/command-center/summary").json()
    for entry in body["build_timelines"]:
        for field in ("build_id", "name", "status", "phase", "created_at", "stages"):
            assert field in entry, f"build_timeline entry missing {field}"


# ── Learning Summary section ────────────────────────────────────────────────

def test_learning_summary_has_all_categories():
    body = client.get("/api/command-center/summary").json()
    ls = body["learning_summary"]
    for key in ("preferred_architectures", "successful_patterns",
                "common_failures", "avoided_approaches"):
        assert key in ls, f"learning_summary missing {key}"
        assert isinstance(ls[key], list), f"learning_summary.{key} must be a list"


# ── _fmt_duration helper ────────────────────────────────────────────────────

def test_fmt_duration_seconds():
    from core.api import _fmt_duration
    assert _fmt_duration(30) == "30s"
    assert _fmt_duration(90) == "1m 30s"
    assert _fmt_duration(3600) == "1h 0m 0s"
    assert _fmt_duration(3723) == "1h 2m 3s"
    assert _fmt_duration(None) is None


# ── No hardcoded provider list ──────────────────────────────────────────────

def test_command_center_workforce_matches_provider_registry(isolated_memory):
    import core.ai_provider as ai_provider
    registered = set(ai_provider.list_providers().keys())
    body = client.get("/api/command-center/summary").json()
    workforce_names = set(body["workforce"].keys())
    assert registered == workforce_names, \
        "workforce keys must match ai_provider.list_providers() exactly"


def test_command_center_health_matches_provider_registry(isolated_memory):
    import core.ai_provider as ai_provider
    registered = set(ai_provider.list_providers().keys())
    body = client.get("/api/command-center/summary").json()
    health_names = set(body["provider_health"].keys())
    assert registered == health_names, \
        "provider_health keys must match ai_provider.list_providers() exactly"
