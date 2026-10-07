"""ToS/legality policy gate (core/providers/gate) — TDD suite.

Covers the full decision matrix, the blocking precondition
``ensure_automation_allowed``, the "gate before any provider action" guarantee,
event/audit emission, and the Vault high-risk folding-in fix.
"""

import pytest

from core.providers import (
    AutomationBlocked,
    AutomationPolicy,
    GateOutcome,
    HumanConfirmationRequired,
    NotApplicableError,
    ProviderAdapter,
    ProviderDescriptor,
    ensure_automation_allowed,
    get_ready_adapter,
    policy_gate,
    register_adapter,
    register_provider,
    set_policy_override,
)
from core.providers import gate as gate_module


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("AI_ORCHESTRATOR_MEMORY_DIR", str(tmp_path))
    from core.providers import manager

    manager.reset_registry()
    return tmp_path


@pytest.fixture
def bus_spy(monkeypatch):
    events = []

    def _publish(topic, payload, *a, **k):
        events.append((topic, payload))
        return 0

    monkeypatch.setattr("core.kai_event_bus.publish", _publish)
    return events


@pytest.fixture
def audit_spy(monkeypatch):
    events = []

    def _audit(**kwargs):
        events.append(kwargs)

    monkeypatch.setattr("core.audit_logger.log_audit_event", _audit)
    return events


def _register(policy, pid=None, source=None):
    pid = pid or f"prov-{policy.value.lower()}"
    descriptor = ProviderDescriptor(
        provider_id=pid,
        display_name=pid,
        official_domain=f"{pid}.example",
        account_types=["user"],
        automation_policy=policy,
        policy_source=source or f"https://{pid}.example/tos",
    )
    register_provider(descriptor)
    return descriptor


class RecordingAdapter(ProviderAdapter):
    def __init__(self, descriptor, calls):
        self._descriptor = descriptor
        self.calls = calls

    @property
    def descriptor(self):
        return self._descriptor

    def registration(self, **k):
        self.calls.append("registration")
        return "registered"


# -- decision matrix -----------------------------------------------------------


@pytest.mark.parametrize(
    "policy,outcome,requires_human",
    [
        (AutomationPolicy.ALLOWED, GateOutcome.ALLOW, False),
        (AutomationPolicy.UNKNOWN, GateOutcome.REQUIRE_HUMAN_CONFIRMATION, True),
        (AutomationPolicy.PROHIBITED, GateOutcome.REFUSE_AUTOMATION, True),
    ],
)
def test_gate_matrix(policy, outcome, requires_human):
    source = f"src-{policy.value}"
    _register(policy, source=source)
    decision = policy_gate(f"prov-{policy.value.lower()}")
    assert decision.decision is outcome
    assert decision.requires_human is requires_human
    assert decision.automation_policy is policy
    assert decision.policy_source == source
    assert decision.reason
    assert decision.evaluated_at


def test_gate_allowed_may_automate():
    _register(AutomationPolicy.ALLOWED)
    decision = policy_gate("prov-allowed")
    assert decision.decision is GateOutcome.ALLOW
    assert decision.requires_human is False


def test_gate_unknown_requires_confirmation():
    _register(AutomationPolicy.UNKNOWN)
    decision = policy_gate("prov-unknown")
    assert decision.decision is GateOutcome.REQUIRE_HUMAN_CONFIRMATION
    assert decision.requires_human is True
    assert "unknown" in decision.reason.lower()


def test_gate_prohibited_routes_to_human_takeover():
    _register(AutomationPolicy.PROHIBITED)
    decision = policy_gate("prov-prohibited")
    assert decision.decision is GateOutcome.REFUSE_AUTOMATION
    assert decision.requires_human is True
    assert "prohibit" in decision.reason.lower()


def test_gate_accepts_descriptor_or_id():
    descriptor = _register(AutomationPolicy.ALLOWED)
    assert policy_gate(descriptor).decision is GateOutcome.ALLOW
    assert policy_gate("prov-allowed").decision is GateOutcome.ALLOW


def test_gate_unknown_provider_raises():
    with pytest.raises(Exception):
        policy_gate("ghost")


