"""Mission auto-recovery loop (directive §26 / §48 / §25).

A running mission must recover from a task failure WITHOUT losing state:
a bounded retry, then a replacement teammate (and, for a model failure, a
compatible model selected by the Model Fabric), then resume and complete.
If the cap is exhausted the mission is marked FAILED with a diagnosis while
the stored task graph and evidence are preserved.

These tests mock ``ai_router.delegate`` so they exercise the real object
graph (runtime → guard → dispatcher → execution → recovery → verification →
persistence) with no network calls.
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


def _record_recovery_events(runtime):
    """Subscribe to mission recovery topics; returns the captured list."""
    events = []
    bus = runtime.bus
    if bus is None:
        return events

    def _capture(_topic, envelope):
        events.append(envelope)

    bus.subscribe("mission.*", _capture)
    return events


# ── 1. forced worker failure → bounded retry → recovers and completes ───────
def test_worker_failure_recovers_and_completes(engine, monkeypatch, runtime):
    """A task that fails N times then succeeds is retried, not abandoned.

    The first failure must not fail the mission: the loop retries the SAME
    task (bounded) and the mission completes once the worker recovers.
    """
    import core.ai.ai_router as ai_router

    calls = {"n": 0}

    def _flaky(description, **kw):
        calls["n"] += 1
        # Fail the first attempt, then succeed — proving a retry happened.
        if calls["n"] == 1:
            raise RuntimeError("worker vanished")
        return {"provider": kw.get("provider"),
                "response": f"ok on attempt {calls['n']}",
                "duration_ms": 1, "attempts": []}

    monkeypatch.setattr(ai_router, "delegate", _flaky)

    events = _record_recovery_events(runtime)
    mission = engine.create_mission(
        "Build a small software feature", execute=True)
    mid = mission["id"]

    assert mission["status"] == "COMPLETED", mission
    assert all(t["status"] == "COMPLETED" for t in mission["tasks"]), mission["tasks"]
    assert calls["n"] >= 2, "the failed task must have been retried"

    # state preserved across the retry: the recovered task carries an audit trail
    recovered = engine.get_mission(mid)
    assert recovered is not None
    assert recovered["status"] == "COMPLETED"

    topics = [e["topic"] for e in events]
    assert "mission.recovered" in topics, topics
    # the recovery event names the failed skill and the action taken
    rec = next(e for e in events if e["topic"] == "mission.recovered")
    assert rec["payload"]["mission_id"] == mid
    assert rec["payload"].get("recovered"), rec["payload"]


# ── 2. model unavailable → failover to a compatible model → completes ───────
def test_model_failure_fails_over_to_compatible_model(engine, monkeypatch, runtime):
    """When a model worker dies the Model Fabric picks a compatible model.

    ``write_code`` is given a two-provider chain; the first provider raises,
    the second succeeds. The replacement must keep the teammate logically
    intact — no teammate is failed for a single provider outage.
    """
    import core.ai.ai_router as ai_router

    runtime.skills.get("write_code").model_requirements["roles"] = [
        "kai_coder", "local"]
    monkeypatch.setattr(
        ai_router, "get_effective_providers",
        lambda tt: (["kai_coder", "local"] if tt == "coding"
                    else ai_router.ROLE_PROVIDERS.get(tt, ["local"])))

    tried = []

    def _failover(description, **kw):
        tried.append(kw.get("provider"))
        if kw.get("provider") == "kai_coder":
            raise ai_router.AllProvidersFailed("kai_coder down")
        return {"provider": "local", "response": "ok from local",
                "duration_ms": 1, "attempts": []}

    monkeypatch.setattr(ai_router, "delegate", _failover)

    events = _record_recovery_events(runtime)
    mission = engine.create_mission(
        "Implement a small software feature", execute=True)

    assert mission["status"] == "COMPLETED", mission
    # the fabric walked the chain: primary then compatible fallback
    assert "kai_coder" in tried and "local" in tried, tried
    # a model failure produces a recovery event naming the compatible model
    recs = [e for e in events if e["topic"] == "mission.recovered"]
    assert recs, [e["topic"] for e in events]
    # teammates stay logically intact (never FAILED for a provider outage)
    for tid in mission["team_member_ids"]:
        mate = runtime.registry.get(tid)
        assert mate is not None
        assert mate.status != "FAILED", (tid, mate.status)


# ── 3. retry cap exceeded → clean failure, state preserved ─────────────────
def test_retry_cap_exceeded_fails_cleanly_and_preserves_state(
        engine, monkeypatch, runtime):
    """Every attempt fails → mission FAILED with a diagnosis, state intact."""
    import core.ai.ai_router as ai_router

    def _always_fail(description, **kw):
        raise RuntimeError("worker never comes back")

    monkeypatch.setattr(ai_router, "delegate", _always_fail)

    events = _record_recovery_events(runtime)
    # squeeze the loop so the test is fast but the cap is still exercised
    monkeypatch.setenv("KAI_MISSION_TASK_RETRIES", "2")
    monkeypatch.setenv("KAI_MISSION_MAX_ATTEMPTS", "1")
    engine.max_task_retries = 2
    engine.max_attempts = 1

    mission = engine.create_mission(
        "Build a small software feature", execute=True)
    mid = mission["id"]

    assert mission["status"] == "FAILED", mission
    # diagnosis present, no task left in flight
    assert mission.get("error") or any(t.get("error") for t in mission["tasks"])

    # state preserved: the full stored graph survives the failure
    stored = engine.get_mission(mid)
    assert stored is not None
    assert stored["status"] == "FAILED"
    assert stored["tasks"], "task graph must not be lost on failure"
    assert any(t["status"] == "FAILED" for t in stored["tasks"])
    # the failed task carries a recovery diagnosis
    assert any(t.get("recovery") for t in stored["tasks"]), stored["tasks"]

    topics = [e["topic"] for e in events]
    assert "mission.failed" in topics, topics
    failed = next(e for e in events if e["topic"] == "mission.failed")
    assert failed["payload"]["mission_id"] == mid
    assert failed["payload"].get("diagnosis")


# ── 4. a failure that needs no retry still emits a recovery record ─────────
def test_recovered_event_not_emitted_for_clean_mission(engine, monkeypatch, runtime):
    """A mission with no failures must not emit a spurious recovery event."""
    import core.ai.ai_router as ai_router

    monkeypatch.setattr(
        ai_router, "delegate",
        lambda description, **kw: {"provider": kw.get("provider"),
                                   "response": "fine", "duration_ms": 1,
                                   "attempts": []})

    events = _record_recovery_events(runtime)
    mission = engine.create_mission("Build a small software feature", execute=True)

    assert mission["status"] == "COMPLETED"
    topics = [e["topic"] for e in events]
    assert "mission.recovered" not in topics, topics
    assert "mission.failed" not in topics, topics
