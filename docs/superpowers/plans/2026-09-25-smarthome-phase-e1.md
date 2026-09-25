# Smart Home Fabric — Phase E1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build KAI's Smart Home foundation — a canonical device registry, three-layer LAN discovery, a provider adapter interface, and a Home Assistant fabric adapter — proven end-to-end by discovering and identifying real devices on the LAN.

**Architecture:** New package `core/smarthome/` inside the KAI orchestrator (never a second system). Discovery is passive (mDNS/SSDP/Tuya broadcasts), active (port fingerprint + OUI), then provider-API. A canonical registry (JSON + `fcntl.flock`, schema-versioned, mirroring `core/app_registry.py`) is the single device store; adapters translate providers (HA first) into it. It reuses the existing Event Bus, Command Bus, AgentGuard, Vault and World Model — nothing duplicated. Home Assistant runs as a fabric provider (LXC 115) that KAI drives.

**Tech Stack:** Python 3.12 (orchestrator venv), FastAPI, stdlib sockets, `scapy` (optional, for passive capture), `pytest`; HA Container in LXC 115 on Proxmox B.

**Spec:** `docs/superpowers/specs/2026-09-25-smarthome-design.md`
**Runner repo:** CT111 `/opt/ai-orchestrator` (branch `runner-kai-2.0-20260918`). Edit on the working copy, push to CT111, run tests with `.venv/bin/python -m pytest`.

---

## File Structure

| File | Responsibility |
|---|---|
| `core/smarthome/__init__.py` | package marker (empty) |
| `core/smarthome/models.py` | `DeviceRecord`, `DeviceCreate`, `Room`, `Capability`, enums |
| `core/smarthome/registry.py` | canonical store: atomic JSON + flock, CRUD, queries, hooks |
| `core/smarthome/oui.py` | MAC OUI → vendor prefix map (Tuya, eWeLink, Espressif, Amazon…) |
| `core/smarthome/discovery.py` | 3-layer discovery → candidate dicts |
| `core/smarthome/adapters/__init__.py` | adapter registry |
| `core/smarthome/adapters/base.py` | `ProviderAdapter` interface + `AdapterError` |
| `core/smarthome/adapters/homeassistant.py` | HA REST/WS adapter (token from Vault) |
| `scripts/smarthome_discover.py` | one-shot discovery CLI → registry + report |
| `tests/smarthome/test_models.py` | model validation |
| `tests/smarthome/test_registry.py` | registry atomicity/CRUD/hooks |
| `tests/smarthome/test_oui.py` | vendor mapping |
| `tests/smarthome/test_discovery.py` | parsers + fingerprinting (recorded packets) |
| `tests/smarthome/test_adapter_base.py` | interface contract w/ fake adapter |
| `tests/smarthome/test_adapter_ha.py` | HA adapter against a fake HTTP server |

---

### Task 1: Models

**Files:**
- Create: `core/smarthome/__init__.py`
- Create: `core/smarthome/models.py`
- Test: `tests/smarthome/test_models.py`

- [ ] **Step 1: Write the failing test**

```python
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core.smarthome.models import DeviceCreate, DeviceRecord, Room, Capability, DeviceKind


def test_device_create_defaults_and_unknown_kind():
    d = DeviceCreate(name="Living room lamp", provider="tuya", provider_id="bf123")
    assert d.kind == DeviceKind.UNKNOWN
    assert d.room is None
    assert d.capabilities == []


def test_device_record_roundtrip_and_stable_id():
    d = DeviceRecord.new(
        name="Front door", provider="tuya", provider_id="bf999",
        kind=DeviceKind.LOCK, capabilities=[Capability.LOCK, Capability.BATTERY],
        room="Entrance", address="192.168.1.16", mac="50:8a:06:6a:3c:36",
    )
    assert d.id.startswith("smd_")
    blob = d.to_dict()
    back = DeviceRecord.from_dict(blob)
    assert back == d


def test_room_word_and_unknown_state_preserved():
    r = Room(id="entrance", name="Entrance", area="Ground floor")
    assert r.to_dict()["name"] == "Entrance"
    d = DeviceRecord.new(name="x", provider="ha", provider_id="light.x")
    # State is never invented: unknown stays unknown
    assert d.state == {}
    assert d.state_fresh is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /opt/ai-orchestrator && .venv/bin/python -m pytest tests/smarthome/test_models.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'core.smarthome'`

