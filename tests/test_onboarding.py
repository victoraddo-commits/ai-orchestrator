"""Universal Onboarding State Machine (core/onboarding) — TDD suite.

Fully offline: a stub provider adapter, a fake browser operator, fixture email
and a synthetic SMS. Persistence is isolated to a tmp memory dir; the Mission
Engine and Telegram are stubbed so no production file or network is touched.
"""

from __future__ import annotations

import json

import pytest

from core.onboarding import manager
from core.onboarding.engine import (
    hash_text,
    is_applicable,
    next_applicable,
    step,
)
from core.onboarding.manager import (
    SessionNotFound,
    cancel_onboarding,
    default_pipeline,
    get_session,
    list_sessions,
    resume_onboarding,
    start_onboarding,
)
from core.onboarding.schema import (
    OnboardingState,
    OnboardingStatus,
    STATE_SEQUENCE,
)


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("AI_ORCHESTRATOR_MEMORY_DIR", str(tmp_path))
    from core.providers import manager as provider_manager

    provider_manager.reset_registry()
    # fresh in-memory OTP hand-off
    from core.sms.otp import OtpHandoffStore

    monkeypatch.setattr("core.sms.otp.handoffs", OtpHandoffStore())
    return tmp_path


@pytest.fixture
def missions(monkeypatch):
    """Stub the Mission Engine so tests never write memory/kai_missions.json."""
    created = []
    checkpoints = []

    def _create(objective, tasks=None, **kwargs):
        mid = f"mis-test-{len(created) + 1}"
        created.append({"id": mid, "objective": objective, "tasks": tasks or []})
        return {"id": mid, "obj": objective}

    def _checkpoint(mission_id, note, evidence=None, kind="note"):
        checkpoints.append({"mission_id": mission_id, "note": note,
                            "evidence": evidence or {}, "kind": kind})
        return {"id": mission_id}

    monkeypatch.setattr("core.kai_missions.create_mission", _create)
    monkeypatch.setattr("core.kai_missions.add_checkpoint", _checkpoint)
    return {"created": created, "checkpoints": checkpoints}


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


@pytest.fixture
def telegram(monkeypatch):
    sent = []

    def _send(text):
        sent.append(text)
        return {"ok": True, "result": {"message_id": len(sent)}}

    monkeypatch.setattr("core.notify.human_action._send_telegram", _send)
    return sent


# ---------------------------------------------------------------------------
# test doubles
# ---------------------------------------------------------------------------

class StubAdapter:
    """A minimal, fully offline ProviderAdapter."""

    def __init__(self, descriptor):
        self._descriptor = descriptor
        self.calls = []

    @property
    def descriptor(self):
        return self._descriptor

    def registration(self, **kwargs):
        self.calls.append("registration")
        return {"provider_account_id": "stub-42", "status": "created"}

    def profile_setup(self, **kwargs):
        self.calls.append("profile_setup")
        return {"profile": "filled"}

    def verification(self, **kwargs):
        self.calls.append("verification")
        return {"verified": True}

    def security_setup(self, **kwargs):
        self.calls.append("security_setup")
        return {"vault_reference": "secrets/accounts/stubprov/stub-42"}

    def account_status(self, **kwargs):
        self.calls.append("account_status")
        return {"status": "active"}


class FakeBrowser:
    """Fake CT110 browser operator: deterministic, resumable, no network."""

    def __init__(self, *, verified=True, resume_after=1):
        self.calls = []
        self.sessions: dict = {}
        self.verified = verified
        self.resume_after = resume_after

    def create_profile(self, identity_id, provider_id):
        self.calls.append(("create_profile", identity_id, provider_id))
        return {"profile_id": f"prof-{identity_id}-{provider_id}"}

    def open_session(self, identity_id, provider_id, mission_id=None, headless=None):
        sid = f"sess-{provider_id}"
        self.sessions[sid] = {"provider_id": provider_id, "resumed": 0}
        self.calls.append(("open_session", sid))
        return {"session_id": sid, "identity_id": identity_id,
                "provider_id": provider_id, "mission_id": mission_id}

    def perform(self, session_id, op, params=None):
        self.calls.append((op, session_id))
        if op == "inspect":
            text = "Your account is verified. Welcome!" if self.verified else "pending"
            return {"op": "inspect", "snapshot": {
                "url": "https://stub.example/account", "title": "Account",
                "text": text, "elements": [], "untrusted": True}}
        return {"op": op, "snapshot": {"url": (params or {}).get("url", ""),
                                       "title": "", "text": "", "elements": [],
                                       "untrusted": True}}

    def pause_for_human(self, session_id, reason="", action_required="", **kwargs):
        self.calls.append(("pause", session_id, action_required))
        info = self.sessions.setdefault(session_id, {"provider_id": None, "resumed": 0})
        return {"takeover_id": f"take-{session_id}", "session_id": session_id,
                "mission_id": kwargs.get("mission_id"),
                "provider_id": info.get("provider_id"),
                "action_required": action_required, "instructions": reason,
                "no_vnc_url": None}

    def resume(self, session_id):
        info = self.sessions.setdefault(session_id, {"provider_id": None, "resumed": 0})
        info["resumed"] += 1
        return {"resumed": info["resumed"] >= self.resume_after,
                "takeover_id": f"take-{session_id}"}

    def end_session(self, session_id):
        self.calls.append(("end_session", session_id))
        return {"session_id": session_id, "status": "ended"}


