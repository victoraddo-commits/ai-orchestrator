"""API route tests for service registry."""
import pytest
from fastapi.testclient import TestClient
from core.api import app
import core.service_registry_routes as routes
from core.service_registry import ServiceRegistry


def auth_headers():
    import core.api as api_module
    return {"Authorization": f"Bearer {api_module._load_api_token()}"}


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def isolated_registry(tmp_path, monkeypatch):
    """Point registry at a temp dir so tests never touch production memory."""
    monkeypatch.setattr("core.service_registry.MEMORY_DIR", tmp_path)
    return ServiceRegistry()

@pytest.fixture
def isolated_registry_client(isolated_registry, client, monkeypatch):
    """Route tests use isolated registry."""
    monkeypatch.setattr(routes, "_registry", isolated_registry)
    return client

def test_get_list_services(isolated_registry_client):
    r = isolated_registry_client.get("/kai/services")
    assert r.status_code == 200
    assert r.json()["ok"] is True

def test_get_service_detail_not_found(isolated_registry_client):
    r = isolated_registry_client.get("/kai/services/nonexistent")
    assert r.status_code == 404

def test_register_service(isolated_registry_client):
    r = isolated_registry_client.post("/kai/services", json={
        "id": "svc-new",
        "name": "New Service",
        "port": 9000,
        "type": "python-service",
        "status": "unknown",
        "source": "manual",
    }, headers=auth_headers())
    assert r.status_code == 200
    assert r.json()["ok"] is True

def test_register_service_requires_id(isolated_registry_client):
    r = isolated_registry_client.post("/kai/services", json={"name": "No ID"}, headers=auth_headers())
    assert r.status_code == 400

def test_update_service(isolated_registry_client, isolated_registry):
    isolated_registry.upsert_service({
        "id": "svc-to-update",
        "name": "Original",
        "status": "running",
        "source": "manual",
    })
    r = isolated_registry_client.put("/kai/services/svc-to-update", json={
        "name": "Updated",
    }, headers=auth_headers())
    assert r.status_code == 200
    assert isolated_registry.get_service("svc-to-update")["name"] == "Updated"

def test_delete_service(isolated_registry_client, isolated_registry):
    isolated_registry.upsert_service({
        "id": "svc-to-delete",
        "name": "To Delete",
        "status": "running",
        "source": "manual",
    })
    r = isolated_registry_client.delete("/kai/services/svc-to-delete", headers=auth_headers())
    assert r.status_code == 200
    assert isolated_registry.get_service("svc-to-delete") is None

def test_list_filter_by_status(isolated_registry_client, isolated_registry):
    isolated_registry.upsert_service({
        "id": "svc-running",
        "name": "Running",
        "status": "running",
        "source": "manual",
    })
    isolated_registry.upsert_service({
        "id": "svc-stopped",
        "name": "Stopped",
        "status": "stopped",
        "source": "manual",
    })
    r = isolated_registry_client.get("/kai/services?status=running")
    assert r.status_code == 200
    data = r.json()["data"]
    assert "svc-running" in data
    assert "svc-stopped" not in data

def test_get_health_summary(isolated_registry_client, isolated_registry):
    isolated_registry.upsert_service({
        "id": "svc-hw",
        "name": "Health Watch",
        "status": "running",
        "source": "manual",
    })
    r = isolated_registry_client.get("/kai/services/health")
    assert r.status_code == 200
    assert "svc-hw" in r.json()["data"]

def test_dependencies_endpoint(isolated_registry_client, isolated_registry):
    isolated_registry.upsert_service({
        "id": "svc-dep",
        "name": "Dep Test",
        "status": "running",
        "source": "manual",
    })
    r = isolated_registry_client.get("/kai/services/svc-dep/dependencies")
    assert r.status_code == 200
    assert r.json()["ok"] is True

def test_get_service_health(isolated_registry_client, isolated_registry):
    isolated_registry.upsert_service({
        "id": "svc-health",
        "name": "Health Check Test",
        "status": "running",
        "source": "manual",
    })
    r = isolated_registry_client.get("/kai/services/svc-health/health")
    assert r.status_code == 200
    assert r.json()["ok"] is True