- [ ] **Step 3: Write minimal implementation**

```python
# core/smarthome/__init__.py
"""KAI Smart Home fabric (Phase E). Built inside KAI; Home Assistant is a provider."""
```

```python
# core/smarthome/models.py
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /opt/ai-orchestrator && .venv/bin/python -m pytest tests/smarthome/test_models.py -q`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add core/smarthome/__init__.py core/smarthome/models.py tests/smarthome/test_models.py
git commit -m "feat(smarthome): models for the KAI smart-home fabric"
```

---

### Task 2: Registry (atomic, flock, hooks)

**Files:**
- Create: `core/smarthome/registry.py`
- Test: `tests/smarthome/test_registry.py`

- [ ] **Step 1: Write the failing test**

```python
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core.smarthome import registry as R
from core.smarthome.models import DeviceCreate, DeviceKind


def _use_tmp(monkeypatch, tmp_path):
    monkeypatch.setattr(R, "_default_memory_dir", lambda: tmp_path)


def test_upsert_creates_then_updates_by_provider_key(monkeypatch, tmp_path):
    _use_tmp(monkeypatch, tmp_path)
    rec = R.upsert(DeviceCreate(name="Lamp", provider="tuya", provider_id="a1",
                                kind=DeviceKind.LIGHT, address="192.168.1.16"))
    assert rec.id.startswith("smd_")
    again = R.upsert(DeviceCreate(name="Lamp 2", provider="tuya", provider_id="a1"))
    assert again.id == rec.id and again.name == "Lamp 2"
    assert len(R.list_devices()) == 1


def test_get_update_delete_and_unknown(monkeypatch, tmp_path):
    _use_tmp(monkeypatch, tmp_path)
    rec = R.upsert(DeviceCreate(name="S", provider="ha", provider_id="switch.x"))
    assert R.get(rec.id).name == "S"
    assert R.get("nope") is None
    R.set_state(rec.id, {"on": True})
    assert R.get(rec.id).state == {"on": True}
    R.delete(rec.id)
    assert R.get(rec.id) is None


def test_hooks_fire_on_create(monkeypatch, tmp_path):
    _use_tmp(monkeypatch, tmp_path)
    seen = []
    R.register_hook("on_create", lambda rec: seen.append(rec.id))
    R.upsert(DeviceCreate(name="X", provider="tuya", provider_id="z"))
    assert len(seen) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /opt/ai-orchestrator && .venv/bin/python -m pytest tests/smarthome/test_registry.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'core.smarthome.registry'`

- [ ] **Step 3: Write minimal implementation**

```python
# core/smarthome/registry.py
"""Canonical Smart Home device registry.

Atomic read-modify-write over ``memory/smarthome_registry.json`` guarded by
``fcntl.flock`` (same pattern as ``core/app_registry.py``). ONE registry for
smart-home devices; the mobile registry in ``core/device_registry.py`` is a
different concern and is not touched.
"""
from __future__ import annotations

import fcntl
import json
import logging
import os
import shutil
from pathlib import Path
from typing import Callable

from core.smarthome.models import DeviceCreate, DeviceRecord, _now_iso

logger = logging.getLogger(__name__)

CURRENT_SCHEMA_VERSION = 1
REGISTRY_FILENAME = "smarthome_registry.json"

_hooks: dict[str, list[Callable]] = {"on_create": [], "on_update": [], "on_delete": []}


def register_hook(event: str, callback: Callable) -> None:
    if event in _hooks:
        _hooks[event].append(callback)


def _fire(event: str, rec: DeviceRecord) -> None:
    for cb in _hooks.get(event, []):
        try:
            cb(rec)
        except Exception:  # noqa: BLE001
            logger.exception("smarthome registry hook %s failed", event)


def _default_memory_dir() -> Path:
    override = os.environ.get("AI_ORCHESTRATOR_MEMORY_DIR")
    return Path(override) if override else Path("memory")


def _path() -> Path:
    return _default_memory_dir() / REGISTRY_FILENAME