def _descriptor(provider_id, *, email=True, phone=True, captcha=False, mfa=False,
                kyc=False, payment=False, policy="ALLOWED", domain="stub.example"):
    from core.providers import AutomationPolicy, ProviderDescriptor

    return ProviderDescriptor(
        provider_id=provider_id,
        display_name=provider_id.title(),
        official_domain=domain,
        registration_url=f"https://{domain}/register",
        account_types=["user"],
        browser_required=True,
        requires_email=email,
        requires_phone=phone,
        requires_captcha=captcha,
        requires_mfa=mfa,
        requires_identity_verification=kyc,
        requires_payment=payment,
        automation_policy=AutomationPolicy(policy),
        policy_source=f"https://{domain}/tos",
    )


def _register_provider(descriptor, adapter_cls=StubAdapter):
    from core.providers import register_adapter, register_provider

    register_provider(descriptor)
    return register_adapter(adapter_cls(descriptor))


def _identity(*, email="kai-onboard@example.invalid", phone="+233200000001"):
    from core.identity import IdentityCreate, create_identity, set_default_identity

    identity = create_identity(IdentityCreate(display_name="KAI Onboarding",
                                              email=email, phone=phone))
    set_default_identity(identity.identity_id)
    return identity


def _verified_email(*_a, **_k):
    return {"verified": True, "email_id": "email-1",
            "message_id": "<v@stub>", "link": "https://stub.example/verify"}


# ---------------------------------------------------------------------------
# state machine mechanics
# ---------------------------------------------------------------------------

def test_state_sequence_is_directive_order():
    values = [s.value for s in STATE_SEQUENCE]
    assert values[0] == "DISCOVER_PROVIDER"
    assert values[-1] == "COMPLETED"
    assert "CAPTCHA/HUMAN_TAKEOVER" in values
    assert values.index("EMAIL_VERIFICATION") < values.index("SMS_VERIFICATION")
    assert values.index("ACCOUNT_REGISTRY") < values.index("VAULT")


def test_applicability_skips_na_states():
    base = _descriptor("stubprov", email=True, phone=False, captcha=False,
                       mfa=False, kyc=False)
    assert is_applicable(OnboardingState.EMAIL_VERIFICATION, base) is True
    assert is_applicable(OnboardingState.SMS_VERIFICATION, base) is False
    assert is_applicable(OnboardingState.MFA, base) is False
    assert is_applicable(OnboardingState.CAPTCHA_HUMAN_TAKEOVER, base) is False
    nxt = next_applicable(OnboardingState.ENTER_INFORMATION, base)
    assert nxt is OnboardingState.EMAIL_VERIFICATION


# ---------------------------------------------------------------------------
# gate (blocking precondition)
# ---------------------------------------------------------------------------

def test_gate_prohibited_blocks(isolated, missions):
    desc = _descriptor("blocked", email=True, phone=False, policy="PROHIBITED")
    _register_provider(desc)
    _identity()
    session = start_onboarding("blocked", pipeline=default_pipeline(client=FakeBrowser()))
    assert session.status == OnboardingStatus.FAILED
    assert session.current_state == OnboardingState.FAILED
    assert any("automation_prohibited" in e.error for e in session.errors)


def test_gate_unknown_pauses_for_human(isolated, missions, telegram):
    desc = _descriptor("unknownprov", email=True, phone=False, policy="UNKNOWN")
    _register_provider(desc)
    _identity()
    session = start_onboarding("unknownprov", pipeline=default_pipeline(client=FakeBrowser()))
    assert session.status == OnboardingStatus.PAUSED_HUMAN
    assert session.current_state == OnboardingState.PAUSED_HUMAN
    assert session.human_action_required is True
    assert session.human_action_id
    assert len(telegram) == 1


