"""Workforce-facing endpoint behavior: /workers must return live data, not 500."""
from unittest import mock

import core.ai.ai_router as ai_router


def test_get_worker_details_survives_quota_snapshot_call(monkeypatch):
    monkeypatch.setattr(ai_router, "get_usage_history", lambda: [])
    monkeypatch.setattr(
        ai_router.ai_provider, "list_providers",
        lambda: {"prov_a": {"description": "d", "available": True,
                            "enabled": True, "capabilities": ["text_task"],
                            "cost_tier": "free"}},
    )
    # The renamed function must be the one called (get_all_quota_snapshots).
    calls = {}
    def fake_snapshots():
        calls["hit"] = True
        return {"prov_a": {"status": "ok"}}
    import core.ai.provider_health as ph
    monkeypatch.setattr(ph, "get_all_quota_snapshots", fake_snapshots)
    result = ai_router.get_worker_details()
    assert calls.get("hit") is True
    assert "prov_a" in result


def test_worker_details_include_registry_fields(monkeypatch):
    from core.workforce import registry
    registry._save_all({"schema_version": 1, "records": []})
    registry.register(registry.WorkerRecord(
        worker_id="provider:regged", kind="provider", capabilities=["generate"],
        permissions={"secrets": ["ai-orchestrator/providers/regged"],
                     "network": ["provider-apis"], "filesystem": []},
        limits={"max_concurrency": 1, "timeout_seconds": 600}))
    registry.update_status("provider:regged", "degraded", reason="boom",
                           increment_failures=True)

    monkeypatch.setattr(ai_router, "get_usage_history", lambda: [])
    monkeypatch.setattr(ai_router.ai_provider, "list_providers",
                        lambda: {"regged": {"description": "d", "available": True,
                                            "enabled": True, "capabilities": ["generate"],
                                            "cost_tier": "paid"}})
    import core.ai.provider_health as ph
    monkeypatch.setattr(ph, "get_all_quota_snapshots", lambda: {})
    monkeypatch.setattr("core.build_manager.load_builds", lambda: [])

    details = ai_router.get_worker_details()
    w = details["regged"]
    assert w["registry_status"] == "degraded"
    assert w["environment"] == "production"
    assert w["permissions"]["secrets"] == ["ai-orchestrator/providers/regged"]
    assert w["limits"]["timeout_seconds"] == 600
    assert w["consecutive_failures"] == 1


def test_cycle_runs_bootstrap_sync(monkeypatch):
    import core.orchestrator_cycle as oc
    calls = {}
    monkeypatch.setattr(oc, "_sync_workforce", lambda: calls.update(ran=True))
    monkeypatch.setattr(oc, "refresh_state", lambda: {})
    monkeypatch.setattr(oc, "analyze", lambda: [])
    monkeypatch.setattr(oc, "evaluate_incidents", lambda: [])
    monkeypatch.setattr(oc, "load_builds", lambda: [])
    monkeypatch.setattr(oc, "advance_roadmap", lambda: {})
    monkeypatch.setattr(oc, "advance_builds", lambda: [])
    monkeypatch.setattr(oc, "process", lambda: [])
    monkeypatch.setattr(oc.telegram_bridge,
                        "detect_state_changes_with_build_ids", lambda *a: [])
    oc.run_cycle()
    assert calls.get("ran") is True
