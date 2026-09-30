"""The ToS/legality gate — a blocking precondition for automating any provider.

Every automation decision funnels through :func:`policy_gate`. The gate is
fail-closed:

* ``ALLOWED``  → automation permitted.
* ``UNKNOWN``  → human confirmation required; the automated path must NOT run.
* ``PROHIBITED`` → refuse the automated path and route to human takeover.

:func:`ensure_automation_allowed` is the single entry point the future
onboarding engine MUST call before any provider action; it returns the decision
on ALLOWED and raises :class:`HumanConfirmationRequired` /
:class:`AutomationBlocked` otherwise. Every evaluation is published on the
event bus (``provider.policy.gate``) and written to the HMAC audit log.
"""

from __future__ import annotations

from typing import Union

from core import audit_logger, kai_event_bus

from core.providers import store
from core.providers.schema import (
    AutomationPolicy,
    GateDecision,
    GateOutcome,
    ProviderDescriptor,
    now_iso,
)

SOURCE = "provider_gate"

_DECISION_BY_POLICY: dict[AutomationPolicy, tuple[GateOutcome, bool, str]] = {
    AutomationPolicy.ALLOWED: (
        GateOutcome.ALLOW,
        False,
        "provider automation policy permits automation",
    ),
    AutomationPolicy.UNKNOWN: (
        GateOutcome.REQUIRE_HUMAN_CONFIRMATION,
        True,
        "provider automation policy is unknown — human confirmation required",
    ),
    AutomationPolicy.PROHIBITED: (
        GateOutcome.REFUSE_AUTOMATION,
        True,
        "provider automation policy prohibits automation — human takeover required",
    ),
}


class AutomationBlocked(RuntimeError):
    """Raised when a provider's policy refuses the automated path."""

    def __init__(self, decision: GateDecision):
        super().__init__(decision.reason)
        self.decision = decision


class HumanConfirmationRequired(RuntimeError):
    """Raised when a provider's policy requires human confirmation first."""

    def __init__(self, decision: GateDecision):
        super().__init__(decision.reason)
        self.decision = decision


def _coerce(provider: Union[ProviderDescriptor, str]) -> ProviderDescriptor:
    if isinstance(provider, ProviderDescriptor):
        return provider
    descriptor = store.effective(provider)
    if descriptor is None:
        raise store.ProviderNotFound(provider)
    return descriptor


def policy_gate(provider: Union[ProviderDescriptor, str]) -> GateDecision:
    """Evaluate the policy gate for *provider* (a descriptor or provider id).

    Never raises for a normal decision — returns the structured
    :class:`GateDecision`. Unknown provider ids raise ``ProviderNotFound``.
    """
    descriptor = _coerce(provider)
    outcome, requires_human, reason = _DECISION_BY_POLICY[descriptor.automation_policy]
    decision = GateDecision(
        provider_id=descriptor.provider_id,
        decision=outcome,
        reason=reason,
        policy_source=descriptor.policy_source,
        requires_human=requires_human,
        automation_policy=descriptor.automation_policy,
        evaluated_at=now_iso(),
    )
    kai_event_bus.publish(
        "provider.policy.gate",
        {
            "provider_id": decision.provider_id,
            "decision": decision.decision.value,
            "automation_policy": decision.automation_policy.value,
            "requires_human": decision.requires_human,
            "policy_source": decision.policy_source,
        },
        source=SOURCE,
    )
    audit_logger.log_audit_event(
        event_type="provider.policy.gate",
        operator=SOURCE,
        endpoint=f"provider/{decision.provider_id}",
        method="CHECK",
        status_code=200,
        details={
            "decision": decision.decision.value,
            "automation_policy": decision.automation_policy.value,
            "requires_human": decision.requires_human,
            "reason": decision.reason,
            "policy_source": decision.policy_source,
        },
    )
    return decision


def ensure_automation_allowed(provider_id: str) -> GateDecision:
    """Return the decision when automation is allowed, else raise.

    THE blocking precondition: call this before any provider action.
    """
    decision = policy_gate(provider_id)
    if decision.decision is GateOutcome.ALLOW:
        return decision
    if decision.decision is GateOutcome.REQUIRE_HUMAN_CONFIRMATION:
        raise HumanConfirmationRequired(decision)
    raise AutomationBlocked(decision)


__all__ = [
    "AutomationBlocked",
    "HumanConfirmationRequired",
    "policy_gate",
    "ensure_automation_allowed",
]