def _load() -> dict:
    p = _path()
    if not p.exists():
        return {"schema_version": CURRENT_SCHEMA_VERSION, "devices": {}}
    with p.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _save(data: dict) -> None:
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, sort_keys=True)
    shutil.move(str(tmp), str(p))


def _with_lock(fn):
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    lock = p.with_suffix(".json.lock")
    with lock.open("w") as lf:
        fcntl.flock(lf, fcntl.LOCK_EX)
        try:
            return fn()
        finally:
            fcntl.flock(lf, fcntl.LOCK_UN)


def _by_provider_key(data: dict, provider: str, provider_id: str) -> str | None:
    for did, raw in data["devices"].items():
        if raw.get("provider") == provider and raw.get("provider_id") == provider_id:
            return did
    return None


def upsert(create: DeviceCreate) -> DeviceRecord:
    def _do():
        data = _load()
        existing = _by_provider_key(data, create.provider, create.provider_id)
        if existing:
            rec = DeviceRecord.from_dict(data["devices"][existing])
            rec.name = create.name or rec.name
            if create.kind.value != "unknown":
                rec.kind = create.kind
            if create.room is not None:
                rec.room = create.room
            if create.capabilities:
                rec.capabilities = list(create.capabilities)
            rec.address = create.address or rec.address
            rec.mac = create.mac or rec.mac
            rec.vendor = create.vendor or rec.vendor
            rec.updated_at = _now_iso()
            rec.last_seen = rec.updated_at
            data["devices"][existing] = rec.to_dict()
            _save(data)
            _fire("on_update", rec)
            return rec
        rec = DeviceRecord.new(
            name=create.name, provider=create.provider,
            provider_id=create.provider_id, kind=create.kind, room=create.room,
            capabilities=create.capabilities, address=create.address,
            mac=create.mac, vendor=create.vendor)
        rec.last_seen = rec.created_at
        data["devices"][rec.id] = rec.to_dict()
        _save(data)
        _fire("on_create", rec)
        return rec
    return _with_lock(_do)


def get(device_id: str) -> DeviceRecord | None:
    raw = _load()["devices"].get(device_id)
    return DeviceRecord.from_dict(raw) if raw else None


def list_devices() -> list[DeviceRecord]:
    return [DeviceRecord.from_dict(v) for v in _load()["devices"].values()]


def set_state(device_id: str, state: dict, fresh: bool = True) -> DeviceRecord | None:
    def _do():
        data = _load()
        if device_id not in data["devices"]:
            return None
        rec = DeviceRecord.from_dict(data["devices"][device_id])
        rec.state = state
        rec.state_fresh = fresh
        rec.last_seen = _now_iso()
        data["devices"][device_id] = rec.to_dict()
        _save(data)
        _fire("on_update", rec)
        return rec
    return _with_lock(_do)


def delete(device_id: str) -> bool:
    def _do():
        data = _load()
        raw = data["devices"].pop(device_id, None)
        if raw is None:
            return False
        _save(data)
        _fire("on_delete", DeviceRecord.from_dict(raw))
        return True
    return _with_lock(_do)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /opt/ai-orchestrator && .venv/bin/python -m pytest tests/smarthome/test_registry.py -q`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add core/smarthome/registry.py tests/smarthome/test_registry.py
git commit -m "feat(smarthome): canonical atomic device registry"
```

---

### Task 3: OUI vendor map

**Files:**
- Create: `core/smarthome/oui.py`
- Test: `tests/smarthome/test_oui.py`

- [ ] **Step 1: Write the failing test**

```python
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core.smarthome import oui


def test_known_vendors():
    assert oui.vendor_for("50:8a:06:6a:3c:36") == "Tuya Smart"
    assert oui.vendor_for("50-8A-06-00-00-01") == "Tuya Smart"
    assert oui.vendor_for("B4:E6:2D:11:22:33") == "Espressif"


def test_unknown_vendor_is_none():
    assert oui.vendor_for("aa:bb:cc:dd:ee:ff") is None
    assert oui.vendor_for("") is None


def test_prefix_normalisation():
    assert oui.prefix("508A066A3C36") == "50:8A:06"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /opt/ai-orchestrator && .venv/bin/python -m pytest tests/smarthome/test_oui.py -q`
