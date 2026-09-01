"""
CapabilityRegistry — JSON-persisted capability catalog with atomic writes and health aggregation.

Provides a singleton registry for capability definitions that link to ServiceRegistry
services. All write operations are atomic (temp file + rename) with backup preservation.

Health aggregation logic:
  - PRIMARY healthy  → capability is "healthy"
  - no PRIMARY but SECONDARY healthy → capability is "degraded"
  - otherwise → "down"
"""
import copy
import json
import logging
import os
import shutil
import threading
import time
from pathlib import Path
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

MEMORY_DIR = Path(os.environ.get("AI_ORCHESTRATOR_MEMORY_DIR", "memory"))

CAPABILITY_FILE = "kai_capabilities.json"
HEALTH_HISTORY_FILE = "kai_capabilities_health_history.json"
MAPPING_FILE = Path("/project/uploads/kai-capability-mapping.json")
MAX_HEALTH_HISTORY = 100

# Schema field defaults for new capability records
_CAPABILITY_DEFAULTS = {
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


class CapabilityRegistry:
    _instance: Optional["CapabilityRegistry"] = None

    @classmethod
    def get_instance(cls, memory_dir: Path = None) -> "CapabilityRegistry":
        """Return the singleton instance, creating it if necessary."""
        if cls._instance is None:
            cls._instance = cls(memory_dir=memory_dir)
        return cls._instance

    def __init__(self, memory_dir: Path = None):
        self.memory_dir = Path(memory_dir) if memory_dir is not None else MEMORY_DIR
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        self._capabilities: dict = {}
        self._health_history: list = []
        # NOTE: do NOT set _instance here — that is get_instance()'s job only.
        # This allows direct CapabilityRegistry() calls in tests to create
        # truly independent instances (same pattern as ServiceRegistry).
        self._load()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _load(self):
        path = self.memory_dir / CAPABILITY_FILE
        if path.exists():
            try:
                with open(path) as f:
                    self._capabilities = json.load(f)
            except (json.JSONDecodeError, OSError) as exc:
                logger.warning(
                    "Failed to load capabilities file %s: %s. Initialising empty.",
                    path, exc,
                )
                self._capabilities = {}
        else:
            self._capabilities = {}

        hpath = self.memory_dir / HEALTH_HISTORY_FILE
        if hpath.exists():
            try:
                with open(hpath) as f:
                    self._health_history = json.load(f)
            except (json.JSONDecodeError, OSError) as exc:
                logger.warning(
                    "Failed to load capability health history %s: %s. Initialising empty.",
                    hpath, exc,
                )
                self._health_history = []
        else:
            self._health_history = []

    def save(self):
        """Atomic write: temp file + os.replace; also saves health history."""
        path = self.memory_dir / CAPABILITY_FILE
        bak = self.memory_dir / f"{CAPABILITY_FILE}.bak"
        if path.exists():
            shutil.copy(path, bak)
        tmp = self.memory_dir / f"{CAPABILITY_FILE}.tmp"
        with open(tmp, "w") as f:
            json.dump(self._capabilities, f, indent=2)
        os.replace(tmp, path)

        hpath = self.memory_dir / HEALTH_HISTORY_FILE
        hbak = self.memory_dir / f"{HEALTH_HISTORY_FILE}.bak"
        if hpath.exists():
            shutil.copy(hpath, hbak)
        htmp = self.memory_dir / f"{HEALTH_HISTORY_FILE}.tmp"
        with open(htmp, "w") as f:
            json.dump(self._health_history, f, indent=2)
        os.replace(htmp, hpath)

    # ------------------------------------------------------------------
    # Core CRUD
    # ------------------------------------------------------------------

    def list_capabilities(
        self,
        status: str = None,
        priority: str = None,
        owner: str = None,
    ) -> dict:
        """Return all capabilities, optionally filtered by status/priority/owner."""
        result = {}
        for cap_id, cap in self._capabilities.items():
            if status is not None and cap.get("status") != status:
                continue
            if priority is not None and cap.get("priority") != priority:
                continue
            if owner is not None and cap.get("canonical_owner") != owner:
                continue
            result[cap_id] = cap
        return result

    def get_capability(self, cap_id: str) -> Optional[dict]:
        return self._capabilities.get(cap_id)

    def upsert_capability(self, cap_id: str, data: dict):
        """Create or update a capability record, ensuring the capability_id field."""
        record = copy.deepcopy(_CAPABILITY_DEFAULTS)
        if cap_id in self._capabilities:
            existing = self._capabilities[cap_id]
            record.update(existing)
        record.update(data)
        record["capability_id"] = cap_id
        self._capabilities[cap_id] = record

    def delete_capability(self, cap_id: str) -> bool:
        """Remove a capability. Returns True if it existed."""
        if cap_id in self._capabilities:
            del self._capabilities[cap_id]
            return True
        return False

    # ------------------------------------------------------------------
    # Implementation links
    # ------------------------------------------------------------------

    def add_implementation(
        self,
        cap_id: str,
        service_id: str,
        role: str = "primary",
    ) -> bool:
        """Add a service implementation to a capability.

        Returns False if the capability does not exist or the role is invalid.
        Silently skips if the service_id is already linked.
        """
        if role not in ("primary", "secondary"):
            return False
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
            "override": False,
        })
        return True

    def remove_implementation(self, cap_id: str, service_id: str) -> bool:
        """Remove a service implementation from a capability. Returns True if found."""
        cap = self._capabilities.get(cap_id)
        if not cap:
            return False
        impls = cap.get("implementations", [])
        for i, impl in enumerate(impls):
            if impl.get("service_id") == service_id:
                impls.pop(i)
                return True
        return False

    # ------------------------------------------------------------------
    # Health aggregation
    # ------------------------------------------------------------------

    def compute_status(self, cap_id: str) -> str:
        """Aggregate implementation health into a capability status.

        Rules:
          - any PRIMARY with health=="healthy" → "healthy"
          - any SECONDARY with health=="healthy" (and no healthy PRIMARY) → "degraded"
          - else → "down"
        """
        cap = self._capabilities.get(cap_id)
        if not cap:
            return "unknown"
        impls = cap.get("implementations", [])
        if not impls:
            return "unknown"

        has_primary_healthy = any(
            i.get("role") == "primary" and i.get("health") == "healthy"
            for i in impls
        )
        if has_primary_healthy:
            return "healthy"

        has_secondary_healthy = any(
            i.get("role") == "secondary" and i.get("health") == "healthy"
            for i in impls
        )
        if has_secondary_healthy:
            return "degraded"

        return "down"

    def _record_health_event(self, cap_id: str, new_status: str, old_status: str):
        """Append a health-change event to the health history (max MAX_HEALTH_HISTORY)."""
        if new_status == old_status:
            return
        entry = {
            "capability_id": cap_id,
            "timestamp": time.time(),
            "old_status": old_status,
            "new_status": new_status,
        }
        self._health_history.append(entry)
        # Trim oldest events beyond the cap
        if len(self._health_history) > MAX_HEALTH_HISTORY:
            self._health_history = self._health_history[-MAX_HEALTH_HISTORY:]

    def refresh_health(self, cap_id: str):
        """For each implementation, look up the service status and recompute capability status.

        Records a health-change event if the status transitions.
        """
        cap = self._capabilities.get(cap_id)
        if not cap:
            return

        # Import here so the module is not a hard dependency at import time
        from core.service_registry import ServiceRegistry

        sr = ServiceRegistry.get_instance()
        old_status = cap.get("status", "unknown")

        for impl in cap.get("implementations", []):
            svc = sr.get_service(impl["service_id"])
            if svc:
                impl["health"] = svc.get("status", "unknown")
            else:
                impl["health"] = "unknown"

        new_status = self.compute_status(cap_id)
        cap["status"] = new_status

        self._record_health_event(cap_id, new_status, old_status)

        # Publish event bus notification when status changes
        if old_status != new_status:
            from core.kai_event_bus import event_bus, IMPORTANT
            severity = "critical" if new_status == "down" else "important"
            event_bus.publish(
                f"capability.health.{cap_id}",
                {
                    "capability_id": cap_id,
                    "old_status": old_status,
                    "new_status": new_status,
                    "implementations": [
                        {"service_id": i["service_id"], "health": i.get("health", "unknown")}
                        for i in cap.get("implementations", [])
                    ],
                },
                source="capability_registry",
                severity=severity,
                journal=True,
            )

    # ------------------------------------------------------------------
    # Startup / discovery
    # ------------------------------------------------------------------

    def seed_from_explicit_mapping(self) -> Tuple[int, int]:
        """Load kai-capability-mapping.json and seed capability records + implementation links.

        For each service_id → capability_id mapping, ensures the capability exists
        (with full schema defaults) and links the service as primary if not already linked.
        Saves if anything changed.
        """
        if not MAPPING_FILE.exists():
            logger.warning("Explicit capability mapping not found at %s", MAPPING_FILE)
            return (0, 0)

        with open(MAPPING_FILE) as f:
            mapping = json.load(f)

        added = 0
        changed = 0
        for service_id, cap_id in mapping.items():
            if service_id.startswith("_"):
                continue

            # Ensure capability record exists
            if cap_id not in self._capabilities:
                self.upsert_capability(cap_id, {
                    "name": cap_id,
                    "canonical_owner": "unknown",
                    "priority": "P2",
                    "status": "unknown",
                })
                added += 1

            # Ensure service is linked as primary
            cap = self._capabilities[cap_id]
            impls = cap.get("implementations", [])
            if not any(i.get("service_id") == service_id for i in impls):
                impls.append({
                    "service_id": service_id,
                    "role": "primary",
                    "health": "unknown",
                    "auto_detected": False,
                    "override": False,
                })
                changed += 1
                cap["implementations"] = impls

        if added or changed:
            self.save()
        return (added, changed)

    def auto_discover(self):
        """Import and call link_services_to_capabilities from core.capability_registry_discovery."""
        from core.capability_registry_discovery import link_services_to_capabilities
        link_services_to_capabilities(self)

    def start(self):
        """Seed from explicit mapping, run auto-discovery, then start health worker thread."""
        logger.info("CapabilityRegistry starting — seeding from explicit mapping...")
        self.seed_from_explicit_mapping()
        logger.info("CapabilityRegistry seeding done — running auto-discovery...")
        self.auto_discover()
        t = threading.Thread(target=self._health_worker, daemon=True, name="capability-registry-health")
        t.start()
        logger.info("CapabilityRegistry health worker thread started")

    def _health_worker(self):
        """Blocking loop: refresh health for all capabilities, save, sleep 60s."""
        while True:
            try:
                for cap_id in list(self._capabilities.keys()):
                    self.refresh_health(cap_id)
                self.save()
            except Exception as exc:
                logger.warning("Health worker error: %s", exc)
            time.sleep(60.0)
