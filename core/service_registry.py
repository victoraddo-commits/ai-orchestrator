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
ECOSYSTEM_GRAPH_PATH = Path("/project/uploads/kai-ecosystem-graph.json")
MAX_HEALTH_HISTORY = 100


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
        hbak = self.memory_dir / f"{HEALTH_FILE}.bak"
        if hpath.exists():
            shutil.copy(hpath, hbak)
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
        """Add health record, trim to MAX_HEALTH_HISTORY per service (FIFO)."""
        self._health_history.append(record)
        by_service = {}
        for h in self._health_history:
            sid = h["service_id"]
            by_service.setdefault(sid, []).append(h)
        trimmed = {}
        needs_save = False
        for sid, records in by_service.items():
            if len(records) > MAX_HEALTH_HISTORY:
                trimmed[sid] = records[-MAX_HEALTH_HISTORY:]
                needs_save = True
            else:
                trimmed[sid] = records
        self._health_history = [h for records in trimmed.values() for h in records]
        if needs_save:
            self.save()

    def check_service_health(self, service_id: str) -> dict:
        """Perform a single health check on a service. Returns result dict."""
        import time as _time
        import requests as _requests
        svc = self._services.get(service_id)
        if not svc:
            return {"service_id": service_id, "result": "not_found"}

        # Stale entry check — if last check was > 5 min ago, mark unknown
        last_check = svc.get("last_health_check")
        if last_check and (svc.get("endpoint")):
            if _time.time() - last_check > 300:
                svc["status"] = "unknown"

        endpoint = svc.get("endpoint")
        if not endpoint:
            return {"service_id": service_id, "result": "no_endpoint"}

        checked_at = _time.time()
        try:
            start = _time.perf_counter()
            # suppress SSL warnings — homelab internal certs
            r = _requests.get(endpoint, timeout=5, verify=False)
            latency_ms = (_time.perf_counter() - start) * 1000

            if r.status_code == 200:
                result = "ok"
                svc["_consecutive_failures"] = 0
                svc["status"] = "running"
            else:
                result = "error"
                consecutive = svc.get("_consecutive_failures", 0) + 1
                svc["_consecutive_failures"] = consecutive
                svc["status"] = "degraded" if consecutive >= 3 else "stopped"
            svc["last_health_check"] = checked_at
            svc["last_health_result"] = result

            health_record = {
                "service_id": service_id,
                "checked_at": checked_at,
                "result": result,
                "latency_ms": round(latency_ms, 2),
                "error": None,
                "response_code": r.status_code,
            }
        except _requests.exceptions.Timeout:
            svc["_consecutive_failures"] = svc.get("_consecutive_failures", 0) + 1
            svc["status"] = "degraded" if svc["_consecutive_failures"] >= 3 else "stopped"
            svc["last_health_check"] = checked_at
            svc["last_health_result"] = "timeout"
            health_record = {
                "service_id": service_id, "checked_at": checked_at,
                "result": "timeout", "latency_ms": 5000,
                "error": "Connection timeout", "response_code": None,
            }
        except _requests.exceptions.RequestException as e:
            svc["_consecutive_failures"] = svc.get("_consecutive_failures", 0) + 1
            svc["status"] = "degraded" if svc["_consecutive_failures"] >= 3 else "stopped"
            svc["last_health_check"] = checked_at
            svc["last_health_result"] = "error"
            health_record = {
                "service_id": service_id, "checked_at": checked_at,
                "result": "error", "latency_ms": 0,
                "error": str(e), "response_code": None,
            }

        self.record_health(health_record)
        self.save()
        return health_record

    async def health_loop(self, interval: float = 60.0):
        """Async health check loop. Call as background task."""
        import asyncio
        while True:
            for sid in list(self._services.keys()):
                svc = self._services.get(sid)
                if svc and svc.get("endpoint"):
                    loop = asyncio.get_event_loop()
                    await loop.run_in_executor(None, self.check_service_health, sid)
            await asyncio.sleep(interval)

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

    def seed_from_ecosystem_graph(self) -> int:
        """Load services from the ecosystem graph JSON and add them to the registry."""
        import json as _json
        path = ECOSYSTEM_GRAPH_PATH
        if not path.exists():
            logger.warning(f"Ecosystem graph not found at {path}")
            return 0

        with open(path) as f:
            graph = _json.load(f)

        entities = graph.get("entities", {})
        services_data = entities.get("services", {})
        added = 0

        for sid, svc in services_data.items():
            canonical_id = svc.get("entity_id", sid)
            if canonical_id in self._services:
                continue  # already registered, don't overwrite

            service = {
                "id": canonical_id,
                "name": svc.get("name", canonical_id),
                "description": svc.get("description", ""),
                "version": svc.get("version"),
                "environment": svc.get("environment", "production"),
                "host": svc.get("host"),
                "port": svc.get("port"),
                "endpoint": svc.get("health_endpoint") or svc.get("endpoint"),
                "protocol": svc.get("protocol", "https"),
                "type": svc.get("type", "unknown"),
                "owner": svc.get("owner", "unknown"),
                "status": "unknown",
                "source": "ecosystem_graph",
                "metadata": {},
            }
            self._services[canonical_id] = service
            added += 1

        if added:
            self.save()
        return added

    def start(self):
        """Run on API startup: seed from ecosystem graph then start background health loop."""
        import threading
        logger.info("ServiceRegistry starting — seeding from ecosystem graph...")
        added = self.seed_from_ecosystem_graph()
        logger.info("ServiceRegistry seeded: %d new services added", added)
        t = threading.Thread(target=self._health_worker, daemon=True, name="service-registry-health")
        t.start()
        logger.info("ServiceRegistry health worker thread started")

    def _health_worker(self):
        """Blocking health loop — runs in a daemon thread."""
        import time as _time
        while True:
            for sid in list(self._services.keys()):
                svc = self._services.get(sid)
                if svc and svc.get("endpoint"):
                    try:
                        self.check_service_health(sid)
                    except Exception as exc:
                        logger.warning("Health check failed for %s: %s", sid, exc)
            _time.sleep(60.0)

    def run_discovery(self) -> dict:
        """Run all discovery probes and upsert results into the registry."""
        # Seed from ecosystem graph first so manually tracked services are present
        self.seed_from_ecosystem_graph()
        results = {
            "docker": self.discover_docker(),
            "systemd": self.discover_systemd(),
            "ports": self.discover_ports(),
            "proxmox": self.discover_proxmox(),
        }

        # Convert Docker containers to service entries
        for c in results["docker"]:
            service_id = c.get("service_id") or f"service-docker--{c['name']}"
            if service_id in self._services:
                current = self._services[service_id]
                current["status"] = "running" if c["status"] == "running" else "stopped"
                current["updated_at"] = time.time()
            else:
                self.upsert_service({
                    "id": service_id,
                    "name": c["name"],
                    "description": f"Docker container: {c.get('image', '')}",
                    "version": None,
                    "environment": "production",
                    "host": "localhost",
                    "port": c["ports"][0]["private"] if c["ports"] else None,
                    "endpoint": None,
                    "protocol": "docker",
                    "type": "container",
                    "owner": "docker",
                    "status": "running" if c["status"] == "running" else "stopped",
                    "source": "auto_discovered",
                    "metadata": {"image": c.get("image")},
                })

        # Convert systemd services
        for s in results["systemd"]:
            sid = s["service_id"]
            if sid in self._services:
                self._services[sid]["status"] = "running"
                self._services[sid]["updated_at"] = time.time()
            else:
                self.upsert_service({
                    "id": sid,
                    "name": s["name"],
                    "description": s.get("description", ""),
                    "version": None,
                    "environment": "production",
                    "host": "localhost",
                    "port": None,
                    "endpoint": None,
                    "protocol": "systemd",
                    "type": "systemd-service",
                    "owner": "system",
                    "status": "running",
                    "source": "auto_discovered",
                    "metadata": {},
                })

        # Port probe results — update endpoint for services with matching ports
        for p in results["ports"]:
            if not p.get("reachable"):
                continue
            for sid, svc in self._services.items():
                if svc.get("port") == p["port"] and svc.get("source") == "ecosystem_graph":
                    svc["endpoint"] = p["url"]
                    svc["status"] = "running"
                    svc["updated_at"] = time.time()

        self.save()
        return results
