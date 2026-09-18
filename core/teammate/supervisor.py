"""Teammate Supervisor (TF Phase 9 / 21K).

Health probes per teammate. Evaluates failure signals — no-progress, repeated
failures, malformed output, resource exhaustion, tool failures, unauthorized
actions, circular behavior, blocked dependencies — into one of:

    HEALTHY / DEGRADED / STALLED / FAILED / BLOCKED / QUARANTINED / RECOVERING

and an action (none / recover / replace / quarantine). ``apply`` drives the
registry transition and, where safe, kicks off recovery; failure and security
states are never auto-recovered.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)

SOURCE = "teammate_supervisor"

# signal -> health state
SIGNAL_TO_STATE = {
    "no_progress": "STALLED",
    "repeated_failures": "FAILED",
    "malformed_output": "DEGRADED",
    "resource_exhaustion": "DEGRADED",
    "tool_failure": "DEGRADED",
    "circular_behavior": "DEGRADED",
    "blocked_dependency": "BLOCKED",
    "unauthorized_action": "QUARANTINED",
    "security_violation": "QUARANTINED",
}

# health state -> registry lifecycle state
STATE_TO_REGISTRY = {
    "HEALTHY": "HEALTHY",
    "DEGRADED": "DEGRADED",
    "STALLED": "DEGRADED",   # registry has no STALLED state
    "FAILED": "FAILED",
    "BLOCKED": "BLOCKED",
    "QUARANTINED": "QUARANTINED",
    "RECOVERING": "RECOVERING",
}

_SEVERITY = {
    "HEALTHY": 0,
    "RECOVERING": 1,
    "BLOCKED": 2,
    "STALLED": 2,
    "DEGRADED": 3,
    "FAILED": 4,
    "QUARANTINED": 5,
}

_ACTION = {
    "HEALTHY": "none",
    "STALLED": "recover",
    "DEGRADED": "recover",
    "BLOCKED": "recover",
    "FAILED": "replace",
    "QUARANTINED": "quarantine",
    "RECOVERING": "none",
}

DEFAULT_THRESHOLDS = {
    "no_progress": 3,
    "repeated_failures": 3,
    "malformed_output": 2,
    "resource_exhaustion": 1,
    "tool_failure": 2,
    "circular_behavior": 1,
    "blocked_dependency": 1,
    "unauthorized_action": 1,
    "security_violation": 1,
}


@dataclass
class HealthDecision:
    state: str = "HEALTHY"
    action: str = "none"
    fired: list = field(default_factory=list)
    reason: str = ""

    def to_dict(self) -> dict:
        return {"state": self.state, "action": self.action,
                "fired": list(self.fired), "reason": self.reason}


class Supervisor:
    def __init__(self, registry=None, factory=None, thresholds=None) -> None:
        self.registry = registry
        self.factory = factory
        self.thresholds = dict(DEFAULT_THRESHOLDS)
        if thresholds:
            self.thresholds.update(thresholds)

    # -- evaluate -----------------------------------------------------------
    def evaluate(self, signals: Optional[dict] = None) -> HealthDecision:
        signals = signals or {}
        worst = "HEALTHY"
        fired = []
        for name, value in signals.items():
            if name not in SIGNAL_TO_STATE:
                continue
            threshold = self.thresholds.get(name, 1)
            count = value if isinstance(value, int) else (1 if value else 0)
            if count >= threshold and count > 0:
                fired.append(name)
                state = SIGNAL_TO_STATE[name]
                if _SEVERITY[state] > _SEVERITY[worst]:
                    worst = state
        return HealthDecision(
            state=worst,
            action=_ACTION.get(worst, "none"),
            fired=fired,
            reason=", ".join(fired) if fired else "all probes healthy",
        )

    # -- apply --------------------------------------------------------------
    def apply(self, teammate: Any, decision: HealthDecision) -> HealthDecision:
        if self.registry is None or decision.state == "HEALTHY":
            return decision

        registry_state = STATE_TO_REGISTRY.get(decision.state, decision.state)
        try:
            self.registry.transition(
                teammate.id, registry_state,
                f"supervisor: {decision.reason}")
        except Exception as exc:
            logger.debug("supervisor transition %s failed: %s", registry_state, exc)
            return decision

        history = getattr(teammate, "failure_history", None)
        if isinstance(history, list):
            history.append({"state": decision.state, "fired": list(decision.fired),
                            "action": decision.action})

        # quarantine and failure never auto-recover
        if decision.action == "quarantine":
            return decision

        if decision.action == "replace" and self.factory is not None:
            decision.reason += " | replacement requested"
            return decision

        if decision.action == "recover":
            try:
                self.registry.transition(
                    teammate.id, "RECOVERING",
                    "supervisor: auto-recover")
            except Exception as exc:
                logger.debug("auto-recover failed: %s", exc)
        return decision

    # -- convenience --------------------------------------------------------
    def supervise(self, teammate: Any, signals: Optional[dict] = None) -> HealthDecision:
        return self.apply(teammate, self.evaluate(signals))