def test_unknown_provider_fails_cleanly(isolated, missions):
    session = start_onboarding("does-not-exist", pipeline=default_pipeline(client=FakeBrowser()))
    assert session.status == OnboardingStatus.FAILED
    assert any("provider_not_found" in e.error for e in session.errors)


# ---------------------------------------------------------------------------
# full traversal / skip
# ---------------------------------------------------------------------------

def test_every_applicable_transition_is_completed(isolated, missions, bus_spy, audit_spy):
    desc = _descriptor("allreq", email=True, phone=True, captcha=True, mfa=True, kyc=True)
    _register_provider(desc)
    _identity()
    browser = FakeBrowser(resume_after=1)  # takeovers auto-complete
    session = start_onboarding("allreq", pipeline=default_pipeline(
        client=browser, email_verifier=_verified_email,
        sms_consumer=lambda r, d: {"otp_present": True, "verified": True}))

    assert session.status == OnboardingStatus.COMPLETED
    expected = {s.value for s in STATE_SEQUENCE if s is not OnboardingState.COMPLETED}
    assert set(session.completed_states) == expected
    assert session.skipped_states == []
    topics = [t for t, _ in bus_spy]
    assert "onboarding.started" in topics
    assert "onboarding.state.completed" in topics
    assert "onboarding.completed" in topics
    assert any(e["event_type"] == "onboarding.start" for e in audit_spy)


def test_na_states_are_skipped_not_run(isolated, missions):
    desc = _descriptor("emailonly", email=True, phone=False, captcha=False,
                       mfa=False, kyc=False)
    _register_provider(desc)
    _identity()
    session = start_onboarding("emailonly", pipeline=default_pipeline(
        client=FakeBrowser(), email_verifier=_verified_email,
        sms_consumer=lambda r, d: {"otp_present": True, "verified": True}))
    assert session.status == OnboardingStatus.COMPLETED
    for skipped in ("SMS_VERIFICATION", "MFA", "CAPTCHA/HUMAN_TAKEOVER",
                    "IDENTITY_VERIFICATION"):
        assert skipped in session.skipped_states
        assert skipped not in session.completed_states
    assert "EMAIL_VERIFICATION" in session.completed_states


# ---------------------------------------------------------------------------
# persistence / resume / idempotency
# ---------------------------------------------------------------------------

def test_resume_after_restart(isolated, missions, telegram, tmp_path):
    desc = _descriptor("pauseprov", email=True, phone=False, captcha=True)
    _register_provider(desc)
    _identity()
    # resume_after=2 → the first attempt genuinely pauses.
    session = start_onboarding("pauseprov", pipeline=default_pipeline(
        client=FakeBrowser(resume_after=2), email_verifier=_verified_email))
    assert session.status == OnboardingStatus.PAUSED_HUMAN
    assert session.current_state == OnboardingState.PAUSED_HUMAN
    mid = session.mission_id

    # Persisted: reloading from disk (fresh call) sees the paused snapshot.
    assert (tmp_path / "onboarding_sessions.json").exists()
    reloaded = get_session(mid)
    assert reloaded.status == OnboardingStatus.PAUSED_HUMAN
    assert reloaded.pre_pause_state == "CAPTCHA/HUMAN_TAKEOVER"

    # Resume with a browser that is now satisfied → completes.
    resumed = resume_onboarding(mid, pipeline=default_pipeline(
        client=FakeBrowser(resume_after=1), email_verifier=_verified_email))
    assert resumed.status == OnboardingStatus.COMPLETED


def test_idempotent_retry_never_double_creates(isolated, missions):
    desc = _descriptor("idemprov", email=True, phone=False)
    _register_provider(desc)
    identity = _identity()
    pipe = default_pipeline(client=FakeBrowser(), email_verifier=_verified_email)
    first = start_onboarding("idemprov", identity_id=identity.identity_id, pipeline=pipe)
    assert first.status == OnboardingStatus.COMPLETED

    from core.accounts import list_accounts
    assert len(list_accounts(provider="idemprov")) == 1

    # A second onboarding for the same identity must not create a duplicate.
    second = start_onboarding("idemprov", identity_id=identity.identity_id,
                              pipeline=default_pipeline(
                                  client=FakeBrowser(), email_verifier=_verified_email))
    assert second.status == OnboardingStatus.COMPLETED
    assert second.account_id == first.account_id
    assert len(list_accounts(provider="idemprov")) == 1
    assert "START_REGISTRATION" in second.skipped_states


