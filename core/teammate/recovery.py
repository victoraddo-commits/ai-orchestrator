"""Teammate Recovery (TF Phase 11 / 21M).

On a teammate failure: preserve mission state, capture evidence, classify the
failure, and pick the least-destructive safe action — retry, replace the
worker / model / teammate, roll back, reassign remaining work, or escalate.

Hard rule: a single-worker failure NEVER restarts the mission. Mission state
is preserved and only the affected work is recovered.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

SOURCE = "teammate_recovery"

# failure class -> recovery action
ACTION_BY_CLASS = {
    "tool_failure": "retry",
    "malformed_output": "retry",
    "transient": "retry",
    "resource_exhaustion": "replace_worker",
    "worker_unavailable": "replace_worker",
    "provider_unavailable": "replace_worker",
    "model_failure": "replace_model",
    "repeated_failures": "replace_teammate",
    "corrupted_state": "replace_teammate",
    "partial_deploy": "rollback",
    "destructive_action": "rollback",
    "unauthorized_action": "escalate",
    "security_violation": "escalate",
}

_ACTION_SEVERITY = {
    "retry": 0,
    "replace_worker": 1,
    "replace_model": 1,
    "reassign": 1,
    "replace_teammate": 2,
    "rollback": 3,
    "escalate": 4,
}


@dataclass
class RecoveryPlan:
    failure_class: str
    action: str = "escalate"
    preserve_mission: bool = True
    restart_mission: bool = False
    evidence: dict = field(default_factory=dict)
    remaining_work: list = field(default_factory=list)
    notes: str = ""

    def to_dict(self) -> dict:
        return {
            "failure_class": self.failure_class,
            "action": self.action,
            "preserve_mission": self.preserve_mission,
            "restart_mission": self.restart_mission,
            "evidence": dict(self.evidence),
            "remaining_work": list(self.remaining_work),
            "notes": self.notes,
        }


def classify(failure_class: str) -> str:
    return ACTION_BY_CLASS.get(failure_class, "escalate")


class RecoveryManager:
    def __init__(self, registry=None, integrator=None, factory=None,
                 bus=None, rollback: Optional[Callable] = None) -> None:
        self.registry = registry
        self.integrator = integrator
        self.factory = factory
        self.bus = bus
        self.rollback = rollback

    def _emit(self, topic: str, payload: dict) -> None:
        if self.bus is None:
            return
        publish = getattr(self.bus, "publish", None)
        if callable(publish):
            try:
                publish(topic, payload, source=SOURCE)
            except TypeError:
                publish(topic, payload)
        elif callable(self.bus):
            self.bus(topic, payload)

    # -- planning -----------------------------------------------------------
    def plan_recovery(self, mission_state: Any, failed_teammate: Any,
                      failure_class: str, evidence: Optional[dict] = None,
                      remaining_work: Optional[list] = None) -> RecoveryPlan:
        action = classify(failure_class)
        return RecoveryPlan(
            failure_class=failure_class,
            action=action,
            preserve_mission=True,
            restart_mission=False,
            evidence={"mission": _snapshot(mission_state),
                      "teammate": getattr(failed_teammate, "id", None),
                      "failure_class": failure_class,
                      **(evidence or {})},
            remaining_work=list(remaining_work or []),
            notes=f"classified {failure_class} -> {action}",
        )

    # -- execution ----------------------------------------------------------
    def recover(self, mission_state: Any, failed_teammate: Any,
                failure_class: str, evidence: Optional[dict] = None,
                remaining_work: Optional[list] = None,
                requirement: Any = None, rollback: Optional[Callable] = None
                ) -> RecoveryPlan:
        plan = self.plan_recovery(mission_state, failed_teammate,
                                  failure_class, evidence, remaining_work)

        if plan.action == "retry":
            plan.notes += " | safe to retry (no state change)"
        elif plan.action == "escalate":
            plan.notes += " | escalated to operator (no automatic action)"
        elif plan.action == "rollback":
            fn = rollback or self.rollback
            if callable(fn):
                fn(mission_state, failed_teammate)
                plan.notes += " | rollback executed"
            else:
                plan.notes += " | rollback requested (no handler)"
        elif plan.action == "replace_teammate":
            if self.factory is not None and requirement is not None:
                self.factory.create_from_requirement(
                    requirement, mission_id=_mission_id(mission_state))
                plan.notes += " | replacement teammate created"
            else:
                plan.notes += " | replacement requested (no factory)"
        elif plan.action in ("replace_worker", "replace_model"):
            plan.notes += " | worker/model replacement requested"

        self._emit("teammate.recovery", plan.to_dict())
        return plan


def _mission_id(state: Any) -> Any:
    if isinstance(state, dict):
        return state.get("id") or state.get("mission_id")
    return getattr(state, "id", None) or getattr(state, "mission_id", None)


def _snapshot(state: Any) -> Any:
    if state is None:
        return None
    if isinstance(state, dict):
        return {k: state[k] for k in list(state)[:10]}
    for attr in ("id", "mission_id"):
        val = getattr(state, attr, None)
        if val is not None:
            return val
    return str(state)
