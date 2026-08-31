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

    def discover_docker(self) -> list[dict]:
        """List all Docker containers via socket."""
        try:
            import docker
            client = docker.Client(base_url="unix:///var/run/docker.sock")
            containers = client.containers(all=True)
            results = []
            for c in containers:
                names = c.get("Names", [])
                name = names[0].lstrip("/") if names else c["Id"][:12]
                ports = c.get("Ports") or []
                port_mappings = []
                for p in ports:
                    if isinstance(p, dict):
                        port_mappings.append({
                            "private": p.get("PrivatePort"),
                            "public": p.get("PublicPort"),
                            "type": p.get("Type"),
                        })
                labels = c.get("Labels") or {}
                results.append({
                    "name": name,
                    "image": c.get("Image"),
                    "status": c.get("State"),
                    "ports": port_mappings,
                    "service_id": labels.get("com.kai.service_id"),
                    "metadata": {k: v for k, v in labels.items() if k.startswith("com.kai.")},
                })
            return results
        except ModuleNotFoundError as e:
            logger.warning("Docker module unavailable: %s", e)
            return []
        except docker.errors.DockerException as e:
            logger.warning("Docker probe failed: %s", e)
            return []

    def discover_systemd(self) -> list[dict]:
        """List running systemd services."""
        import subprocess
        try:
            result = subprocess.run(
                ["systemctl", "list-units", "--type=service",
                 "--state=running", "--no-pager", "--output=json"],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode != 0:
                return []
            units = json.loads(result.stdout)
            services = []
            for u in units:
                name = u.get("unit", "")
                if not name.endswith(".service"):
                    continue
                desc = u.get("description", "")
                active = u.get("active_state", "")
                services.append({
                    "name": name,
                    "description": desc,
                    "active_state": active,
                    "service_id": name.removesuffix(".service"),
                })
            return services
        except (subprocess.TimeoutExpired, OSError) as e:
            logger.warning("Systemd probe failed: %s", e)
            return []
        except json.JSONDecodeError as e:
            logger.warning("Systemd probe JSON parse error: %s", e)
            return []

    def discover_ports(self, hosts: list[str] = None, ports: list[int] = None) -> list[dict]:
        """Probe specific host:port combinations for HTTP/HTTPS health endpoints."""
        if hosts is None:
            # defaults for homelab environment
            hosts = ["localhost", "192.168.1.114", "192.168.1.120"]
        if ports is None:
            ports = [8000, 8090, 8091, 8092, 8094, 8095, 8130, 20128]
        import requests
        results = []
        for host in hosts:
            for port in ports:
                for scheme in ["https", "http"]:
                    url = f"{scheme}://{host}:{port}/health"
                    try:
                        r = requests.get(url, timeout=2, verify=False)
                        results.append({
                            "host": host,
                            "port": port,
                            "scheme": scheme,
                            "url": url,
                            "response_code": r.status_code,
                            "reachable": True,
                        })
                        break
                    except requests.exceptions.RequestException as e:
                        logger.debug("Port probe %s unreachable: %s", url, e)
        return results

    def discover_proxmox(self) -> list[dict]:
        """Query Proxmox API for LXC containers."""
        import os, requests
        host = os.environ.get("PROXMOX_HOST", "192.168.99.3")
        token = os.environ.get("PROXMOX_TOKEN", "")
        if not token:
            return []
        try:
            r = requests.get(
                f"https://{host}:8006/api2/json/nodes/pve/lxc",
                headers={"Authorization": f"PVEAPIToken={token}"},
                verify=False, timeout=5,
            )
            if r.status_code != 200:
                return []
            containers = r.json().get("data", [])
            results = []
            for c in containers:
                results.append({
                    "vmid": c.get("vmid"),
                    "name": c.get("name"),
                    "status": c.get("status"),
                    "ip": c.get("ip"),
                    "type": "container",
                })
            return results
        except requests.exceptions.RequestException as e:
            logger.warning("Proxmox probe failed: %s", e)
            return []