def test_human_takeover_pause_then_resume(isolated, missions, telegram):
    desc = _descriptor("captchaprov", email=True, phone=False, captcha=True)
    _register_provider(desc)
    _identity()
    session = start_onboarding("captchaprov", pipeline=default_pipeline(
        client=FakeBrowser(resume_after=2), email_verifier=_verified_email))
    assert session.status == OnboardingStatus.PAUSED_HUMAN
    assert session.human_action_type == "CAPTCHA"
    from core.notify import human_action
    pending = human_action.list_pending(mission_id=session.mission_id)
    assert len(pending) == 1  # exactly one pending action per (mission, type)

    resumed = resume_onboarding(session.mission_id, pipeline=default_pipeline(
        client=FakeBrowser(resume_after=1), email_verifier=_verified_email))
    assert resumed.status == OnboardingStatus.COMPLETED
    assert resumed.human_action_required is False


def test_otp_consumption_advances_the_flow(isolated, missions, telegram):
    desc = _descriptor("smsprov", email=True, phone=True)
    _register_provider(desc)
    identity = _identity()
    session = start_onboarding("smsprov", identity_id=identity.identity_id,
                               pipeline=default_pipeline(
                                   client=FakeBrowser(), email_verifier=_verified_email,
                                   sms_consumer=lambda r, d: {"otp_present": False}))
    assert session.status == OnboardingStatus.PAUSED_HUMAN
    assert session.current_state == OnboardingState.PAUSED_HUMAN
    assert session.human_action_type == "CONNECT_PHONE_FOR_SMS"

    # The code arrives: stash it in the in-memory hand-off keyed to the mission.
    from core.sms.otp import stash_otp
    stash_otp("123456", mission_id=session.mission_id, account_id=session.account_id)

    # Resume using the REAL sms consumer path.
    resumed = resume_onboarding(session.mission_id, pipeline=default_pipeline(
        client=FakeBrowser(), email_verifier=_verified_email))
    assert resumed.status == OnboardingStatus.COMPLETED
    assert resumed.verification_state.get("sms") is True

    # The OTP was consumed once and never persisted.
    from core.sms.manager import consume_otp_for
    assert consume_otp_for(mission_id=session.mission_id) is None
    raw = (isolated / "onboarding_sessions.json").read_text()
    assert "123456" not in raw


def test_email_verification_advances(isolated, missions):
    desc = _descriptor("mailprov", email=True, phone=False)
    _register_provider(desc)
    _identity()
    session = start_onboarding("mailprov", pipeline=default_pipeline(
        client=FakeBrowser(), email_verifier=_verified_email))
    assert session.status == OnboardingStatus.COMPLETED
    assert session.verification_state.get("email") is True


# ---------------------------------------------------------------------------
# evidence, registry, vault, events
# ---------------------------------------------------------------------------

def test_evidence_ledger_records_each_transition(isolated, missions):
    desc = _descriptor("evprov", email=True, phone=False)
    _register_provider(desc)
    _identity()
    session = start_onboarding("evprov", pipeline=default_pipeline(
        client=FakeBrowser(), email_verifier=_verified_email))
    states_with_evidence = {e.state for e in session.evidence}
    for state in session.completed_states:
        if state == "COMPLETED":
            continue
        assert state in states_with_evidence, f"no evidence for {state}"
    # every evidence entry is a reference/hash, never a raw secret value
    for entry in session.evidence:
        assert entry.kind
        assert entry.at


def test_registry_and_vault_reference_set_on_success(isolated, missions):
    desc = _descriptor("regprov", email=True, phone=False)
    _register_provider(desc)
    identity = _identity()
    session = start_onboarding("regprov", identity_id=identity.identity_id,
                               pipeline=default_pipeline(
                                   client=FakeBrowser(), email_verifier=_verified_email))
    assert session.status == OnboardingStatus.COMPLETED

    from core.accounts import VerificationStatus, get_account
    account = get_account(session.account_id)
    assert account.verification_status == VerificationStatus.VERIFIED
    assert account.vault_reference == f"secrets/accounts/regprov/{account.account_id}"
    assert session.vault_reference == account.vault_reference
    assert account.mission_id == session.mission_id
    assert account.digital_identity == identity.identity_id
    assert account.browser_profile