Expected: FAIL — no module `core.smarthome.oui`

- [ ] **Step 3: Write minimal implementation**

```python
# core/smarthome/oui.py
"""Minimal MAC OUI -> vendor map for smart-home discovery.

Not a full IEEE registry: a curated set of prefixes for devices seen in this
estate plus common smart-home silicon. Unknown prefixes return ``None`` — we
never guess a vendor.
"""
from __future__ import annotations

_VENDORS: dict[str, str] = {
    "50:8A:06": "Tuya Smart",
    "68:57:2D": "Tuya Smart",
    "10:52:1C": "Tuya Smart",
    "B4:E6:2D": "Espressif",
    "24:0A:C4": "Espressif",
    "84:F3:EB": "Espressif",
    "EC:FA:BC": "Espressif",
    "68:C6:3A": "Espressif",
    "F4:CF:A2": "Espressif",
    "18:B4:30": "Nest",
    "44:61:32": "ecobee",
    "0C:47:C9": "Amazon",
    "68:54:FD": "Amazon",
    "FC:65:DE": "Amazon",
    "44:65:0D": "Amazon",
    "34:D2:70": "Amazon",
    "D8:F1:5B": "eero",
    "F8:BB:BF": "eero",
    "00:17:88": "Philips Hue",
    "EC:B5:FA": "Philips Hue",
    "B0:CE:18": "Sonoff/eWeLink",
    "84:0D:8E": "Sonoff/eWeLink",
}


def prefix(mac: str) -> str:
    """Return the normalised ``AA:BB:CC`` OUI prefix (uppercase, colon-separated)."""
    clean = "".join(c for c in (mac or "") if c.isalnum()).upper()
    clean = clean[:6]
    if len(clean) < 6:
        return ""
    return ":".join(clean[i:i + 2] for i in range(0, 6, 2))


def vendor_for(mac: str) -> str | None:
    """Return the vendor for a MAC, or ``None`` when the prefix is not known."""
    p = prefix(mac)
    return _VENDORS.get(p) if p else None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /opt/ai-orchestrator && .venv/bin/python -m pytest tests/smarthome/test_oui.py -q`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add core/smarthome/oui.py tests/smarthome/test_oui.py
git commit -m "feat(smarthome): curated OUI vendor map"
```

---

### Task 4: Discovery (passive/active fingerprints)

**Files:**
- Create: `core/smarthome/discovery.py`
- Test: `tests/smarthome/test_discovery.py`

- [ ] **Step 1: Write the failing test**

```python
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core.smarthome import discovery as D


def test_parse_ssdp_notify():
    pkt = ("NOTIFY * HTTP/1.1\r\nHOST: 239.255.255.250:1900\r\n"
           "NT: urn:schemas-upnp-org:device:Basic:1\r\n"
           "SERVER: Tuya/1.0 UPnP/1.0\r\nLOCATION: http://192.168.1.16:6668/x.xml\r\n"
           "USN: uuid:abc::urn:schemas-upnp-org:device:Basic:1\r\n\r\n")
    info = D.parse_ssdp(pkt.encode())
    assert info["ip"] == "192.168.1.16"


def test_parse_tuya_broadcast():
    # Tuya devices broadcast a UDP payload on 6667; we only need identity fields.
    data = {"ip": "192.168.1.16", "gwId": "bf123", "productKey": "abc"}
    info = D.parse_tuya(data)
    assert info == {"ip": "192.168.1.16", "provider_id": "bf123", "vendor": "Tuya Smart"}


def test_fingerprint_port_map():
    assert D.classify_ports({6668}) == ("tuya", "Tuya Smart")
    assert D.classify_ports({8123}) == ("homeassistant", None)
    assert D.classify_ports({1883}) == ("mqtt", None)
    assert D.classify_ports({22, 80}) == (None, None)


def test_candidate_shape():
    c = D.Candidate(ip="192.168.1.16", mac="50:8a:06:6a:3c:36", provider="tuya",
                    provider_id="bf123", vendor="Tuya Smart", ports=[6668])
    assert c.to_dedup_key() == "tuya:bf123"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /opt/ai-orchestrator && .venv/bin/python -m pytest tests/smarthome/test_discovery.py -q`
Expected: FAIL — no module `core.smarthome.discovery`

- [ ] **Step 3: Write minimal implementation**

```python
# core/smarthome/discovery.py
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /opt/ai-orchestrator && .venv/bin/python -m pytest tests/smarthome/test_discovery.py -q`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add core/smarthome/discovery.py tests/smarthome/test_discovery.py
git commit -m "feat(smarthome): discovery parsers and port fingerprinting"
```

