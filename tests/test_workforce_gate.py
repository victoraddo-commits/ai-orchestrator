"""Delegate-gate enforcement: dead/paused/dev-permission rules."""
import pytest

import core.workforce.gate as gate
from core.workforce import registry


def _reset(**provs):
    registry._save_all({"schema_version": 1, "records": []})
    defaults = dict(
        worker_id=None, kind="provider", capabilities=["generate"],
        permissions={"secrets": [], "network": ["provider-apis"], "filesystem": []},
        limits={}, environment="production", temporary=False,
    )
    for wid, over in provs.items():
        kw = dict(defaults)
        kw["worker_id"] = wid
        kw.update(over)
        registry.register(registry.WorkerRecord(**kw))


def test_unregistered_provider_is_allowed():
    # Backward compatibility: anything not in the registry routes normally.
    assert gate.check("never_registered", "generate") is None


def test_dead_provider_denied():
    _reset(**{"provider:d1": {}})
    registry.update_status("provider:d1", "dead", reason="watchdog")
    denial = gate.check("d1", "generate")
    assert denial is not None
    assert "dead" in denial


def test_paused_and_busy_ok_but_paused_denied():
    _reset(**{"provider:p1": {}})
    registry.update_status("provider:p1", "paused", reason="operator")
    assert gate.check("p1", "generate") is not None


def test_capability_mismatch_denied():
    _reset(**{"provider:c1": {"capabilities": ["classification"]}})
    assert gate.check("c1", "generate") is not None
    assert gate.check("c1", "classification") is None


def test_dev_provider_blocked_for_production_capability():
    _reset(**{"provider:ox_alpha": {"environment": "production"}})  # misconfig attempt
    # gate re-reads bootstrap guard: ox_alpha is in _DEV_ONLY_PROVIDERS so it
    # can never serve production work regardless of its stored record.
    denial = gate.check("ox_alpha", "generate", production=True)
    assert denial is not None
    assert "development" in denial


def test_no_capable_worker_error_type():
    err = gate.NoCapableWorkerError("all denied", attempts=[{"provider": "x"}])
    from core.ai.ai_router import AllProvidersFailed
    assert isinstance(err, AllProvidersFailed)
    assert err.attempts == [{"provider": "x"}]
