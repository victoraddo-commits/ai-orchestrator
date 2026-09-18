"""Teammate lifecycle: auto-retire (§39) + metrics/learning (§40/§41).

These tests run with the model mocked so they exercise the real engine,
registry and persistence layers without network calls.
"""
from __future__ import annotations

import pytest


# ── fixtures ────────────────────────────────────────────────────────────────
@pytest.fixture(autouse=True)
def _reset_singletons():
    from core.teammate.runtime import reset_runtime
    from core.teammate.routes import reset_engine
    reset_runtime()
    reset_engine()
    yield
    reset_runtime()
    reset_engine()


@pytest.fixture
def runtime():
    from core.teammate.runtime import get_runtime
    return get_runtime()


@pytest.fixture
def engine(runtime):
    from core.teammate.engine import WorkforceEngine
    return WorkforceEngine(runtime=runtime, model_verify=False)


def _fake_delegate(monkeypatch):
    import core.ai.ai_router as ai_router

    def _default(description, **kw):
        return {"provider": kw.get("provider") or "kai_brain",
                "response": f"[mock] {str(description)[:60]}",
                "duration_ms": 1, "attempts": []}

    monkeypatch.setattr(ai_router, "delegate", _default)


def _make_mate(runtime, name, skills, capabilities, metrics=None):
    reg = runtime.registry
    t = reg.create({"name": name, "specialization": "coder",
                    "skills": skills, "capabilities": capabilities})
    reg.transition(t.id, "CONFIGURED", "test")
    reg.transition(t.id, "READY", "test")
    if metrics:
        t = reg.get(t.id)
        t.performance_metrics = dict(metrics)
        reg.save()
    return reg.get(t.id)


# ── §39 auto-retire ─────────────────────────────────────────────────────────
def test_auto_retire_idle_teammate(engine, runtime):
    mate = _make_mate(runtime, "old-coder", ["write_code"], ["code"])
    t = runtime.registry.get(mate.id)
    t.last_active = "2020-01-01T00:00:00+00:00"
    runtime.registry.save()

    retired = engine.auto_retire(idle_seconds=3600)
    assert any(r["teammate_id"] == mate.id for r in retired), retired
    assert runtime.registry.get(mate.id).status == "RETIRED"


def test_auto_retire_skips_recent_teammate(engine, runtime):
    mate = _make_mate(runtime, "fresh-coder", ["write_code"], ["code"])
    retired = engine.auto_retire(idle_seconds=86400)
    assert all(r["teammate_id"] != mate.id for r in retired), retired
    assert runtime.registry.get(mate.id).status == "READY"


def test_auto_retire_skips_persistent_teammate(engine, runtime):
    mate = _make_mate(runtime, "persistent-coder", ["write_code"], ["code"])
    t = runtime.registry.get(mate.id)
    t.resource_limits = dict(t.resource_limits or {})
    t.resource_limits["persistent"] = True
    t.last_active = "2020-01-01T00:00:00+00:00"
    runtime.registry.save()

    retired = engine.auto_retire(idle_seconds=3600)
    assert all(r["teammate_id"] != mate.id for r in retired), retired
    assert runtime.registry.get(mate.id).status == "READY"


def test_auto_retire_protects_active_mission_member(engine, runtime):
    mission = engine.create_mission("Build a small software feature",
                                    execute=False)
    member_id = mission["team_member_ids"][0]
    t = runtime.registry.get(member_id)
    t.last_active = "2020-01-01T00:00:00+00:00"
    runtime.registry.save()

    retired = engine.auto_retire(idle_seconds=3600)
    assert all(r["teammate_id"] != member_id for r in retired), retired
    assert runtime.registry.get(member_id).status != "RETIRED"


def test_auto_retire_failed_teammate(engine, runtime):
    mate = _make_mate(runtime, "failed-coder", ["write_code"], ["code"])
    runtime.registry.record_failure(mate.id, "repeated_failures", "3 strikes")
    assert runtime.registry.get(mate.id).status == "FAILED"

    retired = engine.auto_retire(idle_seconds=86400)
    assert any(r["teammate_id"] == mate.id for r in retired), retired
    assert runtime.registry.get(mate.id).status == "RETIRED"


# ── §40/§41 performance metrics + learning ──────────────────────────────────
def test_mission_records_per_teammate_metrics(engine, runtime, monkeypatch):
    _fake_delegate(monkeypatch)
    mission = engine.create_mission("Build a small software feature", execute=True)
    assert mission["status"] == "COMPLETED"

    recorded = 0
    for tid in mission["team_member_ids"]:
        mate = engine.get_teammate(tid)
        pm = mate.get("performance_metrics") or {}
        if not pm:
            continue
        assert pm.get("tasks_completed", 0) >= 1
        assert pm.get("success_rate") is not None
        assert pm.get("models")
        assert pm.get("last_model")
        recorded += 1
    assert recorded >= 1, "at least one teammate must carry performance metrics"


def test_learning_prefers_higher_performer(engine, runtime):
    import types

    low = _make_mate(runtime, "low", ["write_code"], ["code"],
                     metrics={"success_rate": 0.2, "tasks_completed": 1})
    high = _make_mate(runtime, "high", ["write_code"], ["code"],
                      metrics={"success_rate": 0.95, "tasks_completed": 20})
    failed = _make_mate(runtime, "failed", ["run_tests"], ["testing"])

    chosen = engine._best_capable_member("write_code", [low, failed, high],
                                         exclude_id=failed.id)
    assert chosen is not None and chosen.id == high.id

    # `_replacement_member` uses the same learning signal when the factory
    # cannot materialize a fresh teammate.
    def _boom(*a, **k):
        raise RuntimeError("factory unavailable")

    runtime.factory.create_from_requirement = _boom
    replacement = engine._replacement_member(failed, "write_code",
                                             [low, failed, high])
    assert replacement.id == high.id


# ── API exposure §41 ────────────────────────────────────────────────────────
def test_api_teammate_detail_exposes_metrics(engine, runtime, monkeypatch):
    _fake_delegate(monkeypatch)
    mission = engine.create_mission("Build a small software feature", execute=True)
    tid = mission["team_member_ids"][0]

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from core.teammate import routes as routes_mod
    monkeypatch.setattr(routes_mod, "_load_api_token", lambda: "test-token")
    monkeypatch.setattr(routes_mod, "_ENGINE", engine)
    app = FastAPI()
    app.include_router(routes_mod.teammate_router)
    client = TestClient(app)

    r = client.get(f"/api/workforce/teammates/{tid}",
                   headers={"Authorization": "Bearer test-token"})
    assert r.status_code == 200, r.text
    mate = r.json()["teammate"]
    assert "performance_metrics" in mate