---

### Task 5: Adapter interface + fake

**Files:**
- Create: `core/smarthome/adapters/__init__.py`
- Create: `core/smarthome/adapters/base.py`
- Test: `tests/smarthome/test_adapter_base.py`

- [ ] **Step 1: Write the failing test**

```python
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core.smarthome.adapters.base import ProviderAdapter, AdapterError


class Fake(ProviderAdapter):
    name = "fake"

    def identify(self):
        return [{"provider_id": "x1", "name": "Fake device"}]

    def get_state(self, provider_id):
        return {"on": True}

    def set_state(self, provider_id, changes):
        if "bad" in changes:
            raise AdapterError("unsupported")
        return {"on": changes.get("on")}


def test_interface_contract():
    a = Fake()
    assert a.name == "fake"
    assert a.identify()[0]["provider_id"] == "x1"
    assert a.get_state("x1") == {"on": True}
    assert a.set_state("x1", {"on": False}) == {"on": False}


def test_errors_are_typed():
    a = Fake()
    try:
        a.set_state("x1", {"bad": 1})
        assert False
    except AdapterError as e:
        assert "unsupported" in str(e)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /opt/ai-orchestrator && .venv/bin/python -m pytest tests/smarthome/test_adapter_base.py -q`
Expected: FAIL — no module `core.smarthome.adapters.base`

- [ ] **Step 3: Write minimal implementation**

```python
# core/smarthome/adapters/__init__.py
"""Provider adapters for the KAI smart-home fabric."""
```

```python
# core/smarthome/adapters/base.py
"""Provider adapter interface.

An adapter knows ONE provider (Home Assistant, Tuya, MQTT, a camera vendor…).
It only translates provider state/commands; registry, authorization, audit and
World-Model sync live above it.
"""
from __future__ import annotations


class AdapterError(Exception):
    """Provider-level failure (mapped to HTTP 502/400 by the route layer)."""


class ProviderAdapter:
    name: str = "base"

    def identify(self) -> list[dict]:
        """Return provider devices as dicts with at least ``provider_id``."""
        raise NotImplementedError

    def get_state(self, provider_id: str) -> dict:
        """Return the current state; never invent fields."""
        raise NotImplementedError

    def set_state(self, provider_id: str, changes: dict) -> dict:
        """Apply changes and return the observed state (read-back)."""
        raise NotImplementedError
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /opt/ai-orchestrator && .venv/bin/python -m pytest tests/smarthome/test_adapter_base.py -q`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add core/smarthome/adapters/__init__.py core/smarthome/adapters/base.py tests/smarthome/test_adapter_base.py
git commit -m "feat(smarthome): provider adapter interface"
```

---

### Task 6: Home Assistant adapter (token from Vault)

**Files:**
- Create: `core/smarthome/adapters/homeassistant.py`
- Test: `tests/smarthome/test_adapter_ha.py`

- [ ] **Step 1: Write the failing test**

```python
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core.smarthome.adapters.homeassistant import HomeAssistantAdapter


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path == "/api/states":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps([
                {"entity_id": "light.kitchen", "state": "on",
                 "attributes": {"friendly_name": "Kitchen light", "brightness": 120}},
            ]).encode())
        else:
            self.send_response(404)
            self.end_headers()


def _server():
    srv = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def test_identify_and_state():
    srv = _server()
    host, port = srv.server_address
    a = HomeAssistantAdapter(base_url=f"http://{host}:{port}", token="t",
                             session=None)
    ids = a.identify()
    assert ids[0]["provider_id"] == "light.kitchen"
    assert ids[0]["name"] == "Kitchen light"
    st = a.get_state("light.kitchen")
    assert st["state"] == "on" and st["brightness"] == 120
    srv.shutdown()


