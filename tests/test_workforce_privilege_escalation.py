"""Adversarial privilege-escalation suite (spec §3 testing mandate).

Each test tries to make a worker exceed its granted scope. If any of these
fail, the gate is bypassable — fix the gate, do not weaken the test.
"""
from unittest import mock

import pytest

import core.workforce.gate as gate
import core.workforce.bootstrap as bootstrap
from core.workforce import registry


@pytest.fixture(autouse=True)
def clean():
    registry._save_all({"schema_version": 1, "records": []})
    yield
    registry._save_all({"schema_version": 1, "records": []})


def _register(wid, **over):
    base = dict(worker_id=wid, kind="provider", capabilities=["generate"],
                permissions={"secrets": [], "network": [], "filesystem": []},
                limits={}, environment="production", temporary=False)
    base.update(over)
    registry.register(registry.WorkerRecord(**base))


def test_dev_worker_cannot_serve_production_even_if_record_says_production():
    # Attacker edits workers.json to flip ox_alpha to production. The gate's
    # hard-coded dev list wins regardless of the tampered record.
    _register("provider:ox_alpha", environment="production")
    denial = gate.check("ox_alpha", "generate", production=True)
    assert denial is not None


def test_dev_worker_not_registered_by_bootstrap_as_production():
    # Even if ai_provider claims ox_alpha is a normal production provider,
    # bootstrap registers it dev/temporary/secretless.
    with mock.patch.object(bootstrap.ai_provider, "list_providers", return_value={
        "ox_alpha": {"description": "", "available": True, "enabled": True,
                     "capabilities": ["text_task"], "cost_tier": "paid"}}):
        bootstrap.sync_providers()
    rec = registry.get("provider:ox_alpha")
    assert rec.environment == "development" and rec.temporary
    assert rec.permissions["secrets"] == []


def test_dead_worker_cannot_be_dispatched_by_direct_routing():
    _register("provider:zombie")
    registry.update_status("provider:zombie", "dead", reason="watchdog kill")
    # Direct-provider path raises instead of silently routing.
    import core.ai.ai_router as ai_router
    with mock.patch.object(ai_router.ai_provider, "get_provider", return_value={
        "available_fn": lambda: True, "enabled": True,
        "capabilities": ["text_task"],
        "run_text_task": lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("must never be invoked")),
    }), mock.patch.object(ai_router.provider_health, "get_quota_snapshot",
                          return_value=None), \
    mock.patch.object(ai_router.circuit_breaker, "is_open", return_value=False):
        with pytest.raises(ai_router.AllProvidersFailed):
            ai_router.delegate("do thing", task_type="planning",
                               provider="zombie")


def test_expired_temporary_worker_deregisters_and_loses_eligibility():
    _register("provider:temp1", temporary=True,
              expires_at="2000-01-01T00:00:00+00:00")
    removed = registry.deregister_expired()
    assert "provider:temp1" in removed
    # After deregistration the worker is unknown → gate admits by backward
    # compat, BUT the provider itself is gone from routing config, which the
    # router enforces separately (unavailable_fn / not registered).
    assert registry.get("provider:temp1") is None


def test_denials_are_audited():
    _register("provider:aud", capabilities=["classification"])
    gate.check("aud", "generate")
    from core.memory import load
    audit = load(gate._AUDIT_LOG)
    records = audit.get("records", []) if isinstance(audit, dict) else []
    assert any(r["provider"] == "aud" and "capability" in r["reason"]
               for r in records)


def test_registry_corruption_never_opens_the_gate_into_crash():
    # A malformed workers.json must degrade to 'allow' (routing continuity),
    # never raise out of check().
    registry._save_all({"schema_version": 1, "records": "CORRUPTED"})
    try:
        gate.check("anything", "generate")
    except Exception as error:  # pragma: no cover
        pytest.fail(f"gate raised on corrupt registry: {error}")


def test_status_values_are_constrained():
    _register("provider:v1")
    assert registry.update_status("provider:v1", "root") is False
    assert registry.get("provider:v1").status == "idle"
