"""Universal website registration - offline E2E per signup shape.

Each shape (single-page, multi-step, SSO/invite-only) is driven end-to-end
through the real onboarding engine, the real mail/SMS workers over fixtures and
a deterministic offline browser. Nothing touches a network.
"""

from __future__ import annotations

import json
import socket

import pytest

from core.onboarding.manager import start_onboarding
from core.onboarding.schema import OnboardingState, OnboardingStatus
from core.providers import register_adapter, register_provider
from core.site_recipes import store as recipe_store
from tests.site_fixtures import (
    FakeSiteBrowser,
    FakeVault,
    copy_mailbox,
    descriptor_from_site,
    load_site,
    make_adapter,
    make_identity,
    make_pipeline,
    recipe_from_site,
    trust_mail_domain,
)

COMPLETED_SHAPES = ["single_page", "multi_step"]


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("AI_ORCHESTRATOR_MEMORY_DIR", str(tmp_path))
    from core.providers import manager as provider_manager

    provider_manager.reset_registry()
    from core.sms.otp import OtpHandoffStore

    monkeypatch.setattr("core.sms.otp.handoffs", OtpHandoffStore())

    # Prove offline: any attempt to reach the network / a real browser / the
    # real vault raises, and human paging never hits Telegram.
    def _boom(*_a, **_k):
        raise AssertionError("network access attempted during an offline E2E")

    monkeypatch.setattr(socket, "create_connection", _boom)
    monkeypatch.setattr("core.browser.client.BrowserOperatorClient.__init__",
                        lambda self, *a, **k: _boom())
    monkeypatch.setattr("core.ai.kai_vault_client.store_secret", lambda *a, **k: _boom())
    monkeypatch.setattr("core.notify.human_action._send_telegram",
                        lambda text: {"ok": True})
    return tmp_path


@pytest.fixture
def missions(monkeypatch):
    monkeypatch.setattr("core.kai_missions.create_mission",
                        lambda objective, tasks=None, **k: {"id": "mis-e2e"})
    monkeypatch.setattr("core.kai_missions.add_checkpoint",
                        lambda *a, **k: {"id": "mis-e2e"})


@pytest.mark.parametrize("shape", COMPLETED_SHAPES)
def test_offline_shape_reaches_completed(shape, isolated, missions, monkeypatch):
    site = load_site(shape)
    trust_mail_domain(monkeypatch, site)
    browser = FakeSiteBrowser(site)
    vault = FakeVault()
    descriptor = descriptor_from_site(site)
    register_provider(descriptor)
    register_adapter(make_adapter(site, browser=browser, vault=vault))
    recipe_store.save_recipe(recipe_from_site(site))
    recipe_store.publish_recipe(site["domain"])

    identity = make_identity()
    box = copy_mailbox(isolated, site["mail_fixture"])
    session = start_onboarding(site["domain"], identity_id=identity.identity_id,
                               mission_id=f"mis-e2e-{shape}",
                               pipeline=make_pipeline(browser, box))

    assert session.status == OnboardingStatus.COMPLETED, session.errors
    assert session.current_state == OnboardingState.COMPLETED

    from core.accounts import VerificationStatus, get_account

    account = get_account(session.account_id)
    assert account.provider == site["domain"]
    assert account.verification_status == VerificationStatus.VERIFIED

    # identity filled straight from the Digital Identity
    by_selector = {sel: val for sel, val, _ in browser.fills}
    assert by_selector.get("#email") == identity.email
    if site["requirements"].get("phone"):
        assert by_selector.get("#phone") == identity.phone
    else:
        assert by_selector.get("#name") == identity.display_name

    # email via the real mail worker; SMS via the real SMS worker over fixtures
    assert session.verification_state.get("email") is True
    if site["requirements"].get("phone"):
        assert session.verification_state.get("sms") is True

    # vault ref only: the generated password is written exactly once, by path
    assert set(vault.writes) == {account.vault_reference}
    password = vault.writes[account.vault_reference]
    assert len(password) >= 16
    persisted = (isolated / "onboarding_sessions.json").read_text()
    assert password not in persisted
    assert password not in json.dumps(session.model_dump())

    # evidence ledger has an entry for every completed transition
    states_with_evidence = {e.state for e in session.evidence}
    for state in session.completed_states:
        if state == "COMPLETED":
            continue
        assert state in states_with_evidence, f"no evidence for {state}"

    # the published recipe drove the fixture browser
    assert ("navigate", site["signup_url"]) in browser.calls


@pytest.mark.parametrize("flow_type", ["sso_only", "invite_only"])
def test_sso_invite_only_pauses_for_human(flow_type, isolated, missions):
    site = load_site("sso_invite_only")
    browser = FakeSiteBrowser(site)
    vault = FakeVault()
    descriptor = descriptor_from_site(site)
    register_provider(descriptor)
    register_adapter(make_adapter(site, browser=browser, vault=vault,
                                  flow_type=flow_type))
    # Deliberately no published recipe: the adapter must learn a draft and pause.
    box = copy_mailbox(isolated, None)
    identity = make_identity(email=f"kai-{flow_type}@example.invalid")

    session = start_onboarding(site["domain"], identity_id=identity.identity_id,
                               mission_id=f"mis-pause-{flow_type}",
                               pipeline=make_pipeline(browser, box))

    assert session.status == OnboardingStatus.PAUSED_HUMAN
    assert session.current_state == OnboardingState.PAUSED_HUMAN
    assert session.pre_pause_state == OnboardingState.START_REGISTRATION.value
    assert session.human_action_type == "OTHER"

    # no auto-registration: the flow only ever learned a draft for review
    draft = recipe_store.get_recipe(site["domain"])
    assert draft is not None and draft.status.value == "draft"
    assert draft.source.value == "learned"
    assert recipe_store.get_published(site["domain"]) is None

    from core.accounts import VerificationStatus, get_account

    account = get_account(session.account_id)
    assert account.verification_status != VerificationStatus.VERIFIED
    assert session.verification_state.get("email") is not True

    from core.notify import human_action

    assert human_action.list_pending(mission_id=session.mission_id)
