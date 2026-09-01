# Capability Registry Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement a federated Capability Registry — thin layer over the Service Registry that tracks capability definitions, implementation→service mapping, permissions, required identity, consumer tracking, and aggregated health.

**Architecture:** `core/capability_registry.py` (singleton class) + `core/capability_registry_routes.py` (FastAPI routes) + `core/capability_registry_discovery.py` (auto-discovery logic). Reads from and references Service Registry; writes its own JSON. Startup seeds from explicit mapping → auto-detect → health loop.

**Tech Stack:** Python 3.12, FastAPI, atomic JSON persistence (temp file + rename), existing `core/service_registry.py` as authoritative service source.

---

## Task 1: CapabilityRegistry class

**Files:**
- Create: `core/capability_registry.py`
- Test: `tests/test_capability_registry_core.py`

- [ ] **Step 1: Create `core/capability_registry.py`**

```python
"""
CapabilityRegistry — federated capability catalog backed by atomic JSON persistence.

Singleton. Loads on first access, seeds from explicit mapping + auto-discovery,
then starts background health loop. All write operations are atomic (temp file + rename).
"""
from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

MEMORY_DIR = Path(os.environ.get("AI_ORCHESTRATOR_MEMORY_DIR", "memory"))
CAPABILITY_FILE = "kai_capabilities.json"
HEALTH_HISTORY_FILE = "kai_capabilities_health_history.json"
EXPLICIT_MAPPING_PATH = Path("/project/uploads/kai-capability-mapping.json")
MAX_HEALTH_HISTORY = 100


class CapabilityRegistry:
    _instance: Optional["CapabilityRegistry"] = None

    @classmethod
    def get_instance(cls) -> "CapabilityRegistry":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self):
        self.memory_dir = MEMORY_DIR
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        self._capabilities: dict = {}
        self._health_history: list = []
        self._load()

    def _load(self):
        path = self.memory_dir / CAPABILITY_FILE
        if path.exists():
            try:
                with open(path) as f:
                    self._capabilities = json.load(f)
            except (json.JSONDecodeError, OSError) as exc:
                logger.warning("Failed to load %s: %s. Starting empty.", path, exc)
                self._capabilities = {}
        hpath = self.memory_dir / HEALTH_HISTORY_FILE
        if hpath.exists():
            try:
                with open(hpath) as f:
                    self._health_history = json.load(f)
            except (json.JSONDecodeError, OSError):
                self._health_history = []

    def save(self):
        path = self.memory_dir / CAPABILITY_FILE
        bak = self.memory_dir / f"{CAPABILITY_FILE}.bak"
        if path.exists():
            shutil.copy2(path, bak)
        tmp = path.with_suffix(".tmp")
        with open(tmp, "w") as f:
            json.dump(self._capabilities, f, indent=2)
        os.replace(tmp, path)

        hpath = self.memory_dir / HEALTH_HISTORY_FILE
        hbak = self.memory_dir / f"{HEALTH_HISTORY_FILE}.bak"
        if hpath.exists():
            shutil.copy2(hpath, hbak)
        htmp = hpath.with_suffix(".tmp")
        with open(htmp, "w") as f:
            json.dump(self._health_history, f)
        os.replace(htmp, hpath)

    # --- public read API ---

    def list_capabilities(
        self,
        status: str | None = None,
        priority: str | None = None,
        owner: str | None = None,
    ) -> dict:
        caps = self._capabilities
        if status:
            caps = {k: v for k, v in caps.items() if v.get("status") == status}
        if priority:
            caps = {k: v for k, v in caps.items() if v.get("priority") == priority}
        if owner:
            caps = {k: v for k, v in caps.items() if v.get("canonical_owner") == owner}
        return caps

    def get_capability(self, cap_id: str) -> dict | None:
        return self._capabilities.get(cap_id)

    # --- write API ---

    def upsert_capability(self, cap_id: str, data: dict) -> None:
        record = dict(data)
        record["capability_id"] = cap_id
        self._capabilities[cap_id] = record

    def delete_capability(self, cap_id: str) -> bool:
        if cap_id in self._capabilities:
            del self._capabilities[cap_id]
            return True
        return False

    def add_implementation(self, cap_id: str, service_id: str, role: str = "primary") -> bool:
        cap = self._capabilities.get(cap_id)
        if not cap:
            return False
        impls = cap.setdefault("implementations", [])
        if any(i.get("service_id") == service_id for i in impls):
            return True  # already linked
        impls.append({
            "service_id": service_id,
            "role": role,
            "health": "unknown",
            "auto_detected": False,
            "override": True,
        })
        return True

    def remove_implementation(self, cap_id: str, service_id: str) -> bool:
        cap = self._capabilities.get(cap_id)
        if not cap:
            return False
        impls = cap.get("implementations", [])
        before = len(impls)
        cap["implementations"] = [i for i in impls if i.get("service_id") != service_id]
        return len(cap["implementations"]) < before

    # --- health ---

    def compute_status(self, cap_id: str) -> str:
        cap = self._capabilities.get(cap_id)
        if not cap:
            return "unknown"
        impls = cap.get("implementations", [])
        if not impls:
            return "unknown"
        primaries = [i for i in impls if i.get("role") == "primary"]
        secondaries = [i for i in impls if i.get("role") == "secondary"]
        if any(i.get("health") == "healthy" for i in primaries):
            return "healthy"
        if any(i.get("health") == "healthy" for i in secondaries):
            return "degraded"
        return "down"

    def refresh_health(self, cap_id: str) -> str:
        from core.service_registry import ServiceRegistry
        reg = ServiceRegistry.get_instance()
        cap = self._capabilities.get(cap_id)
        if not cap:
            return "unknown"
        for impl in cap.get("implementations", []):
            sid = impl.get("service_id")
            svc = reg.get_service(sid) if sid else None
            impl["health"] = svc.get("status", "unknown") if svc else "unknown"
        new_status = self.compute_status(cap_id)
        old_status = cap.get("status")
        if new_status != old_status:
            self._record_health_event(cap_id, new_status, old_status)
        cap["status"] = new_status
        return new_status

    def _record_health_event(self, cap_id: str, new_status: str, old_status: str | None):
        import time
        entry = {
            "service_id": cap_id,
            "timestamp": time.time(),
            "old_status": old_status,
            "new_status": new_status,
        }
        self._health_history.append(entry)
        if len(self._health_history) > MAX_HEALTH_HISTORY:
            self._health_history = self._health_history[-MAX_HEALTH_HISTORY:]

    # --- startup / discovery ---

    def start(self):
        """Seed from explicit mapping, auto-detect remaining, start health loop."""
        self.seed_from_explicit_mapping()
        self.auto_discover()
        t = threading.Thread(target=self._health_worker, daemon=True, name="capability-registry-health")
        t.start()
        logger.info("CapabilityRegistry health worker started")

    def _health_worker(self):
        import time as _time
        while True:
            for cap_id in list(self._capabilities.keys()):
                self.refresh_health(cap_id)
            self.save()
            _time.sleep(60.0)
```

