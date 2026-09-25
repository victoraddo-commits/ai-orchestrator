"""Three-layer smart-home discovery.

1. Passive : SSDP (1900) / mDNS (5353) / Tuya (6667) broadcasts.
2. Active  : TCP port fingerprint of the LAN.
3. Provider: adapter queries (Phase E2).

This module is pure/side-effect-light: parsing and classification are unit
tested; the network runners are thin and injectable.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from core.smarthome import oui

# Port -> (provider, vendor-hint). 6668 is Tuya's TCP control port; 8123 HA.
_PORT_MAP: dict[int, tuple[str | None, str | None]] = {
    8123: ("homeassistant", None),
    6668: ("tuya", "Tuya Smart"),
    6667: ("tuya", "Tuya Smart"),
    1883: ("mqtt", None),
    8883: ("mqtt", None),
    554: ("camera", None),
    2020: ("camera", None),
}


@dataclass
class Candidate:
    ip: str
    provider: str | None = None
    provider_id: str | None = None
    vendor: str | None = None
    mac: str | None = None
    ports: list[int] = field(default_factory=list)

    def to_dedup_key(self) -> str:
        if self.provider and self.provider_id:
            return f"{self.provider}:{self.provider_id}"
        return f"ip:{self.ip}"


def classify_ports(ports: set[int]) -> tuple[str | None, str | None]:
    """Return ``(provider, vendor_hint)`` for a set of open ports."""
    for p in sorted(ports):
        if p in _PORT_MAP:
            return _PORT_MAP[p]
    return None, None


def parse_ssdp(data: bytes) -> dict:
    """Extract the device IP from an SSDP NOTIFY/M-SEARCH response."""
    text = (data or b"").decode("utf-8", "replace")
    out: dict = {}
    for line in text.splitlines():
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        k = k.strip().upper()
        v = v.strip()
        if k == "LOCATION" and "//" in v:
            out["ip"] = v.split("//", 1)[1].split("/", 1)[0].split(":", 1)[0]
        elif k == "SERVER":
            out["server"] = v
    return out


def parse_tuya(data: dict | bytes) -> dict:
    """Normalise a Tuya broadcast/response into discovery fields."""
    if isinstance(data, bytes):
        data = json.loads(data.decode("utf-8", "replace"))
    ip = data.get("ip")
    gw = data.get("gwId") or data.get("devId")
    return {"ip": ip, "provider_id": gw, "vendor": "Tuya Smart"}
