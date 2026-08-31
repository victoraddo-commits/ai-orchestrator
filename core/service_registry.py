# ai-orchestrator/core/service_registry.py
import os
import json
import shutil
from pathlib import Path
from datetime import datetime
from typing import Optional

MEMORY_DIR = Path(os.environ.get("AI_ORCHESTRATOR_MEMORY_DIR", "memory"))

SERVICE_FILE = "kai_services.json"
HEALTH_FILE = "kai_services_health_history.json"


class ServiceRegistry:
    _instance: Optional["ServiceRegistry"] = None

    def __init__(self, memory_dir: Path = None):
        self.memory_dir = Path(memory_dir) if memory_dir is not None else MEMORY_DIR
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        self._services: dict = {}
        self._health_history: list = []
        self._load()

    def _load(self):
        path = self.memory_dir / SERVICE_FILE
        if path.exists():
            with open(path) as f:
                self._services = json.load(f)
        hpath = self.memory_dir / HEALTH_FILE
        if hpath.exists():
            with open(hpath) as f:
                self._health_history = json.load(f)

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
        return self._services

    def get_service(self, service_id: str) -> Optional[dict]:
        return self._services.get(service_id)

    def upsert_service(self, service: dict):
        service_id = service["id"]
        service["updated_at"] = datetime.now().isoformat()
        if service_id not in self._services:
            service["created_at"] = datetime.now().isoformat()
        self._services[service_id] = service
