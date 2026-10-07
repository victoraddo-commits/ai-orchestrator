"""Amazon provider adapter (core/providers/amazon) — TDD suite.

STEP 7 of Universal Account Registration: the first *real* provider adapter.
Fully offline — a fixture Amazon signup page drives the browser operator, the
real mail worker follows a fixture verification email, and the real SMS/OTP
worker relays a fixture code. No live Amazon, no network.

The suite proves the safety posture:
* the descriptor records Amazon's automation policy as UNKNOWN with a cited
  ``policy_source`` (Conditions of Use);
* the ToS gate forces human confirmation before any automated step;
* no AccountRecord is created before the human confirms;
* the generated password is stored in the Vault as a *reference* and never
  appears in the session, the registry, evidence, or any return value.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from core.mail.transports import FixtureTransport
from core.onboarding.manager import default_pipeline, resume_onboarding, start_onboarding
from core.onboarding.schema import OnboardingState, OnboardingStatus
from core.providers import (
    AutomationPolicy,
    GateOutcome,
    HumanConfirmationRequired,
    OPERATIONS,
    ProviderAdapter,
    discover,
    ensure_automation_allowed,
    get_ready_adapter,
    policy_gate,
    register_adapter,
    set_policy_override,
)
from core.providers import store as provider_store
from core.providers.amazon import AmazonAdapter, resolve_account_type

FIXTURES = Path(__file__).parent / "fixtures" / "mail"
OTP = "482913"


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("AI_ORCHESTRATOR_MEMORY_DIR", str(tmp_path))
    # Clears registered adapters and re-seeds the in-code catalog (amazon).
    provider_store.reset()
    from core.sms.otp import OtpHandoffStore

    monkeypatch.setattr("core.sms.otp.handoffs", OtpHandoffStore())
    return tmp_path


@pytest.fixture
def missions(monkeypatch):
    created, checkpoints = [], []

    def _create(objective, tasks=None, **kwargs):
        mid = f"mis-test-{len(created) + 1}"
        created.append({"id": mid, "objective": objective})
        return {"id": mid, "objective": objective}

    def _checkpoint(mission_id, note, evidence=None, kind="note"):
        checkpoints.append({"mission_id": mission_id, "note": note})
        return {"id": mission_id}

    monkeypatch.setattr("core.kai_missions.create_mission", _create)
    monkeypatch.setattr("core.kai_missions.add_checkpoint", _checkpoint)
    return {"created": created, "checkpoints": checkpoints}


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


class FakeVault:
    """In-memory stand-in for the Vault machine plane (write -> reference)."""

    def __init__(self):
        self.writes: dict[str, str] = {}

    def __call__(self, path: str, value: str) -> str:
        self.writes[path] = value
        return path


class FixtureAmazonBrowser:
    """Offline 'Amazon signup page' + operator, driven by engine + adapter."""

    def __init__(self, *, resume_after: int = 1):
        self.calls: list = []
        self.sessions: dict = {}
        self._seq = 0
        self.resume_after = resume_after

    # -- profiles / sessions -------------------------------------------------
    def create_profile(self, identity_id, provider_id):
        self.calls.append(("create_profile", identity_id, provider_id))
        return {"profile_id": f"prof-{identity_id}-{provider_id}"}

    def open_session(self, identity_id, provider_id, mission_id=None, headless=None):
        self._seq += 1
        sid = f"sess-amazon-{self._seq}"
        self.sessions[sid] = {
            "identity_id": identity_id, "provider_id": provider_id,
            "mission_id": mission_id, "url": "", "stage": "signup",
            "resumed": 0, "fills": [], "clicks": [],
        }
        self.calls.append(("open_session", sid))
        return {"session_id": sid, "identity_id": identity_id,
                "provider_id": provider_id, "mission_id": mission_id}

    def _url(self, st):
        return st.get("url") or {
            "signup": "https://www.amazon.com/ap/register",
            "captcha": "https://www.amazon.com/ap/cvf",
            "created": "https://www.amazon.com/gp/css/homepage.html",
            "verified": "https://www.amazon.com/ap/verify",
        }.get(st["stage"], "https://www.amazon.com/")

    def _snapshot(self, st):
        stage = st["stage"]
        if stage == "signup":
            return {"url": self._url(st), "title": "Amazon Sign-In",
                    "text": "Create account. Your name Email or mobile phone number "
                            "Password Passwords must be at least 6 characters.",
                    "elements": [
                        {"role": "textbox", "name": "Your name", "selector": "#ap_customer_name"},
                        {"role": "textbox", "name": "Email", "selector": "#ap_email"},
                        {"role": "textbox", "name": "Password", "type": "password",
                         "selector": "#ap_password"},
                    ],
                    "has_password_field": True, "untrusted": True}
        if stage == "captcha":
            return {"url": self._url(st), "title": "Amazon",
                    "text": "Please confirm you are not a robot. Type the characters "
                            "you see in this image. CAPTCHA",
                    "elements": [{"role": "img", "name": "captcha", "selector": "#cvf-captcha-image"}],
                    "has_password_field": False, "untrusted": True}
        if stage == "verified":
            return {"url": self._url(st), "title": "Email verified",
                    "text": "Your email has been verified. Welcome!", "elements": [],
                    "has_password_field": False, "untrusted": True}
        if stage == "created":
            return {"url": self._url(st), "title": "Amazon",
                    "text": "Welcome to Amazon. Your account was created successfully. "
                            "You are signed in. Hello, KAI. Sign out",
                    "elements": [{"role": "link", "name": "Sign out",
                                  "selector": "#nav-link-accountList"}],
                    "has_password_field": False, "untrusted": True}
        return {"url": self._url(st), "title": "Amazon", "text": "", "elements": [],
                "has_password_field": False, "untrusted": True}

    def _state(self, session_id):
        return self.sessions.setdefault(session_id, {
            "identity_id": None, "provider_id": "amazon", "mission_id": None,
            "url": "", "stage": "signup", "resumed": 0, "fills": [], "clicks": [],
        })

    # -- operations ----------------------------------------------------------
    def perform(self, session_id, op, params=None):
        params = dict(params or {})
        st = self._state(session_id)
        self.calls.append((op, session_id,
                           params.get("url") or params.get("selector")))
        if op == "navigate":
            url = str(params.get("url") or "")
            st["url"] = url
            if "verify" in url.lower():
                st["stage"] = "verified"
            elif st["stage"] not in ("created", "captcha"):
                st["stage"] = "signup"
            return {"op": "navigate", "snapshot": self._snapshot(st)}
        if op == "inspect":
            return {"op": "inspect", "snapshot": self._snapshot(st)}
        if op == "fill":
            st["fills"].append(params.get("selector"))
            return {"op": "fill", "selector": params.get("selector"), "ok": True,
                    "credential": bool(params.get("credential"))}
        if op == "click":
            sel = str(params.get("selector") or "")
            st["clicks"].append(sel)
            if sel == "#continue" and st["stage"] == "signup":
                st["stage"] = "captcha"
            elif "otp" in sel or "cvf" in sel:
                st["stage"] = "created"
            return {"op": "click", "ok": True}
        return {"op": op, "ok": True}

    # -- human takeover ------------------------------------------------------
    def pause_for_human(self, session_id, reason="", action_required="", **kwargs):
        st = self._state(session_id)
        self.calls.append(("pause", session_id, action_required))
        return {"takeover_id": f"take-{session_id}", "session_id": session_id,
                "mission_id": st.get("mission_id"), "provider_id": st.get("provider_id"),
                "action_required": action_required, "instructions": reason,
                "no_vnc_url": None}

    def resume(self, session_id):
        st = self._state(session_id)
        st["resumed"] += 1
        resumed = st["resumed"] >= self.resume_after
        if resumed:
            st["stage"] = "created"
        return {"resumed": resumed, "takeover_id": f"take-{session_id}"}

    def end_session(self, session_id):
        self.calls.append(("end_session", session_id))
        return {"session_id": session_id, "status": "ended"}


def _identity(*, email="kai-onboard@example.invalid", phone="+233200000001"):
    from core.identity import IdentityCreate, create_identity, set_default_identity

    identity = create_identity(IdentityCreate(display_name="KAI Onboarding",
                                              email=email, phone=phone))
    set_default_identity(identity.identity_id)
    return identity


def _confirm_automation(monkeypatch, provider_id="amazon"):
    """Simulate the operator confirming automation for an UNKNOWN provider."""
    from core.providers import manager as pm

    monkeypatch.setattr(pm, "_approval_is_approved", lambda approval_id: True)
    return set_policy_override(
        provider_id,
        AutomationPolicy.ALLOWED,
        policy_source="operator-confirmed (fixture run): human authorized offline simulation",
        reason="operator confirmed for offline fixture simulation",
        approval_id="appr-step7-test",
    )


def _mailbox(tmp_path, names=("verification_amazon.eml",)):
    box = tmp_path / "mailbox"
    box.mkdir(exist_ok=True)
    for name in names:
        shutil.copy(FIXTURES / name, box / name)
    return box


def _sms_consumer(record, descriptor):
    from core.providers.amazon import AmazonAdapter as _A  # noqa: F401
    from core.sms import manager as sms_manager
    from core.sms.adapter import RawSms

    sms_manager.ingest_raw(
        RawSms(from_number="12345", to_number="+233200000001",
               body=f"Your {descriptor.display_name} verification code is {OTP}"),
        mission_id=record["mission_id"], account_id=record.get("account_id"))
    code = sms_manager.consume_otp_for(
        mission_id=record["mission_id"], account_id=record.get("account_id"))
    if code:
        provider_store.get_adapter("amazon").verification(
            account_id=record.get("account_id"), code=code,
            browser_session_id=record.get("browser_session_id"))
    return {"otp_present": code is not None, "verified": code is not None}


# ---------------------------------------------------------------------------
# interface conformance
# ---------------------------------------------------------------------------


def test_adapter_conforms_to_provider_adapter_interface(isolated):
    adapter = AmazonAdapter(browser=FixtureAmazonBrowser(), vault=FakeVault())
    assert isinstance(adapter, ProviderAdapter)
    assert adapter.descriptor.provider_id == "amazon"
    for operation in OPERATIONS:
        assert callable(getattr(adapter, operation)), operation
    # publishing is explicitly NOT_APPLICABLE for a customer account
    assert adapter.publishing() == ProviderAdapter.NOT_APPLICABLE


# ---------------------------------------------------------------------------
# descriptor honesty
# ---------------------------------------------------------------------------


def test_descriptor_records_unknown_automation_policy_with_source(isolated):
    descriptor = provider_store.get_base("amazon")
    assert descriptor is not None
    assert descriptor.automation_policy is AutomationPolicy.UNKNOWN
    assert descriptor.policy_source and "http" in descriptor.policy_source
    assert "amazon.com" in descriptor.policy_source


def test_descriptor_requirements_are_honest(isolated):
    descriptor = provider_store.get_base("amazon")
    assert descriptor.official_domain == "amazon.com"
    assert descriptor.account_types[0] == "customer"
    assert {"customer", "business", "seller", "associates"} <= set(descriptor.account_types)
    assert descriptor.has_api is False
    assert descriptor.has_oauth is False
    assert descriptor.browser_required is True
    assert descriptor.requires_email is True
    assert descriptor.requires_phone is True
    assert descriptor.requires_captcha is True
    assert descriptor.requires_mfa is False
    assert descriptor.requires_identity_verification is False  # customer default
    assert descriptor.requires_payment is True  # card on file
    # region domains are listed
    assert any("co.uk" in region for region in descriptor.regions)
    assert any("amazon.de" in region for region in descriptor.regions)


# ---------------------------------------------------------------------------
# discovery
# ---------------------------------------------------------------------------


def test_discovery_resolves_amazon_and_account_types(isolated):
    for query in ("amazon", "Amazon", "amazon.com",
                  "https://www.amazon.com/ap/register"):
        assert discover(query).provider_id == "amazon", query

    adapter = AmazonAdapter(browser=FixtureAmazonBrowser(), vault=FakeVault())
    info = adapter.discovery()
    assert info["provider_id"] == "amazon"
    assert info["account_type"] == "customer"
    assert list(info["account_types"])[0] == "customer"
    assert "amazon.co.uk" in info["official_domains"]
    assert info["automation_policy"] == "UNKNOWN"


# ---------------------------------------------------------------------------
# the ToS gate (UNKNOWN -> human confirmation)
# ---------------------------------------------------------------------------


def test_gate_forces_human_confirmation_for_amazon(isolated):
    decision = policy_gate("amazon")
    assert decision.decision is GateOutcome.REQUIRE_HUMAN_CONFIRMATION
    assert decision.requires_human is True
    assert decision.automation_policy is AutomationPolicy.UNKNOWN
    assert decision.policy_source

    with pytest.raises(HumanConfirmationRequired):
        ensure_automation_allowed("amazon")


def test_gate_blocks_adapter_handoff_for_amazon(isolated):
    register_adapter(AmazonAdapter(browser=FixtureAmazonBrowser(), vault=FakeVault()))
    with pytest.raises(HumanConfirmationRequired):
        get_ready_adapter("amazon")


def test_no_account_record_until_human_confirms(isolated, missions, telegram):
    register_adapter(AmazonAdapter(browser=FixtureAmazonBrowser(), vault=FakeVault()))
    identity = _identity()
    session = start_onboarding(
        "amazon", identity_id=identity.identity_id, mission_id="mis-gate-amazon",
        pipeline=default_pipeline(client=FixtureAmazonBrowser(),
                                  mail_transport=None, sms_consumer=_sms_consumer))
    assert session.status == OnboardingStatus.PAUSED_HUMAN
    assert session.current_state == OnboardingState.PAUSED_HUMAN
    assert session.human_action_required is True

    from core.accounts import list_accounts

    assert list_accounts(provider="amazon") == []  # nothing created pre-confirmation


# ---------------------------------------------------------------------------
# account-type resolution
# ---------------------------------------------------------------------------


def test_account_type_defaults_to_customer():
    assert resolve_account_type("") == "customer"
    assert resolve_account_type("Buy a Kindle on Amazon") == "customer"
    assert resolve_account_type(None) == "customer"


@pytest.mark.parametrize("objective,expected", [
    ("Open an Amazon seller account", "seller"),
    ("register as a seller on Amazon and list products with FBA", "seller"),
    ("Set up an Amazon Business account for procurement", "business"),
    ("Join the Amazon Associates affiliate program", "associates"),
])
def test_account_type_resolution(objective, expected):
    assert resolve_account_type(objective) == expected


def test_seller_descriptor_requires_identity_verification():
    customer = AmazonAdapter.descriptor_for("customer")
    seller = AmazonAdapter.descriptor_for("seller")
    assert customer.requires_identity_verification is False
    assert seller.requires_identity_verification is True
    assert seller.provider_id == "amazon"


def test_seller_discovery_flags_human_identity_gate(isolated):
    adapter = AmazonAdapter(account_type="seller", browser=FixtureAmazonBrowser(),
                            vault=FakeVault())
    info = adapter.discovery(objective="register an Amazon seller account")
    assert info["account_type"] == "seller"
    assert info["requires_identity_verification"] is True
    assert info["requires_human_identity_verification"] is True


def test_seller_onboarding_pauses_for_human_identity(isolated, missions, telegram,
                                                     monkeypatch, tmp_path):
    """A seller run must stop at the human identity-verification step."""
    # isolate the identity-verification gate: the engine's generic human-takeover
    # state also fires for payment, so switch both off for this focused test.
    seller = AmazonAdapter.descriptor_for("seller").model_copy(
        update={"requires_captcha": False, "requires_payment": False})
    monkeypatch.setitem(provider_store._DESCRIPTORS, "amazon", seller)

    browser = FixtureAmazonBrowser(resume_after=2)
    register_adapter(AmazonAdapter(descriptor=seller, account_type="seller",
                                   browser=browser, vault=FakeVault()))
    _confirm_automation(monkeypatch)
    identity = _identity()
    box = _mailbox(tmp_path)

    session = start_onboarding(
        "amazon", identity_id=identity.identity_id, mission_id="mis-seller-amazon",
        objective="Open an Amazon seller account",
        pipeline=default_pipeline(client=browser,
                                  mail_transport=FixtureTransport(box),
                                  sms_consumer=_sms_consumer))
    assert session.status == OnboardingStatus.PAUSED_HUMAN
    assert session.current_state == OnboardingState.PAUSED_HUMAN
    assert session.pre_pause_state == OnboardingState.IDENTITY_VERIFICATION.value
    assert session.human_action_type == "ID_VERIFICATION"


# ---------------------------------------------------------------------------
# adapter registration mechanics (direct, offline)
# ---------------------------------------------------------------------------


def test_registration_stores_password_as_vault_ref_and_detects_captcha(isolated):
    browser = FixtureAmazonBrowser()
    vault = FakeVault()
    adapter = AmazonAdapter(browser=browser, vault=vault)
    identity = _identity()
    opened = browser.open_session(identity.identity_id, "amazon", mission_id="mis-direct")

    outcome = adapter.registration(
        identity_id=identity.identity_id,
        browser_session_id=opened["session_id"],
        account_id="acct-amazon-1", mission_id="mis-direct")

    assert outcome["account_type"] == "customer"
    assert outcome["captcha_detected"] is True
    assert outcome["requires_human"] is True
    assert outcome["action_type"] == "CAPTCHA"
    assert outcome["password_ref"] == "secrets/accounts/amazon/acct-amazon-1"

    # the plaintext was written to the vault exactly once and never returned
    assert set(vault.writes) == {"secrets/accounts/amazon/acct-amazon-1"}
    password = vault.writes["secrets/accounts/amazon/acct-amazon-1"]
    assert len(password) >= 16
    assert any(c.isupper() for c in password)
    assert any(c.islower() for c in password)
    assert any(c.isdigit() for c in password)
    assert password not in json.dumps(outcome)
    assert "password" not in outcome  # only password_ref, never the value

    # the navigation landed on the official host and the credential fill flagged
    assert any(c[0] == "navigate" for c in browser.calls)
    assert any(c[0] == "fill" and c[2] in ("#ap_password", "#ap_password_check")
               for c in browser.calls)


def test_verification_enters_otp_without_leaking(isolated):
    browser = FixtureAmazonBrowser()
    adapter = AmazonAdapter(browser=browser, vault=FakeVault())
    opened = browser.open_session("ident-1", "amazon", mission_id="mis-otp")

    result = adapter.verification(account_id="acct-x", code="654321",
                                  browser_session_id=opened["session_id"])
    assert result["status"] == "otp_submitted"
    assert "654321" not in json.dumps(result)
    st = browser.sessions[opened["session_id"]]
    assert "#cvf-input-code" in st["fills"]


def test_publishing_not_applicable_for_customer_but_gated_for_seller(isolated):
    customer = AmazonAdapter(account_type="customer", browser=FixtureAmazonBrowser(),
                             vault=FakeVault())
    seller = AmazonAdapter(account_type="seller", browser=FixtureAmazonBrowser(),
                           vault=FakeVault())
    assert customer.publishing() == ProviderAdapter.NOT_APPLICABLE
    seller_out = seller.publishing()
    assert isinstance(seller_out, dict) and seller_out.get("requires_human") is True


# ---------------------------------------------------------------------------
# fixture-driven end-to-end registration simulation
# ---------------------------------------------------------------------------


def test_fixture_registration_simulation_end_to_end(isolated, missions, telegram,
                                                    monkeypatch, tmp_path):
    """Gate -> human confirmation -> fixture signup -> email OTP -> SMS OTP ->
    CAPTCHA human takeover -> account created -> registry VERIFIED + vault ref."""
    from core.mail.transports import FixtureTransport

    browser = FixtureAmazonBrowser(resume_after=2)  # force a genuine takeover
    vault = FakeVault()
    register_adapter(AmazonAdapter(browser=browser, vault=vault))
    identity = _identity()
    box = _mailbox(tmp_path)

    pipeline = default_pipeline(client=browser, mail_transport=FixtureTransport(box),
                                sms_consumer=_sms_consumer)
    session = start_onboarding("amazon", identity_id=identity.identity_id,
                               mission_id="mis-amazon-e2e", pipeline=pipeline)

    # 1) The UNKNOWN gate pauses for a human BEFORE anything is registered.
    assert session.status == OnboardingStatus.PAUSED_HUMAN
    assert session.current_state == OnboardingState.PAUSED_HUMAN
    assert session.human_action_type == "OTHER"
    from core.accounts import list_accounts

    assert list_accounts(provider="amazon") == []

    # 2) The operator confirms; the flow proceeds and pauses at the CAPTCHA.
    _confirm_automation(monkeypatch)
    session = resume_onboarding("mis-amazon-e2e", pipeline=default_pipeline(
        client=browser, mail_transport=FixtureTransport(box), sms_consumer=_sms_consumer))
    assert session.status == OnboardingStatus.PAUSED_HUMAN
    assert session.pre_pause_state == OnboardingState.CAPTCHA_HUMAN_TAKEOVER.value
    assert session.human_action_type == "CAPTCHA"

    from core.notify import human_action

    pending = human_action.list_pending(mission_id="mis-amazon-e2e")
    assert any(p["action_type"] == "CAPTCHA" for p in pending)

    # email + SMS worker paths already ran before the captcha state
    assert session.verification_state.get("email") is True
    assert session.verification_state.get("sms") is True

    # 3) Human solves the CAPTCHA; the flow completes.
    session = resume_onboarding("mis-amazon-e2e", pipeline=default_pipeline(
        client=browser, mail_transport=FixtureTransport(box), sms_consumer=_sms_consumer))
    assert session.status == OnboardingStatus.COMPLETED
    assert session.current_state == OnboardingState.COMPLETED

    # account + vault outcome
    from core.accounts import VerificationStatus, get_account

    account = get_account(session.account_id)
    assert account.provider == "amazon"
    assert account.verification_status == VerificationStatus.VERIFIED
    assert session.vault_reference == account.vault_reference
    assert account.vault_reference.startswith("secrets/accounts/amazon/")

    # exactly one password written, at the account's vault path, as a REF
    assert set(vault.writes) == {account.vault_reference}
    password = vault.writes[account.vault_reference]
    assert len(password) >= 16

    # the plaintext never touched the persisted session or the registry
    persisted = (tmp_path / "onboarding_sessions.json").read_text()
    assert password not in persisted
    assert password not in json.dumps(session.model_dump())
    assert password not in (tmp_path / "account_registry.json").read_text()

    # evidence ledger covers the real transitions
    kinds = {e.kind for e in session.evidence}
    assert {"provider_descriptor", "provider_registration", "email_verification",
            "sms_verification", "human_takeover", "account_registry",
            "vault_reference", "end_to_end"} <= kinds

    # customer onboarding skips the identity-verification state
    assert OnboardingState.IDENTITY_VERIFICATION.value in session.skipped_states


def test_fixture_simulation_never_touches_real_network(isolated, missions, telegram,
                                                       monkeypatch, tmp_path):
    """With fakes injected, the real browser client / vault writer are never used."""
    from core.mail.transports import FixtureTransport

    def _boom(*a, **k):
        raise AssertionError("real network path used")

    monkeypatch.setattr("core.browser.client.BrowserOperatorClient.__init__",
                        lambda self, *a, **k: _boom())
    monkeypatch.setattr("core.ai.kai_vault_client.store_secret",
                        lambda *a, **k: _boom())

    browser = FixtureAmazonBrowser(resume_after=1)
    register_adapter(AmazonAdapter(browser=browser, vault=FakeVault()))
    identity = _identity()
    box = _mailbox(tmp_path)
    _confirm_automation(monkeypatch)

    session = start_onboarding("amazon", identity_id=identity.identity_id,
                               mission_id="mis-amazon-nonet",
                               pipeline=default_pipeline(
                                   client=browser, mail_transport=FixtureTransport(box),
                                   sms_consumer=_sms_consumer))
    # gate pause then override already applied -> completes with no real network
    if session.status == OnboardingStatus.PAUSED_HUMAN:
        session = resume_onboarding("mis-amazon-nonet", pipeline=default_pipeline(
            client=browser, mail_transport=FixtureTransport(box),
            sms_consumer=_sms_consumer))
    assert session.status == OnboardingStatus.COMPLETED
