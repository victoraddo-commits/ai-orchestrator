"""One-shot smart-home discovery: LAN -> candidates -> canonical registry.

Passive/active scanning is best-effort and injectable; ``ingest_candidates`` is
pure and unit-tested. Run:  .venv/bin/python scripts/smarthome_discover.py
"""
from __future__ import annotations

import json
import socket
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.smarthome import registry as R  # noqa: E402
from core.smarthome.discovery import Candidate, classify_ports  # noqa: E402
from core.smarthome.models import DeviceCreate, DeviceKind  # noqa: E402

_KIND_FOR_PROVIDER = {"tuya": DeviceKind.SWITCH, "homeassistant": DeviceKind.UNKNOWN,
                      "mqtt": DeviceKind.UNKNOWN, "camera": DeviceKind.CAMERA}


def ingest_candidates(cands: list[Candidate]) -> dict:
    created = updated = 0
    for c in cands:
        if not c.provider:
            continue
        pid = c.provider_id or c.ip
        existed = any(d.provider == c.provider and d.provider_id == pid
                      for d in R.list_devices())
        R.upsert(DeviceCreate(
            name=c.provider_id or f"{c.vendor or c.provider} {c.ip}",
            provider=c.provider, provider_id=pid,
            kind=_KIND_FOR_PROVIDER.get(c.provider, DeviceKind.UNKNOWN),
            address=c.ip, mac=c.mac, vendor=c.vendor))
        if existed:
            updated += 1
        else:
            created += 1
    return {"created": created, "updated": updated,
            "total": len(R.list_devices())}


def scan_ports(ip: str, ports: tuple[int, ...] = (8123, 6668, 6667, 1883, 554),
               timeout: float = 0.25) -> set[int]:
    open_ports = set()
    for p in ports:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(timeout)
        try:
            if s.connect_ex((ip, p)) == 0:
                open_ports.add(p)
        finally:
            s.close()
    return open_ports


def _probe(ip: str) -> Candidate:
    ports = scan_ports(ip)
    provider, vendor = classify_ports(ports)
    return Candidate(ip=ip, provider=provider, vendor=vendor, ports=sorted(ports))


def sweep(ips: list[str], workers: int = 64) -> list[Candidate]:
    """Parallel LAN sweep; returns only candidates with a smart-home provider."""
    with ThreadPoolExecutor(max_workers=workers) as ex:
        return [c for c in ex.map(_probe, ips) if c.provider]


def main() -> int:
    targets = sys.argv[1:] or ["192.168.1.16"]
    cands = sweep(targets) if len(targets) > 1 else [_probe(targets[0])]
    report = ingest_candidates(cands)
    print(json.dumps({"candidates": [c.__dict__ for c in cands], "report": report}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
