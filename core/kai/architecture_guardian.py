"""Architecture Guardian (roadmap 23E).

Scans a service/port inventory for structural problems: duplicate service
names, two services claiming the same port, and services marked enabled but
not actually running (dead services). Pure logic over an injected inventory so
it is testable and side-effect free; callers supply live probes.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Finding:
    kind: str
    target: str
    detail: str = ""

    def to_dict(self) -> dict:
        return {"kind": self.kind, "target": self.target, "detail": self.detail}


class ArchitectureGuardian:
    def __init__(self, inventory_provider=None):
        self.inventory_provider = inventory_provider

    def _inventory(self, services):
        if services is not None:
            return list(services)
        if self.inventory_provider is not None:
            return list(self.inventory_provider())
        return []

    def scan(self, services=None) -> list:
        services = self._inventory(services)
        findings = []

        seen_names = set()
        ports: dict = {}
        for svc in services:
            name = svc.get("name")
            if name in seen_names:
                findings.append(Finding("duplicate_service", str(name),
                                        "duplicate service name"))
            seen_names.add(name)

            port = svc.get("port")
            if port is not None:
                ports.setdefault(port, []).append(name)

        for port, names in ports.items():
            if len(names) > 1:
                findings.append(Finding(
                    "duplicate_port", str(port),
                    f"claimed by {sorted(str(n) for n in names)}"))

        for svc in services:
            if svc.get("enabled") and not svc.get("active"):
                findings.append(Finding(
                    "dead_service", str(svc.get("name")),
                    "enabled but not running"))

        return findings