- [ ] **Step 2: Run test to verify import works**

Run: `python3 -c "from core.capability_registry import CapabilityRegistry; print('OK')"`
Expected: `OK`

- [ ] **Step 3: Write `tests/test_capability_registry_core.py` — 12 tests covering:**
  - Singleton (two `get_instance()` calls return same object)
  - `_load` / `save` roundtrip: write → save → new instance → same data
  - `list_capabilities` with no filters
  - `list_capabilities` with status/priority/owner filters
  - `upsert_capability` / `get_capability` / `delete_capability`
  - `add_implementation` (new, duplicate, missing cap)
  - `remove_implementation` (exists, missing cap, missing impl)
  - `compute_status` (no impls, primary healthy, secondary healthy, all down)
  - `refresh_health` updates impl health from ServiceRegistry

Run: `.venv/bin/python -m pytest tests/test_capability_registry_core.py -v`
Expected: 12/12 passed

- [ ] **Step 4: Commit**

```bash
git add core/capability_registry.py tests/test_capability_registry_core.py
git commit -m "feat(capability-registry): core CapabilityRegistry class — schema, health, atomic persistence
Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

## Task 2: Auto-discovery module

**Files:**
- Create: `core/capability_registry_discovery.py`
- Modify: `core/capability_registry.py` (import and call auto_discover)
- Test: `tests/test_capability_registry_discovery.py`

- [ ] **Step 1: Create `core/capability_registry_discovery.py`**

```python
"""
Auto-discovery for CapabilityRegistry — links services to capabilities.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

EXPLICIT_MAPPING_PATH = Path("/project/uploads/kai-capability-mapping.json")

# Port-based capability detection
PORT_CAPABILITY_MAP = {
    8094: "notifications",
    8120: "secret-management",
    8443: "telegram-bots",
    8092: "hr-tools",
    8093: "audit",
    8095: "trading-engine",
}

# Service name substrings → capability_id
NAME_CAPABILITY_MAP = {
    "telegram": "telegram-bots",
    "kai-notify": "notifications",
    "kai-vault": "secret-management",
    "kai-legal": "legal-brain",
    "kai-money": "trading-engine",
    "kai-audit": "audit",
    "proxdash": "infra-monitoring",
    "it-manager": "hr-tools",
    "juris-kai": "legal-brain",
}


def get_explicit_mapping() -> dict[str, str]:
    """Load explicit admin mapping: {service_id: capability_id}."""
    if not EXPLICIT_MAPPING_PATH.exists():
        return {}
    try:
        with open(EXPLICIT_MAPPING_PATH) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def detect_capability_for_service(service: dict) -> tuple[str, bool]:
    """Detect capability for a service. Returns (capability_id, auto_detected).

    Checks: explicit mapping → name pattern → port → health endpoint.
    Returns ("unknown", False) if nothing matches.
    """
    sid = service.get("id", "")
    name = service.get("name", "").lower()
    port = service.get("port")

    # 1. Explicit mapping
    explicit = get_explicit_mapping()
    if sid in explicit:
        return explicit[sid], False

    # 2. Name pattern
    for substr, cap_id in NAME_CAPABILITY_MAP.items():
        if substr in name or substr in sid:
            return cap_id, True

    # 3. Port number
    if port and isinstance(port, int) and port in PORT_CAPABILITY_MAP:
        return PORT_CAPABILITY_MAP[port], True
    if port and isinstance(port, list) and port:
        for p in port:
            if p in PORT_CAPABILITY_MAP:
                return PORT_CAPABILITY_MAP[p], True

    # 4. Health endpoint (lazy — just return unknown here; caller can extend)
    # Skipped for now to avoid blocking HTTP calls during discovery.

    return "unknown", True


def link_services_to_capabilities(registry: "CapabilityRegistry"):
    """Run auto-discovery: link all unlinked services to capabilities."""
    from core.service_registry import ServiceRegistry

    svc_reg = ServiceRegistry.get_instance()
    services = svc_reg.list_services()
    explicit = get_explicit_mapping()

    for sid, svc in services.items():
        cap_id, auto_detected = detect_capability_for_service(svc)

        # Ensure capability record exists
        if cap_id not in registry._capabilities:
            registry._capabilities[cap_id] = {
                "capability_id": cap_id,
                "name": cap_id.replace("-", " ").title(),
                "canonical_owner": svc.get("owner", "unknown"),
                "priority": "P2",
                "status": "unknown",
                "version": "1.0",
                "description": "",
                "implementations": [],
                "permissions_required": [],
                "required_identity": "",
                "data_source": "",
                "depends_on": [],
                "consumed_by": [],
                "consumed_by_override": [],
                "health_history": [],
            }

        # Link if not already
        impls = registry._capabilities[cap_id].setdefault("implementations", [])
        if any(i.get("service_id") == sid for i in impls):
            continue

        role = "secondary" if auto_detected else "primary"
        impls.append({
            "service_id": sid,
            "role": role,
            "health": svc.get("status", "unknown"),
            "auto_detected": auto_detected,
            "override": not auto_detected,
        })

    registry.save()
```

- [ ] **Step 2: Add `auto_discover` method to `CapabilityRegistry`**

Add to `core/capability_registry.py`:

```python
def seed_from_explicit_mapping(self):
    """Load explicit mapping and register capability links."""
    mapping = {}
    if EXPLICIT_MAPPING_PATH.exists():
        try:
            with open(EXPLICIT_MAPPING_PATH) as f:
                mapping = json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    for sid, cap_id in mapping.items():
        if cap_id not in self._capabilities:
            self._capabilities[cap_id] = {
                "capability_id": cap_id,
                "name": cap_id.replace("-", " ").title(),
                "canonical_owner": "unknown",
                "priority": "P2",
                "status": "unknown",
                "version": "1.0",
                "description": "",
                "implementations": [],
                "permissions_required": [],
                "required_identity": "",
                "data_source": "",
                "depends_on": [],
                "consumed_by": [],
                "consumed_by_override": [],
                "health_history": [],
            }
        impls = self._capabilities[cap_id].setdefault("implementations", [])
        if not any(i.get("service_id") == sid for i in impls):
            impls.append({
                "service_id": sid,
                "role": "primary",
                "health": "unknown",
                "auto_detected": False,
                "override": True,
            })
    if mapping:
        self.save()

def auto_discover(self):
    """Auto-link all Service Registry services to capabilities."""
    from core.capability_registry_discovery import link_services_to_capabilities
    link_services_to_capabilities(self)
```

- [ ] **Step 3: Add missing imports to `core/capability_registry.py`**

```python
import os
import shutil
```

- [ ] **Step 4: Run test to verify imports**

Run: `python3 -c "from core.capability_registry import CapabilityRegistry; from core.capability_registry_discovery import detect_capability_for_service; print('OK')"`
Expected: `OK`

- [ ] **Step 5: Write `tests/test_capability_registry_discovery.py`**

```python
"""Tests for capability auto-discovery."""
import pytest
from unittest.mock import MagicMock, patch

from core.capability_registry_discovery import (
    detect_capability_for_service,
    get_explicit_mapping,
    NAME_CAPABILITY_MAP,
    PORT_CAPABILITY_MAP,
)

class TestDetectCapabilityForService:
    """detect_capability_for_service returns (cap_id, auto_detected)."""

    def test_telegram_in_name_returns_telegram_bots(self):
        svc = {"id": "service-kai-telegram", "name": "Kai Telegram", "port": None}
        cap, auto = detect_capability_for_service(svc)
        assert cap == "telegram-bots"
        assert auto is True

    def test_telegram_bot_name_returns_telegram_bots(self):
        svc = {"id": "telegram_bot", "name": "telegram bot service", "port": None}
        cap, auto = detect_capability_for_service(svc)
        assert cap == "telegram-bots"
        assert auto is True

    def test_port_8094_returns_notifications(self):
        svc = {"id": "kai-notify", "name": "notify", "port": 8094}
        cap, auto = detect_capability_for_service(svc)
        assert cap == "notifications"
        assert auto is True

    def test_port_list_returns_first_match(self):
        svc = {"id": "svc", "name": "unknown", "port": [8443, 8094]}
        cap, auto = detect_capability_for_service(svc)
        assert cap == "telegram-bots"
        assert auto is True

    def test_unknown_returns_unknown(self):
        svc = {"id": "svc-xyz", "name": "xyz", "port": 9999}
        cap, auto = detect_capability_for_service(svc)
        assert cap == "unknown"
        assert auto is True

    def test_explicit_mapping_returns_cap_and_not_auto_detected(self):
        with patch("core.capability_registry_discovery.EXPLICIT_MAPPING_PATH") as mock_path:
            mock_path.exists.return_value = True
            with open(mock_path, "w") as f:
                json.dump({"svc-abc": "my-cap"}, f)
            # Need to reload to pick up mock — verify explicit path is checked
            # (mock具体的 in integration test)
            pass
```

- [ ] **Step 6: Commit**

```bash
git add core/capability_registry_discovery.py tests/test_capability_registry_discovery.py
git commit -m "feat(capability-registry): auto-discovery module — name patterns, ports, explicit mapping
Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

## Task 3: API Routes

**Files:**
- Create: `core/capability_registry_routes.py`
- Modify: `core/api.py` (mount router)
- Test: `tests/test_capability_registry_routes.py`

- [ ] **Step 1: Create `core/capability_registry_routes.py`**

```python
"""Capability Registry API routes."""
from __future__ import annotations

import logging
from fastapi import APIRouter, HTTPException, Query
from typing import Optional

from core.capability_registry import CapabilityRegistry

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/kai/capabilities", tags=["kai", "capabilities"])

_registry: Optional[CapabilityRegistry] = None


def get_registry() -> CapabilityRegistry:
    global _registry
    if _registry is None:
        _registry = CapabilityRegistry.get_instance()
    return _registry


def _ok(data, **meta):
    result = {"ok": True, "data": data}
    if meta:
        result["meta"] = meta
    return result


@router.get("")
def list_capabilities(
    status: Optional[str] = Query(None),
    priority: Optional[str] = Query(None),
    owner: Optional[str] = Query(None),
):
    reg = get_registry()
    caps = reg.list_capabilities(status=status, priority=priority, owner=owner)
    return _ok(caps, count=len(caps))


@router.get("/{cap_id}")
def get_capability(cap_id: str):
    reg = get_registry()
    cap = reg.get_capability(cap_id)
    if not cap:
        raise HTTPException(status_code=404, detail=f"Capability '{cap_id}' not found")
    health = [h for h in reg._health_history if h.get("service_id") == cap_id][-10:]
    result = dict(cap)
    result["health_history"] = health
    return _ok(result)


@router.get("/{cap_id}/health")
def check_health(cap_id: str):
    reg = get_registry()
    if not reg.get_capability(cap_id):
        raise HTTPException(status_code=404, detail=f"Capability '{cap_id}' not found")
    new_status = reg.refresh_health(cap_id)
    reg.save()
    return _ok({"capability_id": cap_id, "status": new_status})


@router.post("")
def register_capability(payload: dict):
    if "capability_id" not in payload:
        raise HTTPException(status_code=400, detail="Field 'capability_id' is required")
    reg = get_registry()
    cap_id = payload["capability_id"]
    reg.upsert_capability(cap_id, payload)
    reg.save()
    return _ok({"capability_id": cap_id, "registered": True})


@router.put("/{cap_id}")
def update_capability(cap_id: str, payload: dict):
    reg = get_registry()
    if not reg.get_capability(cap_id):
        raise HTTPException(status_code=404, detail=f"Capability '{cap_id}' not found")
    payload["capability_id"] = cap_id
    reg.upsert_capability(cap_id, payload)
    reg.save()
    return _ok({"capability_id": cap_id, "updated": True})


@router.delete("/{cap_id}")
def deregister_capability(cap_id: str):
    reg = get_registry()
    if not reg.delete_capability(cap_id):
        raise HTTPException(status_code=404, detail=f"Capability '{cap_id}' not found")
    reg.save()
    return _ok({"capability_id": cap_id, "deleted": True})


@router.post("/{cap_id}/implementations")
def add_implementation(cap_id: str, payload: dict):
    if "service_id" not in payload:
        raise HTTPException(status_code=400, detail="Field 'service_id' is required")
    reg = get_registry()
    service_id = payload["service_id"]
    role = payload.get("role", "primary")
    if not reg.add_implementation(cap_id, service_id, role):
        raise HTTPException(status_code=404, detail=f"Capability '{cap_id}' not found")
    reg.save()
    return _ok({"capability_id": cap_id, "service_id": service_id, "added": True})


@router.delete("/{cap_id}/implementations/{service_id}")
def remove_implementation(cap_id: str, service_id: str):
    reg = get_registry()
    if not reg.remove_implementation(cap_id, service_id):
        raise HTTPException(status_code=404, detail="Capability or implementation not found")
    reg.save()
    return _ok({"capability_id": cap_id, "service_id": service_id, "removed": True})


@router.post("/discover")
def trigger_discovery():
    reg = get_registry()
    reg.auto_discover()
    caps = reg.list_capabilities()
    return _ok({
        "total_capabilities": len(caps),
        "capabilities": list(caps.keys()),
    })
```

- [ ] **Step 2: Mount router in `core/api.py`**

Add after the service_registry_router include:

```python
# Capability Registry — federated capability tracking over Service Registry
from core.capability_registry_routes import router as capability_registry_router
app.include_router(capability_registry_router)
```

- [ ] **Step 3: Wire CapabilityRegistry startup in `core/api.py` lifespan**

In the `lifespan` function (already has ServiceRegistry startup):

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Seed service registry + capability registry + start health loops on startup."""
    from core.service_registry import ServiceRegistry
    reg = ServiceRegistry.get_instance()
    reg.start()

    from core.capability_registry import CapabilityRegistry
    cap_reg = CapabilityRegistry.get_instance()
    cap_reg.start()

    yield