# -- events / audit ------------------------------------------------------------


def test_gate_evaluations_are_audited_and_published(bus_spy, audit_spy):
    _register(AutomationPolicy.PROHIBITED)
    policy_gate("prov-prohibited")
    gate_events = [(t, p) for t, p in bus_spy if t == "provider.policy.gate"]
    assert len(gate_events) == 1
    assert gate_events[0][1]["requires_human"] is True
    assert gate_events[0][1]["decision"] == "refuse_automation"
    assert any(e["event_type"] == "provider.policy.gate" for e in audit_spy)


# -- blocking precondition -----------------------------------------------------


def test_ensure_automation_allowed_returns_decision_on_allowed():
    _register(AutomationPolicy.ALLOWED)
    decision = ensure_automation_allowed("prov-allowed")
    assert decision.decision is GateOutcome.ALLOW


def test_ensure_automation_allowed_raises_for_unknown():
    _register(AutomationPolicy.UNKNOWN)
    with pytest.raises(HumanConfirmationRequired) as exc:
        ensure_automation_allowed("prov-unknown")
    assert exc.value.decision.decision is GateOutcome.REQUIRE_HUMAN_CONFIRMATION
    assert exc.value.decision.requires_human is True


def test_ensure_automation_allowed_raises_for_prohibited():
    _register(AutomationPolicy.PROHIBITED)
    with pytest.raises(AutomationBlocked) as exc:
        ensure_automation_allowed("prov-prohibited")
    assert exc.value.decision.decision is GateOutcome.REFUSE_AUTOMATION
    assert exc.value.decision.requires_human is True
    assert exc.value.decision.policy_source


def test_override_can_tighten_to_prohibited():
    _register(AutomationPolicy.ALLOWED)
    set_policy_override(
        "prov-allowed",
        AutomationPolicy.PROHIBITED,
        policy_source="https://prov-allowed.example/tos#automation",
        reason="reviewed",
    )
    with pytest.raises(AutomationBlocked):
        ensure_automation_allowed("prov-allowed")


# -- gate before any provider action -------------------------------------------


def test_gate_is_called_before_provider_action(monkeypatch):
    descriptor = _register(AutomationPolicy.ALLOWED)
    calls = []
    register_adapter(RecordingAdapter(descriptor, calls))

    seen = []
    original = gate_module.ensure_automation_allowed

    def spy(provider_id):
        assert calls == []  # no provider action happened before the gate
        seen.append(provider_id)
        return original(provider_id)

    monkeypatch.setattr(gate_module, "ensure_automation_allowed", spy)

    adapter = get_ready_adapter("prov-allowed")
    assert seen == ["prov-allowed"]
    assert adapter.registration() == "registered"
    assert calls == ["registration"]


def test_prohibited_provider_blocks_before_adapter_handed_back(monkeypatch):
    descriptor = _register(AutomationPolicy.PROHIBITED)
    calls = []
    register_adapter(RecordingAdapter(descriptor, calls))

    seen = []
    original = gate_module.ensure_automation_allowed

    def spy(provider_id):
        seen.append(provider_id)
        return original(provider_id)

    monkeypatch.setattr(gate_module, "ensure_automation_allowed", spy)

    with pytest.raises(AutomationBlocked):
        get_ready_adapter("prov-prohibited")
    assert seen == ["prov-prohibited"]
    assert calls == []  # no provider action ever ran


# -- Vault high-risk folding-in (additive fix) ---------------------------------


def test_vault_high_risk_patterns_cover_account_and_identity_secrets():
    from core.vault.broker import HIGH_RISK_PATTERNS, is_high_risk

    assert "secrets/accounts/*" in HIGH_RISK_PATTERNS
    assert "secrets/identities/*" in HIGH_RISK_PATTERNS
    assert "secrets/telegram/*" in HIGH_RISK_PATTERNS  # additive: nothing removed
    assert is_high_risk("secrets/accounts/github/acct-1") is True
    assert is_high_risk("secrets/identities/ident-abc") is True
    assert is_high_risk("secrets/telegram/bot-token") is True
    assert is_high_risk("secrets/public/readme") is False
