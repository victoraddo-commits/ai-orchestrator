"""Provider Registry (core/providers) — TDD suite.

Isolation: AI_ORCHESTRATOR_MEMORY_DIR -> tmp dir; registry re-seeded per test.
Event bus and audit are spied exactly as in the step-1 suites.
"""

import importlib
import json

import pytest

from core.providers import (
    AutomationPolicy,
    DuplicateAdapter,
    DuplicateProvider,
    GateDecision,
    GateOutcome,
    NotApplicableError,
    NOT_APPLICABLE,
    ProviderAdapter,
    ProviderDescriptor,
    ProviderNotFound,
    discover,
    get_adapter,
    get_provider,
    get_ready_adapter,
    list_adapters,
    list_providers,
    register_adapter,
    register_provider,
    set_policy_override,
)
from core.providers import store
from core.agentguard.guard import (
    AgentGuard,
    ActionRequest,
    ActionType,
    RiskLevel,
    Decision,
)


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


def _desc(**overrides):
    payload = {
        "provider_id": "testprov",
        "display_name": "Test Prov",
        "official_domain": "testprov.example",
        "registration_url": "https://testprov.example/signup",
        "account_types": ["user"],
        "automation_policy": AutomationPolicy.ALLOWED,
        "policy_source": "https://testprov.example/tos",
    }
    payload.update(overrides)
    return ProviderDescriptor(**payload)


class FullAdapter(ProviderAdapter):
    """Conformance adapter implementing every interface method."""

    def __init__(self, descriptor):
        self._descriptor = descriptor

    @property
    def descriptor(self):
        return self._descriptor

    def discovery(self, **k):
        return "discovery"

    def registration(self, **k):
        return "registration"

    def verification(self, **k):
        return "verification"

    def authentication(self, **k):
        return "authentication"

    def security_setup(self, **k):
        return "security_setup"

    def profile_setup(self, **k):
        return "profile_setup"

    def recovery(self, **k):
        return "recovery"

    def publishing(self, **k):
        return "publishing"

    def account_status(self, **k):
        return "account_status"


class PartialAdapter(ProviderAdapter):
    """Partial adapter: one implemented op, one explicit NOT_APPLICABLE."""

    def __init__(self, descriptor):
        self._descriptor = descriptor

    @property
    def descriptor(self):
        return self._descriptor

    def discovery(self, **k):
        return "stub-discovery"

    def account_status(self, **k):
        return NOT_APPLICABLE


# -- schema --------------------------------------------------------------------


def test_descriptor_defaults_and_enum():
    d = ProviderDescriptor(
        provider_id="Acme", display_name="Acme", account_types=["user"]
    )
    assert d.provider_id == "acme"  # normalized
    assert d.automation_policy == AutomationPolicy.UNKNOWN  # fail-closed default
    assert d.official_domain is None
    assert AutomationPolicy.PROHIBITED.value == "PROHIBITED"


def test_descriptor_rejects_secret_fields_and_bad_id():
    with pytest.raises(Exception):
        ProviderDescriptor(
            provider_id="x", display_name="x", account_types=["u"], password="hunter2"
        )
    with pytest.raises(Exception):
        ProviderDescriptor(provider_id="Bad Id!", display_name="x", account_types=["u"])
    with pytest.raises(Exception):
        ProviderDescriptor(provider_id="x", display_name="x", account_types=[])


def test_descriptor_rejects_invalid_domain():
    with pytest.raises(Exception):
        ProviderDescriptor(
            provider_id="x", display_name="x", account_types=["u"], official_domain="not a domain"
        )


# -- registry CRUD -------------------------------------------------------------


def test_register_get_list_and_duplicate_prevention():
    registered = register_provider(_desc(provider_id="acme", display_name="Acme"))
    assert registered.provider_id == "acme"
    assert get_provider("acme").display_name == "Acme"
    ids = [p.provider_id for p in list_providers()]
    assert "acme" in ids and "generic" in ids  # built-in stub present
    with pytest.raises(DuplicateProvider):
        register_provider(_desc(provider_id="acme", display_name="Acme 2"))


def test_get_missing_raises():
    with pytest.raises(ProviderNotFound):
        get_provider("nope")


def test_register_emits_event_and_audit(bus_spy, audit_spy):
    register_provider(_desc(provider_id="acme"))
    assert "provider.registered" in [t for t, _ in bus_spy]
    assert any(e["event_type"] == "provider.register" for e in audit_spy)