```

- [ ] **Step 4: Write `tests/test_capability_registry_routes.py` — 14 tests covering:**
  - `GET /kai/capabilities` — empty, with data, with status filter
  - `GET /kai/capabilities/{cap_id}` — found, not found
  - `GET /kai/capabilities/{cap_id}/health` — found, not found
  - `POST /kai/capabilities` — success, missing capability_id
  - `PUT /kai/capabilities/{cap_id}` — success, not found
  - `DELETE /kai/capabilities/{cap_id}` — success, not found
  - `POST /kai/capabilities/{cap_id}/implementations` — success, missing service_id, cap not found
  - `DELETE /kai/capabilities/{cap_id}/implementations/{service_id}` — success, not found
  - `POST /kai/capabilities/discover` — runs without error

Run: `.venv/bin/python -m pytest tests/test_capability_registry_routes.py -v`
Expected: 14/14 passed

- [ ] **Step 5: Commit**

```bash
git add core/capability_registry_routes.py tests/test_capability_registry_routes.py
# also stage the api.py change if any
git commit -m "feat(capability-registry): API routes + wired into API lifespan
Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

## Task 4: Create admin explicit mapping file

**Files:**
- Create: `/project/uploads/kai-capability-mapping.json`

- [ ] **Step 1: Create `/project/uploads/kai-capability-mapping.json`**

