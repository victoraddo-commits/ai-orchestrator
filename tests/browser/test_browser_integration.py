"""CT111 orchestration integration — mission-backed, gate-first browser sessions.

Uses a fake browser-service client (no network) and spies on the real event bus
and audit logger. Verifies the STEP 2 gate blocks automation up front, missions
are created/started, tools register, and canonical events/audit are emitted.
"""

from __future__ import annotations

import pytest


class FakeClient:
    def __init__(self):
        self.calls = []

    def open_session(self, identity_id, provider_id, *, mission_id=None, headless=None):
        self.calls.append(("open_session", identity_id, provider_id, mission_id))
        return {"session_id": "bsess-fake", "mission_id": mission_id,
                "identity_id": identity_id, "provider_id": provider_id}

    def pause_for_human(self, session_id, **kwargs):
        self.calls.append(("pause", session_id))
        return {"takeover_id": "tover-fake", "session_id": session_id, "mission_id": "mis-fake",
                "no_vnc_url": "http://127.0.0.1:6080/vnc.html", "instructions": kwargs.get("instructions", "")}

    def resume(self, session_id):
        self.calls.append(("resume", session_id))
        return {"resumed": True, "takeover_id": "tover-fake", "session_id": session_id}

    def end_session(self, session_id):
        self.calls.append(("end", session_id))
        return {"session_id": session_id, "status": "ended"}


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("AI_ORCHESTRATOR_MEMORY_DIR", str(tmp_path))
    import core.kai_missions as missions

    monkeypatch.setattr(missions, "MISSIONS_PATH", tmp_path / "kai_missions.json")
    from core.providers import (
        AutomationPolicy, ProviderDescriptor, manager, register_provider,
    )

    manager.reset_registry()
    register_provider(ProviderDescriptor(
        provider_id="prov-allowed", display_name="Allowed Provider",
        official_domain="example.test", account_types=["user"],
        automation_policy=AutomationPolicy.ALLOWED, policy_source="test"))
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


def test_start_onboarding_session_creates_and_starts_mission(bus_spy, audit_spy):
    from core.browser.integration import start_onboarding_session
    from core.kai_missions import get_mission

    client = FakeClient()
    payload = start_onboarding_session("ident-1", "prov-allowed", client=client,
                                       objective="Onboard ident-1")
    assert payload["provider_id"] == "prov-allowed"
    assert payload["official_domain"] == "example.test"
    assert payload["session"]["session_id"] == "bsess-fake"
    mission_id = payload["mission_id"]
    assert mission_id.startswith("mis-")
    assert get_mission(mission_id)["status"] == "running"
    assert client.calls and client.calls[0][0] == "open_session"
    assert client.calls[0][3] == mission_id
    assert "browser.session.started" in [t for t, _ in bus_spy]
    assert any(e["event_type"] == "browser.onboarding.start" for e in audit_spy)


def test_gate_blocks_unknown_provider_before_any_session():
    from core.browser.integration import start_onboarding_session
    from core.providers import HumanConfirmationRequired

    client = FakeClient()
    with pytest.raises(HumanConfirmationRequired):
        start_onboarding_session("ident-1", "generic", client=client)
    assert client.calls == []  # no browser session was ever opened


def test_browser_tools_registered():
    from core.browser.tools import register_browser_tools
    from core.kai_tools.registry import REGISTRY

    register_browser_tools(FakeClient())
    assert REGISTRY.get("kai.browser.open_session") is not None
    assert REGISTRY.get("kai.browser.inspect") is not None
    assert REGISTRY.get("kai.browser.resume") is not None


def test_pause_and_resume_helpers_emit_events(bus_spy, audit_spy):
    from core.browser.integration import pause_for_human, resume_if_completed

    client = FakeClient()
    record = pause_for_human("bsess-fake", reason="mfa", action_required="MFA",
                             client=client, instructions="Enter the code")
    assert record["takeover_id"] == "tover-fake"
    result = resume_if_completed("bsess-fake", client=client)
    assert result["resumed"] is True
    topics = [t for t, _ in bus_spy]
    assert "browser.session.paused" in topics
    assert "human.action.required" in topics
    assert "browser.session.resumed" in topics
    assert "human.action.completed" in topics
    types = [e["event_type"] for e in audit_spy]
    assert "browser.onboarding.pause" in types
    assert "browser.onboarding.resume" in types