# -- discovery -----------------------------------------------------------------


def test_discovery_resolution_forms(bus_spy, audit_spy):
    register_provider(
        _desc(provider_id="example-co", display_name="Example Co", official_domain="example.com")
    )
    assert get_provider("example-co")
    for query in (
        "example-co",
        "Example Co",
        "example.com",
        "https://www.example.com/register",
        "http://example.com:443/path?q=1",
    ):
        assert discover(query).provider_id == "example-co", query
    assert "provider.discovered" in [t for t, _ in bus_spy]
    assert any(e["event_type"] == "provider.discover" for e in audit_spy)


def test_discover_unknown_raises():
    with pytest.raises(ProviderNotFound):
        discover("does-not-exist.example")


# -- adapter interface ---------------------------------------------------------


def test_adapter_is_partially_implementable():
    with pytest.raises(TypeError):
        ProviderAdapter()  # abstract: descriptor required

    partial = PartialAdapter(_desc(provider_id="partial"))
    register_adapter(partial)
    assert get_adapter("partial").discovery() == "stub-discovery"
    assert get_adapter("partial").account_status() == NOT_APPLICABLE
    with pytest.raises(NotApplicableError):
        partial.registration()
    with pytest.raises(NotImplementedError):
        partial.registration()


def test_adapter_full_conformance():
    adapter = FullAdapter(_desc(provider_id="full"))
    register_adapter(adapter)
    assert get_adapter("full") is adapter
    assert adapter.discovery() == "discovery"
    assert adapter.registration() == "registration"
    assert adapter.verification() == "verification"
    assert adapter.authentication() == "authentication"
    assert adapter.security_setup() == "security_setup"
    assert adapter.profile_setup() == "profile_setup"
    assert adapter.recovery() == "recovery"
    assert adapter.publishing() == "publishing"
    assert adapter.account_status() == "account_status"
    assert len(list_adapters()) >= 1


def test_adapter_duplicate_prevention():
    d = _desc(provider_id="dup-adapter")
    register_adapter(FullAdapter(d))
    with pytest.raises(DuplicateAdapter):
        register_adapter(FullAdapter(d))


def test_register_adapter_auto_registers_descriptor():
    register_adapter(FullAdapter(_desc(provider_id="auto-reg")))
    assert get_provider("auto-reg").provider_id == "auto-reg"


# -- persistence / secret boundary --------------------------------------------


def test_store_boundary_rejects_secret_override():
    with pytest.raises(Exception):
        store.update_overrides(
            lambda recs: recs + [{"provider_id": "x", "password": "p"}]
        )


def test_policy_override_persists_and_applies(isolated, bus_spy, audit_spy):
    set_policy_override(
        "generic",
        AutomationPolicy.PROHIBITED,
        policy_source="https://example.invalid/tos#automation",
        reason="operator confirmed automation prohibited",
    )
    assert get_provider("generic").automation_policy == AutomationPolicy.PROHIBITED
    assert "provider.policy.override" in [t for t, _ in bus_spy]
    assert any(e["event_type"] == "provider.policy.override" for e in audit_spy)

    path = isolated / "provider_overrides.json"
    assert path.exists()
    assert json.loads(path.read_text())["schema_version"] == 1

    importlib.reload(store)
    assert store.effective("generic").automation_policy == AutomationPolicy.PROHIBITED


def test_set_policy_override_validates():
    with pytest.raises(ProviderNotFound):
        set_policy_override("nope", AutomationPolicy.ALLOWED, "src")
    with pytest.raises(ValueError):
        set_policy_override("generic", "BOGUS", "src")


# -- AgentGuard mapping --------------------------------------------------------


def test_agentguard_provider_action_mapping():
    guard = AgentGuard()
    discover_result = guard.check_action(
        ActionRequest(
            agent_id="onboarding",
            user_id="kai",
            action_type=ActionType.PROVIDER_DISCOVER,
            resource="provider/example-co",
            details="resolve provider descriptor",
        )
    )
    assert discover_result.risk_level == RiskLevel.LOW
    assert discover_result.decision == Decision.ALLOW

    automate = guard.check_action(
        ActionRequest(
            agent_id="onboarding",
            user_id="kai",
            action_type=ActionType.PROVIDER_AUTOMATION,
            resource="provider/example-co",
            details="run provider adapter action",
        )
    )
    assert automate.risk_level == RiskLevel.MEDIUM
    assert automate.decision == Decision.ALLOW
