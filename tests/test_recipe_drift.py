"""Recipe drift: a published selector stops resolving -> stale + relearn + pause.

No new production code: this exercises ``GenericWebAdapter`` drift handling and
the engine's ``requires_human`` pause from Task 7 through the real pipeline.
"""

from __future__ import annotations

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
)


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("AI_ORCHESTRATOR_MEMORY_DIR", str(tmp_path))
    from core.providers import manager as provider_manager

    provider_manager.reset_registry()
    from core.sms.otp import OtpHandoffStore

    monkeypatch.setattr("core.sms.otp.handoffs", OtpHandoffStore())

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
                        lambda objective, tasks=None, **k: {"id": "mis-drift"})
    monkeypatch.setattr("core.kai_missions.add_checkpoint",
                        lambda *a, **k: {"id": "mis-drift"})


def test_published_recipe_drift_marks_stale_relearns_and_pauses(isolated, missions):
    site = load_site("single_page")
    browser = FakeSiteBrowser(site)
    # Break a published selector so the recipe no longer resolves.
    browser.failing_selectors = {"#password"}
    vault = FakeVault()
    descriptor = descriptor_from_site(site)
    register_provider(descriptor)
    register_adapter(make_adapter(site, browser=browser, vault=vault))
    recipe_store.save_recipe(recipe_from_site(site))
    recipe_store.publish_recipe(site["domain"])

    identity = make_identity(email="kai-drift@example.invalid")
    box = copy_mailbox(isolated, site["mail_fixture"])
    session = start_onboarding(site["domain"], identity_id=identity.identity_id,
                               mission_id="mis-drift",
                               pipeline=make_pipeline(browser, box))

    # the adapter marked the recipe stale, re-learned a draft, and the engine
    # paused for operator review (learn-then-pause) instead of registering.
    assert session.status == OnboardingStatus.PAUSED_HUMAN
    assert session.current_state == OnboardingState.PAUSED_HUMAN
    assert session.pre_pause_state == OnboardingState.START_REGISTRATION.value
    assert session.human_action_type == "OTHER"

    stale = recipe_store.get_recipe(site["domain"], status="stale")
    assert stale is not None
    assert recipe_store.get_published(site["domain"]) is None

    versions = recipe_store.read_versions(site["domain"])
    drafts = [v for v in versions if v["status"] == "draft" and v["source"] == "learned"]
    assert drafts, "a re-learned draft must be stored"
    # re-learning inspected the live page
    assert any(c and c[0] == "inspect" for c in browser.calls)

    from core.notify import human_action

    assert human_action.list_pending(mission_id="mis-drift")
