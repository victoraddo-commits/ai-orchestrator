# ai-orchestrator/tests/test_service_registry.py
import json
import pytest
import requests
from pathlib import Path
from core.service_registry import ServiceRegistry


def auth_headers():
    import core.api as api_module
    return {"Authorization": f"Bearer {api_module._load_api_token()}"}


@pytest.fixture
def isolated_registry(tmp_path, monkeypatch):
    """Point registry at a temp dir so tests never touch production memory."""
    monkeypatch.setattr("core.service_registry.MEMORY_DIR", tmp_path)
    return ServiceRegistry()


def test_registry_loads_empty(isolated_registry):
    assert isolated_registry.list_services() == {}


def test_registry_save_and_load():
    reg = ServiceRegistry()
    svc = {
        "id": "service-test",
        "name": "Test Service",
        "description": "A test",
        "version": "1.0.0",
        "environment": "production",
        "host": "test-host",
        "port": 9000,
        "endpoint": "http://localhost:9000/health",
        "protocol": "http",
        "type": "python-service",
        "owner": "test",
        "status": "unknown",
        "source": "manual",
        "metadata": {},
    }
    reg.upsert_service(svc)
    reg.save()

    reg2 = ServiceRegistry()
    assert "service-test" in reg2.list_services()
    assert reg2.get_service("service-test")["name"] == "Test Service"


def test_atomic_write_with_backup(tmp_path, monkeypatch):
    """save() must write a .bak file before replacing the main file.

    First save creates the main file; second save creates .bak from it.
    The .bak must contain the PREVIOUS version's data (name "S"), not the new one ("S v2").
    """
    monkeypatch.setattr("core.service_registry.MEMORY_DIR", tmp_path)
    reg = ServiceRegistry()
    reg.upsert_service({"id": "svc", "name": "S", "status": "unknown", "source": "manual"})
    reg.save()  # first save: creates main file

    reg.upsert_service({"id": "svc", "name": "S v2", "status": "healthy", "source": "manual"})
    # upsert_service auto-saves; .bak now holds version "S" from before this call

    assert (tmp_path / "kai_services.json").exists()
    assert (tmp_path / "kai_services.json.bak").exists()

    # .bak must have the previous version's name, not the current one
    with open(tmp_path / "kai_services.json.bak") as f:
        bak = json.load(f)
    assert bak["svc"]["name"] == "S"


def test_docker_probe_finds_containers(isolated_registry):
    """Docker probe returns a list of container dicts with name, image, ports."""
    containers = isolated_registry.discover_docker()
    assert isinstance(containers, list)
    if containers:
        c = containers[0]
        assert "name" in c
        assert "image" in c
        assert "status" in c


def test_systemd_probe_returns_list(isolated_registry):
    """Systemd probe returns running services."""
    services = isolated_registry.discover_systemd()
    assert isinstance(services, list)


def test_port_probe_on_localhost(isolated_registry):
    """Port probe hits known ports, returns reachable services."""
    results = isolated_registry.discover_ports(hosts=["localhost"], ports=[20128])
    assert isinstance(results, list)


def test_port_probe_returns_empty_for_unreachable(isolated_registry):
    """Port probe returns empty list for unreachable hosts without crashing."""
    results = isolated_registry.discover_ports(hosts=["192.168.255.254"], ports=[9999])
    assert results == []


def test_seed_from_ecosystem_graph(tmp_path, monkeypatch):
    """Services in the ecosystem graph but not in registry get added on seed."""
    import json
    import core.service_registry as sr
    monkeypatch.setattr("core.service_registry.MEMORY_DIR", tmp_path)

    # Create a fake ecosystem graph
    graph_path = tmp_path / "kai-ecosystem-graph.json"
    graph_path.write_text(json.dumps({
        "entities": {
            "service-test-graph": {
                "entity_id": "service-test-graph",
                "type": "python-service",
                "name": "Test From Graph",
                "port": 9001,
                "host": "test-host",
            }
        }
    }))

    orig_path = sr.ECOSYSTEM_GRAPH_PATH
    sr.ECOSYSTEM_GRAPH_PATH = graph_path
    try:
        reg = ServiceRegistry()
        added = reg.seed_from_ecosystem_graph()
    finally:
        sr.ECOSYSTEM_GRAPH_PATH = orig_path

    assert added == 1
    svc = reg.get_service("service-test-graph")
    assert svc is not None
    assert svc["source"] == "ecosystem_graph"


