import core.config as config
from core.action_policy import risk_tier, requires_approval


def test_known_actions_are_classified_into_risk_tiers():
    assert risk_tier("restart_container") == "low"
    assert risk_tier("restart_monitoring_agent") == "low"
    assert risk_tier("clear_temp_files") == "low"
    assert risk_tier("resize_resources") == "medium"
    assert risk_tier("restart_production_service") == "medium"
    assert risk_tier("migrate_vm") == "high"
    assert risk_tier("shutdown_node") == "high"
    assert risk_tier("modify_networking") == "high"
    assert risk_tier("delete_resources") == "high"


def test_unknown_action_is_unclassified_and_always_requires_approval(monkeypatch):
    monkeypatch.setattr(config, "AUTONOMOUS_MODE", True)

    assert risk_tier("something_made_up") == "unknown"
    assert requires_approval("something_made_up") is True


def test_everything_requires_approval_while_autonomous_mode_is_off(monkeypatch):
    monkeypatch.setattr(config, "AUTONOMOUS_MODE", False)

    assert requires_approval("restart_container") is True
    assert requires_approval("resize_resources") is True
    assert requires_approval("migrate_vm") is True


def test_only_low_risk_skips_approval_when_autonomous_mode_is_on(monkeypatch):
    monkeypatch.setattr(config, "AUTONOMOUS_MODE", True)

    assert requires_approval("restart_container") is False
    assert requires_approval("resize_resources") is True
    assert requires_approval("migrate_vm") is True