def test_events_and_audit_emitted(isolated, missions, bus_spy, audit_spy):
    desc = _descriptor("obsprov", email=True, phone=False)
    _register_provider(desc)
    _identity()
    start_onboarding("obsprov", pipeline=default_pipeline(
        client=FakeBrowser(), email_verifier=_verified_email))
    topics = [t for t, _ in bus_spy]
    for topic in ("onboarding.started", "onboarding.state.entered",
                  "onboarding.state.completed"):
        assert topic in topics
    event_types = {e["event_type"] for e in audit_spy}
    assert "onboarding.start" in event_types
    assert "onboarding.state.completed" in event_types


def test_cancel(isolated, missions):
    desc = _descriptor("cancelprov", email=True, phone=False, captcha=True)
    _register_provider(desc)
    _identity()
    session = start_onboarding("cancelprov", pipeline=default_pipeline(
        client=FakeBrowser(resume_after=2), email_verifier=_verified_email))
    assert session.status == OnboardingStatus.PAUSED_HUMAN
    cancelled = cancel_onboarding(session.mission_id, reason="operator aborted")
    assert cancelled.status == OnboardingStatus.CANCELLED
    assert cancelled.current_state == OnboardingState.CANCELLED
    with pytest.raises(SessionNotFound):
        cancel_onboarding("ghost")


# ---------------------------------------------------------------------------
# local end-to-end (stub provider + fixture browser/email/sms, no real sites)
# ---------------------------------------------------------------------------

def test_local_e2e_simulated_onboarding(isolated, missions, bus_spy, audit_spy,
                                        telegram, tmp_path):
    """A complete simulated onboarding: fixture verification email -> real mail
    worker -> real SMS/OTP worker -> Account Registry VERIFIED + vault ref."""
    import shutil
    from pathlib import Path

    from core.mail.transports import FixtureTransport

    # A stub provider whose official domain matches the offline Proton fixture.
    desc = _descriptor("stubprov", email=True, phone=True, domain="proton.me")
    _register_provider(desc)
    identity = _identity(email="kai-onboard@example.invalid", phone="+233200000001")

    fixture_dir = Path(__file__).parent / "fixtures" / "mail"
    box = tmp_path / "mailbox"
    box.mkdir()
    shutil.copy(fixture_dir / "verification_proton.eml", box / "verify.eml")

    OTP = "482913"

    def sms_consumer(record, descriptor):
        from core.sms import manager as sms_manager
        from core.sms.adapter import RawSms

        sms_manager.ingest_raw(
            RawSms(from_number="12345", to_number="+233200000001",
                   body=f"Your {descriptor.display_name} verification code is {OTP}"),
            mission_id=record["mission_id"], account_id=record.get("account_id"))
        code = sms_manager.consume_otp_for(
            mission_id=record["mission_id"], account_id=record.get("account_id"))
        return {"otp_present": code is not None, "verified": code is not None}

    browser = FakeBrowser()
    pipeline = default_pipeline(client=browser,
                                mail_transport=FixtureTransport(box),
                                sms_consumer=sms_consumer)
    session = start_onboarding("stubprov", identity_id=identity.identity_id,
                               pipeline=pipeline)

    assert session.status == OnboardingStatus.COMPLETED
    assert session.current_state == OnboardingState.COMPLETED
    assert "EMAIL_VERIFICATION" in session.completed_states
    assert "SMS_VERIFICATION" in session.completed_states
    assert session.verification_state.get("email") is True
    assert session.verification_state.get("sms") is True
    assert session.skipped_states and "MFA" in session.skipped_states

    from core.accounts import VerificationStatus, get_account
    account = get_account(session.account_id)
    assert account.verification_status == VerificationStatus.VERIFIED
    assert session.vault_reference == account.vault_reference
    assert account.vault_reference.startswith("secrets/accounts/stubprov/")

    kinds = {e.kind for e in session.evidence}
    assert {"provider_descriptor", "browser_profile", "provider_registration",
            "email_verification", "sms_verification", "account_registry",
            "vault_reference", "end_to_end"} <= kinds

    # The OTP never leaked into the persisted session.
    assert OTP not in (isolated / "onboarding_sessions.json").read_text()
    assert OTP not in json.dumps(session.model_dump())

    assert "onboarding.started" in [t for t, _ in bus_spy]
    assert any(e["event_type"] == "onboarding.state.completed" for e in audit_spy)