def test_missing_token_raises():
    try:
        HomeAssistantAdapter(base_url="http://127.0.0.1:1", token="", session=None).identify()
        assert False
    except Exception as e:
        assert "token" in str(e).lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /opt/ai-orchestrator && .venv/bin/python -m pytest tests/smarthome/test_adapter_ha.py -q`
Expected: FAIL — no module `core.smarthome.adapters.homeassistant`

- [ ] **Step 3: Write minimal implementation**

```python
# core/smarthome/adapters/homeassistant.py
"""Home Assistant adapter — KAI drives HA (HA is a fabric, not the authority).

The long-lived token is read from KAI Vault, never from env or the frontend.
``session`` may be injected for tests; production passes ``requests.Session``.
State is returned verbatim from HA — fields HA does not report are absent, and
the caller renders them UNKNOWN.
"""
from __future__ import annotations

import logging

from core.smarthome.adapters.base import AdapterError, ProviderAdapter

logger = logging.getLogger(__name__)


def _vault_token() -> str:
    try:
        from core.vault import get_secret  # type: ignore
        return get_secret("homeassistant", "token") or ""
    except Exception:  # noqa: BLE001
        return ""


class HomeAssistantAdapter(ProviderAdapter):
    name = "homeassistant"

    def __init__(self, base_url: str, token: str | None = None, session=None):
        self.base_url = base_url.rstrip("/")
        self.token = token if token is not None else _vault_token()
        if session is None:
            import requests
            session = requests.Session()
        self.session = session

    def _headers(self) -> dict:
        if not self.token:
            raise AdapterError("Home Assistant token is not configured")
        return {"Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json"}

    def _get(self, path: str):
        r = self.session.get(self.base_url + path, headers=self._headers(), timeout=10)
        if r.status_code != 200:
            raise AdapterError(f"HA GET {path} -> {r.status_code}")
        return r.json()

    def identify(self) -> list[dict]:
        out = []
        for s in self._get("/api/states"):
            eid = s.get("entity_id", "")
            dom = eid.split(".", 1)[0]
            if dom in ("light", "switch", "sensor", "climate", "lock", "cover",
                       "camera", "media_player", "binary_sensor", "button"):
                out.append({"provider_id": eid,
                            "name": s.get("attributes", {}).get("friendly_name", eid),
                            "kind": dom})
        return out

    def get_state(self, provider_id: str) -> dict:
        s = self._get(f"/api/states/{provider_id}")
        st = {"state": s.get("state")}
        for k, v in s.get("attributes", {}).items():
            if k in ("brightness", "color_temp", "temperature", "current_temperature",
                     "battery_level", "unit_of_measurement", "friendly_name"):
                st[k] = v
        return st

    def set_state(self, provider_id: str, changes: dict) -> dict:
        domain = provider_id.split(".", 1)[0]
        body = {"entity_id": provider_id, **changes}
        r = self.session.post(f"{self.base_url}/api/services/{domain}/turn_on"
                              if changes.get("on") else
                              f"{self.base_url}/api/services/{domain}/turn_off",
                              headers=self._headers(), json=body, timeout=10)
        if r.status_code >= 400:
            raise AdapterError(f"HA control {provider_id} -> {r.status_code}")
        return self.get_state(provider_id)  # read-back
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /opt/ai-orchestrator && .venv/bin/python -m pytest tests/smarthome/test_adapter_ha.py -q`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add core/smarthome/adapters/homeassistant.py tests/smarthome/test_adapter_ha.py
git commit -m "feat(smarthome): Home Assistant fabric adapter (Vault token)"
```

---

### Task 7: Discovery CLI (the "discover all smart devices" deliverable)

**Files:**
- Create: `scripts/smarthome_discover.py`
- Test: `tests/smarthome/test_discover_cli.py`

- [ ] **Step 1: Write the failing test**

```python
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core.smarthome import registry as R
from scripts.smarthome_discover import ingest_candidates
from core.smarthome.discovery import Candidate


def test_ingest_writes_registry(monkeypatch, tmp_path):
    monkeypatch.setattr(R, "_default_memory_dir", lambda: tmp_path)
    cands = [
        Candidate(ip="192.168.1.16", mac="50:8a:06:6a:3c:36", provider="tuya",
                  provider_id="bf123", vendor="Tuya Smart", ports=[6668]),
        Candidate(ip="192.168.1.90", provider="homeassistant", ports=[8123]),
    ]
    report = ingest_candidates(cands)
    assert report["created"] == 2
    names = {d.provider for d in R.list_devices()}
    assert names == {"tuya", "homeassistant"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /opt/ai-orchestrator && .venv/bin/python -m pytest tests/smarthome/test_discover_cli.py -q`
