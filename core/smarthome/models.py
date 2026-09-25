"""Data models for the KAI Smart Home fabric.

Deliberately independent of ``core/device_registry.py`` (the *mobile* device
registry). Smart-home devices are keyed by a stable KAI id and may be bound to
one or more providers.
"""
from __future__ import annotations

import datetime
import secrets
from dataclasses import asdict, dataclass, field
from enum import Enum


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


class DeviceKind(str, Enum):
    LIGHT = "light"
    SWITCH = "switch"
    SENSOR = "sensor"
    CLIMATE = "climate"
    LOCK = "lock"
    DOOR = "door"
    CAMERA = "camera"
    MEDIA = "media"
    COVER = "cover"
    HUB = "hub"
    UNKNOWN = "unknown"


class Capability(str, Enum):
    ON_OFF = "on_off"
    BRIGHTNESS = "brightness"
    COLOR = "color"
    TEMPERATURE = "temperature"
    LOCK = "lock"
    MOTION = "motion"
    CONTACT = "contact"
    BATTERY = "battery"
    POWER = "power"
    STREAM = "stream"


@dataclass
class Room:
    id: str
    name: str
    area: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class DeviceCreate:
    name: str
    provider: str
    provider_id: str
    kind: DeviceKind = DeviceKind.UNKNOWN
    room: str | None = None
    capabilities: list[Capability] = field(default_factory=list)
    address: str | None = None
    mac: str | None = None
    vendor: str | None = None


@dataclass
class DeviceRecord:
    id: str
    name: str
    provider: str
    provider_id: str
    kind: DeviceKind
    room: str | None
    capabilities: list[Capability]
    address: str | None
    mac: str | None
    vendor: str | None
    created_at: str
    updated_at: str
    state: dict = field(default_factory=dict)
    state_fresh: bool = False
    last_seen: str | None = None

    @classmethod
    def new(cls, name: str, provider: str, provider_id: str,
            kind: DeviceKind = DeviceKind.UNKNOWN, room: str | None = None,
            capabilities: list[Capability] | None = None,
            address: str | None = None, mac: str | None = None,
            vendor: str | None = None) -> "DeviceRecord":
        ts = _now_iso()
        return cls(
            id="smd_" + secrets.token_hex(6), name=name, provider=provider,
            provider_id=provider_id, kind=kind, room=room,
            capabilities=list(capabilities or []), address=address, mac=mac,
            vendor=vendor, created_at=ts, updated_at=ts,
        )

    def to_dict(self) -> dict:
        d = asdict(self)
        d["kind"] = self.kind.value
        d["capabilities"] = [c.value for c in self.capabilities]
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "DeviceRecord":
        d = dict(d)
        d["kind"] = DeviceKind(d.get("kind", "unknown"))
        d["capabilities"] = [Capability(c) for c in d.get("capabilities", [])]
        return cls(**d)
