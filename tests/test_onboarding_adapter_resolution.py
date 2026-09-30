"""Onboarding engine adapter resolution (Task 7).

Focused unit tests for ``core.onboarding.engine``:

* ``SELECT_PROVIDER_ADAPTER`` resolves a registered/seeded adapter first and
  falls back to the universal ``GenericWebAdapter`` for any other domain;
* the ToS/legality gate still applies (an UNKNOWN provider pauses for a human);
* ``START_REGISTRATION`` honors a ``requires_human`` signal from the adapter
  (learn-then-pause for operator recipe review) while leaving the dedicated
  CAPTCHA / identity-verification states to the state machine.

Fully offline: no provider, browser, mail, SMS or network is touched.
"""

from __future__ import annotations

import pytest

from core.onboarding.engine import step
from core.onboarding.schema import (
    OnboardingSession,
    OnboardingState,
    now_iso,
)
from core.providers import (
    AdapterNotFound,
    AutomationPolicy,
    HumanConfirmationRequired,
    ProviderDescriptor,
    policy_gate,
    register_adapter,
    register_provider,
)
from core.providers import manager as provider_manager
from core.providers import store as provider_store


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("AI_ORCHESTRATOR_MEMORY_DIR", str(tmp_path))
    provider_manager.reset_registry()
    return tmp_path


# ---------------------------------------------------------------------------
# test doubles
# ---------------------------------------------------------------------------

class _Obs:
    def __init__(self):
        self.events, self.audits, self.checkpoints = [], [], []

    def publish(self, topic, payload, severity="informational"):
        self.events.append((topic, payload))

    def audit(self, event_type, details):
        self.audits.append((event_type, details))

    def checkpoint(self, mission_id, note, evidence):
        self.checkpoints.append((mission_id, note, evidence))


class _Human:
    def __init__(self):
        self.requests = []

    def request(self, action_type, mission_id, *, instructions="", provider=None):
        self.requests.append((action_type, mission_id, instructions))
        return "hact-1"

    def complete(self, action_id, *, reason=""):
        return None


class _Identity:
    display_name = "KAI"
    email = "kai@x.test"
    phone = "+233200000001"


class _IdentityPort:
    def resolve(self, identity_id):
        return _Identity()


class _Account:
    account_id = "acct-1"
    vault_reference = "secrets/accounts/fresh.example/acct-1"


class _AccountsPort:
    def ensure_account(self, record, descriptor, identity):
        return _Account()


class _BrowserPort:
    def open_session(self, identity_id, provider_id, mission_id):
        return {"session_id": "sess-1"}

    def perform(self, session_id, op, params=None):
        return {"snapshot": {"url": "https://fresh.example/signup", "title": "",
                             "text": "", "elements": [], "untrusted": True}}


class _ProviderPort:
    """Provider port with an explicit adapter (or gate error) injected."""

    def __init__(self, adapter=None, *, gate_error=None):
        self._adapter = adapter
        self._gate_error = gate_error

    def get_adapter(self, provider_id):
        if self._gate_error is not None:
            raise self._gate_error
        if self._adapter is None:
            raise AdapterNotFound(provider_id)
        return self._adapter

    def op(self, descriptor, operation, **kwargs):
        return {"not_applicable": False,
                "result": getattr(self._adapter, operation)(**kwargs)}


class _Pipe:
    def __init__(self, *, provider, human=None, obs=None):
        self.provider = provider
        self.human = human or _Human()
        self.identity = _IdentityPort()
        self.accounts = _AccountsPort()
        self.browser = _BrowserPort()
        self.obs = obs or _Obs()


def _descriptor(domain="fresh.example", *, policy="ALLOWED", email=True, phone=False):
    return ProviderDescriptor(
        provider_id=domain, display_name=domain, official_domain=domain,
        registration_url=f"https://{domain}/signup", account_types=["web"],
        browser_required=True, requires_email=email, requires_phone=phone,
        automation_policy=AutomationPolicy(policy), policy_source="test")


def _record(state, **extra):
    data = dict(mission_id="mis-1", objective="obj", provider_id="fresh.example",
                identity_id="ident-1", current_state=state,
                created_at=now_iso(), updated_at=now_iso())
    data.update(extra)
    return OnboardingSession(**data).model_dump(mode="json")


# ---------------------------------------------------------------------------
# SELECT_PROVIDER_ADAPTER
# ---------------------------------------------------------------------------