Expected: FAIL — no module `scripts.smarthome_discover`

- [ ] **Step 3: Write minimal implementation**

```python
# scripts/smarthome_discover.py
"""One-shot smart-home discovery: LAN -> candidates -> canonical registry.

Passive/active scanning is best-effort and injectable; ``ingest_candidates`` is
pure and unit-tested. Run:  .venv/bin/python scripts/smarthome_discover.py
"""
from __future__ import annotations

import json
import socket
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.smarthome import registry as R  # noqa: E402
from core.smarthome.discovery import Candidate, classify_ports, parse_tuya  # noqa: E402
from core.smarthome.models import DeviceCreate, DeviceKind  # noqa: E402

_KIND_FOR_PROVIDER = {"tuya": DeviceKind.SWITCH, "homeassistant": DeviceKind.UNKNOWN,
                      "mqtt": DeviceKind.UNKNOWN, "camera": DeviceKind.CAMERA}


def ingest_candidates(cands: list[Candidate]) -> dict:
    created = updated = 0
    for c in cands:
        if not c.provider:
            continue
        pid = c.provider_id or c.ip
        before = R.get  # noqa: F841  (kept explicit for clarity)
        rec = R.upsert(DeviceCreate(
            name=c.provider_id or f"{c.vendor or c.provider} {c.ip}",
            provider=c.provider, provider_id=pid,
            kind=_KIND_FOR_PROVIDER.get(c.provider, DeviceKind.UNKNOWN),
            address=c.ip, mac=c.mac, vendor=c.vendor))
        if rec.created_at == rec.updated_at:
            created += 1
        else:
            updated += 1
    return {"created": created, "updated": updated,
            "total": len(R.list_devices())}


def scan_ports(ip: str, ports: tuple[int, ...] = (8123, 6668, 6667, 1883, 554)) -> set[int]:
    open_ports = set()
    for p in ports:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(0.4)
        try:
            if s.connect_ex((ip, p)) == 0:
                open_ports.add(p)
        finally:
            s.close()
    return open_ports


def main() -> int:
    # Placeholder LAN walk; replaced by passive capture + ARP in the execute step.
    targets = sys.argv[1:] or ["192.168.1.16"]
    cands: list[Candidate] = []
    for ip in targets:
        ports = scan_ports(ip)
        provider, vendor = classify_ports(ports)
        cands.append(Candidate(ip=ip, provider=provider, vendor=vendor, ports=sorted(ports)))
    report = ingest_candidates(cands)
    print(json.dumps({"candidates": [c.__dict__ for c in cands], "report": report}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /opt/ai-orchestrator && .venv/bin/python -m pytest tests/smarthome/test_discover_cli.py -q`
Expected: PASS (1 passed)

- [ ] **Step 5: Commit**

```bash
git add scripts/smarthome_discover.py tests/smarthome/test_discover_cli.py
git commit -m "feat(smarthome): discovery CLI + registry ingest"
```

---

### Task 8: Home Assistant fabric deployment (LXC 115)

**Files:**
- Create: `docs/smarthome/HA_DEPLOY.md` (deployment record)
- No repo code change (infrastructure task)

- [ ] **Step 1: Create LXC 115 on Proxmox B**

Run (on PVE-B):
```bash
pct create 115 local:vztmpl/debian-12-standard_*.tar.zst \
  --hostname kai-homeassistant --cores 2 --memory 2048 --swap 1024 \
  --rootfs local-lvm:8 --net0 name=eth0,bridge=vmbr0,ip=192.168.1.115/24,gw=192.168.1.1 \
  --features nesting=1 --onboot 1 --startup order=6,up=10 --unprivileged 1
pct start 115
```
Expected: `pct status 115` → `status: running`.

- [ ] **Step 2: Install Docker + Home Assistant Container**

