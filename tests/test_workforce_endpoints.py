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