def test_get_service_health_not_found(isolated_registry_client):
    r = isolated_registry_client.get("/kai/services/nonexistent/health")
    assert r.status_code == 404


def test_discover_endpoint(isolated_registry_client, isolated_registry, monkeypatch):
    """POST /kai/services/discover runs all probes and returns counts."""
    import core.service_registry_routes as routes
    monkeypatch.setattr(routes, "_registry", isolated_registry)

    # Stub all probes so they return deterministic results
    def fake_docker():
        return [{"name": "test-container", "status": "running",
                 "ports": [], "service_id": "svc-docker-1", "image": "nginx"}]
    def fake_systemd():
        return [{"name": "kai-orchestrator.service", "description": "AI Orchestrator",
                 "service_id": "kai-orchestrator", "active_state": "running"}]
    def fake_ports():
        return []
    def fake_proxmox():
        return []

    monkeypatch.setattr(isolated_registry, "discover_docker", fake_docker)
    monkeypatch.setattr(isolated_registry, "discover_systemd", fake_systemd)
    monkeypatch.setattr(isolated_registry, "discover_ports", fake_ports)
    monkeypatch.setattr(isolated_registry, "discover_proxmox", fake_proxmox)

    r = isolated_registry_client.post("/kai/services/discover", headers=auth_headers())
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert data["data"]["docker_containers"] == 1
    assert data["data"]["systemd_services"] == 1
    assert data["data"]["total_services"] >= 2


def test_full_lifecycle_via_api(isolated_registry_client, isolated_registry, monkeypatch):
    """Register → list → update → health → delete — full lifecycle through the API."""
    import core.service_registry_routes as routes
    monkeypatch.setattr(routes, "_registry", isolated_registry)

    # Register
    r = isolated_registry_client.post("/kai/services", json={
        "id": "lifecycle-test",
        "name": "Lifecycle Test",
        "port": 9999,
        "endpoint": "http://localhost:9999/health",
        "type": "python-service",
        "status": "unknown",
        "source": "manual",
    }, headers=auth_headers())
    assert r.status_code == 200
    assert r.json()["ok"] is True

    # List shows it
    r = isolated_registry_client.get("/kai/services")
    assert "lifecycle-test" in r.json()["data"]

    # Update
    r = isolated_registry_client.put("/kai/services/lifecycle-test", json={
        "name": "Lifecycle Test Updated",
        "status": "running",
    }, headers=auth_headers())
    assert r.status_code == 200
    assert isolated_registry.get_service("lifecycle-test")["name"] == "Lifecycle Test Updated"

    # Delete
    r = isolated_registry_client.delete("/kai/services/lifecycle-test", headers=auth_headers())
    assert r.status_code == 200
    assert isolated_registry.get_service("lifecycle-test") is None

    # List no longer shows it
    r = isolated_registry_client.get("/kai/services")
    assert "lifecycle-test" not in r.json()["data"]


def test_seed_from_ecosystem_graph_via_discover(isolated_registry_client, isolated_registry,
                                                tmp_path, monkeypatch):
    """Discover endpoint seeds from ecosystem graph when registry is empty."""
    import core.service_registry_routes as routes
    import core.service_registry as sr
    import json as _json
    monkeypatch.setattr(routes, "_registry", isolated_registry)

    # Create a fake ecosystem graph with one service
    graph_path = tmp_path / "kai-ecosystem-graph.json"
    graph_data = {
        "entities": {
            "services": {
                "svc-graph-seed": {
                    "entity_id": "svc-graph-seed",
                    "name": "Graph Seeded",
                    "type": "python-service",
                    "port": 9001,
                    "host": "test-host",
                }
            }
        }
    }
    graph_path.write_text(_json.dumps(graph_data))

    orig_path = sr.ECOSYSTEM_GRAPH_PATH
    sr.ECOSYSTEM_GRAPH_PATH = graph_path
    try:
        r = isolated_registry_client.post("/kai/services/discover", headers=auth_headers())
    finally:
        sr.ECOSYSTEM_GRAPH_PATH = orig_path

    assert r.status_code == 200
    assert isolated_registry.get_service("svc-graph-seed") is not None
    assert isolated_registry.get_service("svc-graph-seed")["source"] == "ecosystem_graph"