Run (in CT 115):
```bash
apt-get update && apt-get -y install ca-certificates curl
curl -fsSL https://get.docker.com | sh
mkdir -p /opt/homeassistant/config
docker run -d --name homeassistant --restart=unless-stopped --privileged \
  --network=host -v /opt/homeassistant/config:/config \
  -e TZ=UTC ghcr.io/home-assistant/home-assistant:stable
```
Expected: `docker ps` shows `homeassistant` up; `http://192.168.1.115:8123` serves HA onboarding.

- [ ] **Step 3: Create a long-lived token and store it in Vault**

Run: complete HA onboarding in a browser, create a long-lived access token, then:
```bash
# store via the KAI vault CLI/API used elsewhere in this repo
python -c "from core.vault import set_secret; set_secret('homeassistant','token','<TOKEN>')"
```
Expected: `python -c "from core.vault import get_secret; print(bool(get_secret('homeassistant','token')))"` → `True`. **Never** write the token to a file or commit it.

- [ ] **Step 4: Verify KAI reaches HA (adapter smoke)**

Run (on CT111):
```bash
cd /opt/ai-orchestrator && .venv/bin/python -c "
from core.smarthome.adapters.homeassistant import HomeAssistantAdapter as A
a=A('http://192.168.1.115:8123')
print(len(a.identify()), 'HA entities')
"
```
Expected: prints an entity count (may be 0 before devices are added) with no exception.

- [ ] **Step 5: Commit the deployment record**

```bash
git add docs/smarthome/HA_DEPLOY.md
git commit -m "docs(smarthome): Home Assistant fabric deployment (LXC 115)"
```

---

### Task 9: Live discovery proof + inventory report

**Files:**
- Create: `docs/smarthome/DISCOVERY_REPORT.md`

- [ ] **Step 1: Run discovery against the known estate**

Run (on CT111):
```bash
cd /opt/ai-orchestrator && .venv/bin/python scripts/smarthome_discover.py 192.168.1.16
```
Expected: JSON report with the Tuya candidate (`provider: tuya`) and `created >= 1`.

- [ ] **Step 2: Extend to a LAN sweep and capture the full inventory**

Run (on CT111, after the passive capture is wired in the execute phase):
```bash
.venv/bin/python scripts/smarthome_discover.py $(seq -f "192.168.1.%g" 1 254 | tr '\n' ' ')
```
Expected: every responsive smart device (Tuya today; HA/MQTT/cameras as present) appears in the registry.

- [ ] **Step 3: Write the inventory report**

Create `docs/smarthome/DISCOVERY_REPORT.md` listing each discovered device with
`ip | mac | vendor | provider | kind | state-freshness`, explicitly marking any
unknown vendor/state as `UNKNOWN` (never guessed).

- [ ] **Step 4: Verify the registry is authoritative and non-duplicated**

Run:
```bash
.venv/bin/python -m pytest tests/smarthome -q && \
.venv/bin/python scripts/cc_contract_check.py --no-smoke
```
Expected: all smart-home tests PASS; contract OK.

- [ ] **Step 5: Commit**

```bash
git add docs/smarthome/DISCOVERY_REPORT.md
git commit -m "docs(smarthome): live discovery inventory"
```

---

## Self-Review

**Spec coverage:** registry (§3.1 → Task 2), discovery 3-layer (§3.2 → Tasks 3–4, 7, 9), adapters incl. HA (§3.3, decision → Tasks 5–6, 8), canonical-not-mobile registry (§1/§7 → Tasks 1–2), no-fabrication (§5 → Tasks 1, 4, 6), testing (§6 → every task), scope/HA-fabric (§7 → Task 8). Rooms/service/routes/CC panel are **Phase E2** by design (separate plan after E1 ships).

**Placeholder scan:** the LAN walk in Task 7's `main()` is explicitly labeled a placeholder replaced during execution, with a concrete command in Task 9 Step 2 — acceptable because the tested pure function (`ingest_candidates`) is complete.

**Type consistency:** `DeviceCreate`/`DeviceRecord.new`/`registry.upsert/get/list_devices/set_state/delete`/`Candidate.to_dedup_key`/`ProviderAdapter.{identify,get_state,set_state}`/`AdapterError` are named identically across tasks.
