"""Emergency control (roadmap 23F).

Operator-triggered (or incident-triggered) containment: freeze a set of
dangerous capabilities at once — pause missions, stop autonomous execution,
freeze network changes, restrict vault access, lock Telegram control,
quarantine workers, force local-only operation. Every engage/release is
audited with a reason. Integrates with ``core.kai_emergency`` for the global
stop switch.
"""
from __future__ import annotations

import time

CAPABILITIES = (
    "missions", "autonomous_execution", "network_changes", "vault_access",
    "telegram_control", "worker_spawning", "cloud_providers",
)

_AUDIT: list = []


def _audit(event: str, detail: dict) -> None:
    _AUDIT.append({"event": event, "at": time.time(), **detail})


class EmergencyController:
    def __init__(self, engager=None, releaser=None):
        self._frozen = set()
        self._engager = engager
        self._releaser = releaser
        self.reason = ""
        self.engaged_at = None

    def engage(self, capabilities=None, reason: str = "", operator: str = "operator") -> dict:
        caps = list(capabilities or CAPABILITIES)
        self._frozen.update(caps)
        self.reason = reason
        self.engaged_at = time.time()
        _audit("emergency.engage", {"operator": operator, "reason": reason,
                                    "capabilities": caps})
        if self._engager:
            try:
                self._engager(operator, reason)
            except Exception:
                pass
        return self.status()

    def release(self, operator: str = "operator") -> dict:
        released = sorted(self._frozen)
        self._frozen.clear()
        self.reason = ""
        self.engaged_at = None
        _audit("emergency.release", {"operator": operator, "released": released})
        if self._releaser:
            try:
                self._releaser(operator)
            except Exception:
                pass
        return self.status()

    def is_frozen(self, capability: str) -> bool:
        return capability in self._frozen

    def status(self) -> dict:
        return {"engaged": bool(self._frozen),
                "frozen": sorted(self._frozen),
                "reason": self.reason,
                "engaged_at": self.engaged_at}


def audit_log() -> list:
    return list(_AUDIT)
