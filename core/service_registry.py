"""
ServiceRegistry — JSON-persisted service catalog with atomic writes and backup.

Provides a singleton registry for discovering and tracking services in the
AI orchestrator ecosystem. All write operations are atomic (temp file + rename)
and previous versions are preserved in ``*.bak`` backup files.
"""
import os
import json
import shutil
import time
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

MEMORY_DIR = Path(os.environ.get("AI_ORCHESTRATOR_MEMORY_DIR", "memory"))

SERVICE_FILE = "kai_services.json"
HEALTH_FILE = "kai_services_health_history.json"


class ServiceRegistry:
    _instance: Optional["ServiceRegistry"] = None

    @classmethod
    def get_instance(cls, memory_dir: Path = None) -> "ServiceRegistry":
        """Return the singleton ServiceRegistry instance, creating it if necessary."""
        if cls._instance is None:
            cls._instance = cls(memory_dir=memory_dir)
        return cls._instance

    def __init__(self, memory_dir: Path = None):
        self.memory_dir = Path(memory_dir) if memory_dir is not None else MEMORY_DIR
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        self._services: dict = {}
        self._health_history: list = []
        self._load()

    def _load(self):
        path = self.memory_dir / SERVICE_FILE
        if path.exists():
            try:
                with open(path) as f:
                    self._services = json.load(f)
            except (json.JSONDecodeError, OSError) as exc:
                logger.warning("Failed to load services file %s: %s. Initialising empty.", path, exc)
                self._services = {}
        hpath = self.memory_dir / HEALTH_FILE
        if hpath.exists():
            try:
                with open(hpath) as f:
                    self._health_history = json.load(f)
            except (json.JSONDecodeError, OSError) as exc:
                logger.warning("Failed to load health history %s: %s. Initialising empty.", hpath, exc)
                self._health_history = []

    def save(self):
        # Atomic write: write .bak first, then replace
        path = self.memory_dir / SERVICE_FILE
        bak = self.memory_dir / f"{SERVICE_FILE}.bak"
        if path.exists():
            shutil.copy(path, bak)
        tmp = self.memory_dir / f"{SERVICE_FILE}.tmp"
        with open(tmp, "w") as f:
            json.dump(self._services, f, indent=2)
        os.replace(tmp, path)

        hpath = self.memory_dir / HEALTH_FILE
        with open(hpath, "w") as f:
            json.dump(self._health_history, f, indent=2)

    def list_services(self) -> dict:
        return dict(self._services)

    def get_service(self, service_id: str) -> Optional[dict]:
        return self._services.get(service_id)

    def upsert_service(self, service: dict):
        if "id" not in service:
            raise ValueError("service must have an 'id' field")
        service_id = service["id"]
        service["updated_at"] = time.time()
        if service_id not in self._services:
            service["created_at"] = time.time()
        self._services[service_id] = service
        self.save()

    def record_health(self, record: dict):
        """Append a health check record to the health history."""
        self._health_history.append(record)
        self.save()

    def delete_service(self, service_id: str) -> bool:
        """Remove a service from the registry. Returns True if it existed, False otherwise."""
        if service_id in self._services:
            del self._services[service_id]
            self.save()
            return True
        return False