def test_select_provider_adapter_falls_back_to_generic(isolated):
    from core.providers.generic_web import GenericWebAdapter

    descriptor = _descriptor("fresh.example")
    register_provider(descriptor)
    pipe = _Pipe(provider=_ProviderPort(adapter=None))
    record = _record(OnboardingState.SELECT_PROVIDER_ADAPTER)

    step(record, descriptor, pipe)

    assert record["errors"] == []
    assert record["current_state"] == OnboardingState.CHECK_REQUIREMENTS.value
    adapter = provider_store.get_adapter("fresh.example")
    assert isinstance(adapter, GenericWebAdapter)
    assert adapter.descriptor.official_domain == "fresh.example"
    kinds = [e["kind"] for e in record["evidence"]]
    assert "provider_adapter" in kinds


def test_select_provider_adapter_prefers_registered_adapter(isolated):
    from core.providers.generic_web import GenericWebAdapter

    descriptor = _descriptor("seeded.example")
    register_provider(descriptor)
    seeded = GenericWebAdapter("seeded.example", descriptor=descriptor)
    register_adapter(seeded)
    pipe = _Pipe(provider=_ProviderPort(adapter=seeded))
    record = _record(OnboardingState.SELECT_PROVIDER_ADAPTER,
                     provider_id="seeded.example")

    step(record, descriptor, pipe)

    assert record["errors"] == []
    assert record["current_state"] == OnboardingState.CHECK_REQUIREMENTS.value
    assert provider_store.get_adapter("seeded.example") is seeded
    evidence = next(e for e in record["evidence"] if e["kind"] == "provider_adapter")
    assert evidence["ref"] == type(seeded).__name__


def test_unknown_policy_gate_still_pauses_for_human(isolated):
    descriptor = _descriptor("unknown.example", policy="UNKNOWN")
    register_provider(descriptor)
    decision = policy_gate(descriptor)
    gate_error = HumanConfirmationRequired(decision)
    human = _Human()
    pipe = _Pipe(provider=_ProviderPort(adapter=None, gate_error=gate_error),
                 human=human)
    record = _record(OnboardingState.SELECT_PROVIDER_ADAPTER,
                     provider_id="unknown.example")

    step(record, descriptor, pipe)

    assert record["status"] == "paused_human"
    assert record["current_state"] == OnboardingState.PAUSED_HUMAN.value
    assert record["human_action_type"] == "OTHER"
    assert len(human.requests) == 1
    assert provider_store.get_adapter("unknown.example") is None


# ---------------------------------------------------------------------------
# START_REGISTRATION honors requires_human
# ---------------------------------------------------------------------------

def test_start_registration_pauses_when_adapter_requests_human(isolated):
    from core.providers.generic_web import GenericWebAdapter

    descriptor = _descriptor("pause.example")
    register_provider(descriptor)

    class MarkingAdapter(GenericWebAdapter):
        def registration(self, **ctx):
            return {"status": "draft_learned", "requires_human": True,
                    "action_type": "OTHER", "instructions": "review recipe"}

    human = _Human()
    adapter = MarkingAdapter("pause.example", descriptor=descriptor)
    pipe = _Pipe(provider=_ProviderPort(adapter=adapter), human=human)
    record = _record(OnboardingState.START_REGISTRATION,
                     provider_id="pause.example", identity_id="ident-1")

    step(record, descriptor, pipe)

    assert record["current_state"] == OnboardingState.PAUSED_HUMAN.value
    assert record["status"] == "paused_human"
    assert record["pre_pause_state"] == OnboardingState.START_REGISTRATION.value
    assert human.requests[0][0] == "OTHER"
    assert human.requests[0][2] == "review recipe"


def test_start_registration_leaves_dedicated_states_to_the_machine(isolated):
    """A CAPTCHA/ID signal is handled by its dedicated state, not paused here."""
    from core.providers.generic_web import GenericWebAdapter

    descriptor = _descriptor("captcha.example")
    register_provider(descriptor)

    class CaptchaAdapter(GenericWebAdapter):
        def registration(self, **ctx):
            return {"status": "blocked", "requires_human": True,
                    "action_type": "CAPTCHA"}

    human = _Human()
    adapter = CaptchaAdapter("captcha.example", descriptor=descriptor)
    pipe = _Pipe(provider=_ProviderPort(adapter=adapter), human=human)
    record = _record(OnboardingState.START_REGISTRATION,
                     provider_id="captcha.example", identity_id="ident-1")

    step(record, descriptor, pipe)

    assert record["current_state"] == OnboardingState.ENTER_INFORMATION.value
    assert human.requests == []