def test_run_discovery_calls_all_probes(isolated_registry, monkeypatch):
    """run_discovery() calls docker, systemd, port, and Proxmox probes and upserts results."""
    called = []

    def fake_docker():
        called.append("docker")
        return [{"name": "test-container", "status": "running", "ports": [], "service_id": None, "image": "test"}]

    def fake_systemd():
        called.append("systemd")
        return [{"name": "kai-orchestrator.service", "description": "AI Orchestrator", "service_id": "kai-orchestrator", "active_state": "running"}]

    def fake_ports():
        called.append("ports")
        return []

    def fake_proxmox():
        called.append("proxmox")
        return []

    monkeypatch.setattr(isolated_registry, "discover_docker", fake_docker)
    monkeypatch.setattr(isolated_registry, "discover_systemd", fake_systemd)
    monkeypatch.setattr(isolated_registry, "discover_ports", fake_ports)
    monkeypatch.setattr(isolated_registry, "discover_proxmox", fake_proxmox)

    isolated_registry.run_discovery()
    assert "docker" in called
    assert "systemd" in called
    assert "ports" in called
    assert "proxmox" in called
    # Verify Docker container was upserted (service_id is None so key is "service-docker--test-container")
    assert "service-docker--test-container" in isolated_registry.list_services()
    docker_svc = isolated_registry.get_service("service-docker--test-container")
    assert docker_svc["source"] == "auto_discovered"
    assert docker_svc["type"] == "container"
    # Verify systemd service was upserted
    assert "kai-orchestrator" in isolated_registry.list_services()
    systemd_svc = isolated_registry.get_service("kai-orchestrator")
    assert systemd_svc["source"] == "auto_discovered"
    assert systemd_svc["type"] == "systemd-service"


def test_health_check_updates_service_status(isolated_registry):
    """A service with an endpoint gets its status updated after a health check."""
    svc = {
        "id": "service-echo",
        "name": "Echo",
        "endpoint": "http://localhost:9999/health",
        "port": 9999,
        "protocol": "http",
        "status": "unknown",
        "source": "manual",
    }
    isolated_registry.upsert_service(svc)

    import requests_mock as rm
    with rm.Mocker() as m:
        m.get("http://localhost:9999/health", text="ok", status_code=200)
        result = isolated_registry.check_service_health("service-echo")

    assert result["result"] == "ok"
    assert result["response_code"] == 200
    assert isolated_registry.get_service("service-echo")["status"] == "running"


def test_consecutive_failures_degrade(isolated_registry):
    """Three consecutive failures set status to degraded."""
    svc = {
        "id": "service-fail",
        "name": "Failing Service",
        "endpoint": "http://localhost:9998/health",
        "port": 9998,
        "protocol": "http",
        "status": "running",
        "source": "manual",
    }
    isolated_registry.upsert_service(svc)

    import requests_mock as rm
    with rm.Mocker() as m:
        m.get("http://localhost:9998/health", exc=requests.exceptions.ConnectionError)
        # First failure
        isolated_registry.check_service_health("service-fail")
        # Second failure
        isolated_registry.check_service_health("service-fail")
        # Third failure — should now be degraded
        result = isolated_registry.check_service_health("service-fail")

    assert result["result"] == "error"
    assert isolated_registry.get_service("service-fail")["status"] == "degraded"


def test_health_check_nonexistent_service(isolated_registry):
    """check_service_health on unknown service returns not_found without crashing."""
    result = isolated_registry.check_service_health("does-not-exist")
    assert result["result"] == "not_found"
    assert result["service_id"] == "does-not-exist"


def test_health_history_capped_at_100(isolated_registry):
    """Health history per service is capped at 100 entries (FIFO)."""
    svc = {
        "id": "service-hist",
        "name": "History Test",
        "endpoint": "http://localhost:9997/health",
        "status": "unknown",
        "source": "manual",
    }
    isolated_registry.upsert_service(svc)

    for i in range(110):
        isolated_registry.record_health({
            "service_id": "service-hist",
            "checked_at": 1000 + i,
            "result": "ok",
            "latency_ms": 10.0,
        })

    hist = [h for h in isolated_registry._health_history if h["service_id"] == "service-hist"]
    assert len(hist) == 100
    # Oldest entries (1000-1009) should be gone
    assert not any(h["checked_at"] < 1010 for h in hist)


# Additional API tests — add to test_service_registry.py

def test_api_list_services(isolated_registry, client, monkeypatch):
    """GET /kai/services returns all registered services."""
    # Override the global registry in routes
    import core.service_registry_routes as routes
    monkeypatch.setattr(routes, "_registry", isolated_registry)

    isolated_registry.upsert_service({
        "id": "service-api-test",
        "name": "API Test",
        "status": "running",
        "source": "manual",
    })
    isolated_registry.save()

    response = client.get("/kai/services")
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert "service-api-test" in data["data"]

def test_api_register_service(isolated_registry, client, monkeypatch):
    """POST /kai/services registers a new service."""
    import core.service_registry_routes as routes
    monkeypatch.setattr(routes, "_registry", isolated_registry)

    response = client.post("/kai/services", json={
        "id": "service-new",
        "name": "New Service",
        "port": 9000,
        "type": "python-service",
        "status": "unknown",
        "source": "manual",
    }, headers=auth_headers())
    assert response.status_code == 200
    assert isolated_registry.get_service("service-new") is not None

def test_api_delete_service(isolated_registry, client, monkeypatch):
    """DELETE /kai/services/{id} removes a service."""
    import core.service_registry_routes as routes
    monkeypatch.setattr(routes, "_registry", isolated_registry)

    isolated_registry.upsert_service({
        "id": "service-to-delete",
        "name": "To Delete",
        "status": "running",
        "source": "manual",
    })
    isolated_registry.save()

    response = client.delete("/kai/services/service-to-delete", headers=auth_headers())
    assert response.status_code == 200
    assert isolated_registry.get_service("service-to-delete") is None
