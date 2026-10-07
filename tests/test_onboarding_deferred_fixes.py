"""STEP 6 deferred fixes (a) tool-surface registration, (b) policy-override
approval gate. Fully offline.
"""

from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# (a) browser/mail/sms tool surfaces registered at boot
# ---------------------------------------------------------------------------

def test_scheduler_registers_onboarding_tool_surfaces(isolated_registry):
    from core.kai_tools.registry import REGISTRY
    from core.scheduler import _register_onboarding_tool_surfaces

    registered = _register_onboarding_tool_surfaces()
    for tool_id in ("kai.browser.open_session", "kai.mail.ingest",
                    "kai.sms.ingest"):
        assert REGISTRY.get(tool_id) is not None, f"{tool_id} not registered"
    # idempotent: a second call must not raise or duplicate
    again = _register_onboarding_tool_surfaces()
    assert isinstance(again, list)
    assert registered  # first call registered something


# ---------------------------------------------------------------------------
# (b) policy-override approval gate
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def isolated_registry(monkeypatch, tmp_path):
    monkeypatch.setenv("AI_ORCHESTRATOR_MEMORY_DIR", str(tmp_path))
    # The approval queue (core.approval) uses core.memory's default directory,
    # which is resolved at import; point it at the isolated dir for determinism.
    import core.memory as _memory

    monkeypatch.setattr(_memory, "MEMORY_DIR", tmp_path, raising=False)
    from core.providers import manager

    manager.reset_registry()
    return tmp_path


def _register(policy, pid="prov-ov"):
    from core.providers import ProviderDescriptor, register_provider

    register_provider(ProviderDescriptor(
        provider_id=pid, display_name=pid,
        official_domain=f"{pid}.example", account_types=["user"],
        automation_policy=policy, policy_source="https://example/tos"))
    return pid


def test_loosening_override_requires_approval_and_does_not_apply():
    from core.providers import (
        AutomationPolicy, GateOutcome, PolicyOverridePending, policy_gate,
        set_policy_override,
    )
    from core import approval

    pid = _register(AutomationPolicy.PROHIBITED)
    with pytest.raises(PolicyOverridePending) as exc:
        set_policy_override(pid, AutomationPolicy.ALLOWED,
                            policy_source="https://example/tos#automation",
                            reason="operator wants automation")
    # Not applied yet — still refused.
    assert policy_gate(pid).decision is GateOutcome.REFUSE_AUTOMATION
    # A pending approval request now exists in the real queue.
    requests = [r for r in approval.load_requests() if r.get("id") == exc.value.approval_id]
    assert requests and requests[0]["status"] == "pending"


def test_approved_loosening_override_takes_effect():
    from core.providers import (
        AutomationPolicy, GateOutcome, PolicyOverridePending, policy_gate,
        set_policy_override,
    )
    from core import approval

    pid = _register(AutomationPolicy.PROHIBITED)
    with pytest.raises(PolicyOverridePending) as exc:
        set_policy_override(pid, AutomationPolicy.ALLOWED,
                            policy_source="https://example/tos#automation")
    aid = exc.value.approval_id
    approval.approve(aid, operator="operator")

    descriptor = set_policy_override(
        pid, AutomationPolicy.ALLOWED,
        policy_source="https://example/tos#automation", approval_id=aid)
    assert AutomationPolicy(descriptor.automation_policy) is AutomationPolicy.ALLOWED
    assert policy_gate(pid).decision is GateOutcome.ALLOW


def test_tightening_override_applies_immediately():
    from core.providers import (
        AutomationPolicy, GateOutcome, policy_gate, set_policy_override,
    )

    pid = _register(AutomationPolicy.ALLOWED)
    descriptor = set_policy_override(pid, AutomationPolicy.PROHIBITED,
                                     policy_source="https://example/tos#automation")
    assert AutomationPolicy(descriptor.automation_policy) is AutomationPolicy.PROHIBITED
    assert policy_gate(pid).decision is GateOutcome.REFUSE_AUTOMATION
