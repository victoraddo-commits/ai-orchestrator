"""Independent Verification (TF Phase 10 / 21L).

A verifier that is a DIFFERENT teammate from the producer reviews the output
(tests, security scan, endpoint checks). On failure the result carries
feedback so the work can go back to the producer. Verification never accepts
a producer verifying its own work.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

SOURCE = "teammate_verification"

_PREFERRED_SPECIALIZATIONS = ("verifier", "reviewer", "security", "qa")
_PREFERRED_SKILLS = ("verify_endpoint", "run_security_scan", "run_tests")


@dataclass
class VerificationResult:
    passed: bool
    verifier_id: str = ""
    checks: list = field(default_factory=list)
    feedback: str = ""

    def to_dict(self) -> dict:
        return {"passed": self.passed, "verifier_id": self.verifier_id,
                "checks": list(self.checks), "feedback": self.feedback}


def select_verifier(producer: Any, members: list, skill_registry=None):
    """Pick an independent verifier from ``members``. Never returns the
    producer. Prefers verifier/reviewer/security specializations, then
    verification skills. Returns None when no independent member exists."""
    pid = getattr(producer, "id", None)
    candidates = [m for m in members if getattr(m, "id", None) != pid]
    if not candidates:
        return None

    def rank(m):
        spec = getattr(m, "specialization", "")
        skills = set(getattr(m, "skills", None) or [])
        score = 0
        if spec in _PREFERRED_SPECIALIZATIONS:
            score += 10
        if skills & set(_PREFERRED_SKILLS):
            score += 5
        return score

    return max(candidates, key=rank)


def verify_output(producer: Any, verifier: Any, output: Any,
                  checks: list, bus: Any = None) -> VerificationResult:
    """Run ``checks`` (list of (name, callable)) against ``output`` using an
    independent ``verifier``. Raises ValueError if the verifier is the
    producer or is missing."""
    if verifier is None:
        raise ValueError("an independent verifier is required")
    if getattr(verifier, "id", None) == getattr(producer, "id", None):
        raise ValueError("a teammate may not verify its own work")

    results = []
    passed = True
    for name, fn in checks:
        try:
            outcome = fn(output)
            if isinstance(outcome, tuple):
                ok, detail = bool(outcome[0]), str(outcome[1])
            else:
                ok, detail = bool(outcome), ""
        except Exception as exc:
            ok, detail = False, f"raised {type(exc).__name__}: {exc}"
        results.append({"check": name, "passed": ok, "detail": detail})
        if not ok:
            passed = False

    feedback = ""
    if not passed:
        feedback = "; ".join(
            f"{c['check']}: {c['detail']}" for c in results if not c["passed"])

    result = VerificationResult(
        passed=passed,
        verifier_id=getattr(verifier, "id", ""),
        checks=results,
        feedback=feedback,
    )
    _emit(bus, result, producer)
    return result


def _emit(bus: Any, result: VerificationResult, producer: Any) -> None:
    if bus is None:
        return
    payload = {
        "producer_id": getattr(producer, "id", ""),
        "verifier_id": result.verifier_id,
        "passed": result.passed,
    }
    publish = getattr(bus, "publish", None)
    if callable(publish):
        try:
            publish("teammate.verification", payload, source=SOURCE)
        except TypeError:
            publish("teammate.verification", payload)
    elif callable(bus):
        bus("teammate.verification", payload)


class Verifier:
    """Stateful helper that selects an independent verifier and applies it."""

    def __init__(self, members: list = None, bus: Any = None,
                 skill_registry: Any = None) -> None:
        self.members = list(members or [])
        self.bus = bus
        self.skill_registry = skill_registry

    def verify(self, producer: Any, output: Any, checks: list,
               verifier: Any = None) -> VerificationResult:
        verifier = verifier or select_verifier(
            producer, self.members, self.skill_registry)
        return verify_output(producer, verifier, output, checks, bus=self.bus)