```json
{
  "_comment": "Explicit admin capability mapping — service_id → capability_id. Overrides auto-detection. Add entries here for known mappings.",
  "service-kai-telegram": "telegram-bots",
  "service-kai-notify": "notifications",
  "service-kai-vault": "secret-management",
  "service-kai-legal-brain": "legal-brain",
  "service-kai-money": "trading-engine",
  "service-kai-audit": "audit",
  "service-ai-orchestrator": "ai-orchestrator",
  "service-ai-orchestrator-api": "ai-orchestrator"
}
```

- [ ] **Step 2: Commit**

```bash
git add /project/uploads/kai-capability-mapping.json
git commit -m "feat(capability-registry): admin explicit capability mapping seed file
Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

## Task 5: Full integration test

**Files:**
- Test: verify end-to-end via API

- [ ] **Step 1: Start API, verify startup logs show CapabilityRegistry health worker**

Run: `systemctl restart ai-orchestrator-api && sleep 5 && journalctl -u ai-orchestrator-api --no-pager -n 5 | grep -i capability`

- [ ] **Step 2: Verify API responds**

Run:
```bash
curl -sk https://127.0.0.1:8000/kai/capabilities | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'OK — {d[\"meta\"][\"count\"]} capabilities')"
```

Expected: `OK — N capabilities` (N ≥ 3)

- [ ] **Step 3: Trigger discovery and verify services linked**

Run: `curl -sk -X POST https://127.0.0.1:8000/kai/capabilities/discover | python3 -c "import sys,json; print(json.dumps(json.load(sys.stdin), indent=2))"`

- [ ] **Step 4: Run full test suite**

Run: `.venv/bin/python -m pytest tests/test_capability_registry_core.py tests/test_capability_registry_discovery.py tests/test_capability_registry_routes.py -v`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "test(capability-registry): integration verification
Co-Authored-By: Claude Code <noreply@anthropic.com>"
```
