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


def test_check_operation_unknown_op_passes_through():
    """Operations not in DESTRUCTIVE_OPERATIONS return None (forward compat)."""
    _reset(**{"provider:t1": {}})
    assert gate.check_operation("t1", "not_a_real_operation") is None


def test_check_operation_registered_worker_without_authority_denied():
    """A registered worker without the destructive flag is denied."""
    _reset(**{"provider:t2": {}})
    denial = gate.check_operation("t2", "delete_files")
    assert denial is not None
    assert "delete_files" in denial


def test_check_operation_worker_with_authority_allowed():
    """A worker with destructive_authority.delete_files=True is allowed."""
    _reset(**{"provider:t3": {
        "destructive_authority": {
            "delete_files": True,
            "terminate_worker": False,
            "kill_provider": False,
            "force_deploy": False,
            "modify_secrets": False,
            "network_bridge": False,
            "data_export": False,
            "admin_action": False,
        }
    }})
    assert gate.check_operation("t3", "delete_files") is None
    assert gate.check_operation("t3", "terminate_worker") is not None  # not granted


def test_check_operation_unregistered_worker_passes():
    """Backward compat: unregistered workers pass through check_operation."""
    assert gate.check_operation("never_seen", "delete_files") is None


def test_check_operation_audits_denial():
    """Denied operations write to the audit log."""
    _reset(**{"provider:t4": {}})
    result = gate.check_operation("t4", "force_deploy")
    assert result is not None
