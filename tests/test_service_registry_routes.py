"""API route tests for service registry."""
import pytest
from fastapi.testclient import TestClient
from core.api import app
import core.service_registry_routes as routes
from core.service_registry import ServiceRegistry


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
    })
    assert r.status_code == 200
    assert r.json()["ok"] is True

def test_register_service_requires_id(isolated_registry_client):
    r = isolated_registry_client.post("/kai/services", json={"name": "No ID"})
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
    })
    assert r.status_code == 200
    assert isolated_registry.get_service("svc-to-update")["name"] == "Updated"

def test_delete_service(isolated_registry_client, isolated_registry):
    isolated_registry.upsert_service({
        "id": "svc-to-delete",
        "name": "To Delete",
        "status": "running",
        "source": "manual",
    })
    r = isolated_registry_client.delete("/kai/services/svc-to-delete")
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
    r = isolated_registry_client.get("/kai/services/dependencies/svc-dep")
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
