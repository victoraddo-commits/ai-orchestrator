"""Tests for core.capability_registry_routes."""
import pytest
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient


def auth_headers():
    import core.api as api_module
    return {"Authorization": f"Bearer {api_module._load_api_token()}"}


@pytest.fixture
def client():
    """Return a TestClient with the global _registry patched to a mock."""
    # Patch the global _registry in the routes module before importing the app
    mock_reg = MagicMock()
    mock_reg.list_capabilities.return_value = {}
    mock_reg.get_capability.return_value = None
    mock_reg.upsert_capability.return_value = None
    mock_reg.delete_capability.return_value = False
    mock_reg.add_implementation.return_value = True
    mock_reg.remove_implementation.return_value = False
    mock_reg.refresh_health.return_value = None
    mock_reg.save.return_value = None
    mock_reg.auto_discover.return_value = None

    with patch("core.capability_registry_routes._registry", mock_reg):
        with patch("core.capability_registry_routes.get_registry", return_value=mock_reg):
            from core.capability_registry_routes import router
            from fastapi import FastAPI
            app = FastAPI()
            app.include_router(router)
            yield TestClient(app), mock_reg


class TestListCapabilities:
    def test_empty_registry(self, client):
        tc, mock_reg = client
        mock_reg.list_capabilities.return_value = {}
        resp = tc.get("/kai/capabilities")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["data"] == {}
        assert data["meta"]["count"] == 0

    def test_with_data(self, client):
        tc, mock_reg = client
        mock_reg.list_capabilities.return_value = {
            "notifications": {"capability_id": "notifications", "status": "healthy"}
        }
        resp = tc.get("/kai/capabilities")
        assert resp.status_code == 200
        data = resp.json()
        assert data["data"]["notifications"]["status"] == "healthy"
        assert data["meta"]["count"] == 1

    def test_with_status_filter(self, client):
        tc, mock_reg = client
        mock_reg.list_capabilities.return_value = {}
        tc.get("/kai/capabilities", params={"status": "healthy"})
        mock_reg.list_capabilities.assert_called_once_with(status="healthy", priority=None, owner=None)

    def test_with_all_filters(self, client):
        tc, mock_reg = client
        mock_reg.list_capabilities.return_value = {}
        tc.get("/kai/capabilities", params={"status": "down", "priority": "P1", "owner": "ops"})
        mock_reg.list_capabilities.assert_called_once_with(status="down", priority="P1", owner="ops")


class TestGetCapability:
    def test_found(self, client):
        tc, mock_reg = client
        mock_reg.get_capability.return_value = {"capability_id": "notifications", "status": "healthy", "health_history": []}
        resp = tc.get("/kai/capabilities/notifications")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["data"]["capability_id"] == "notifications"

    def test_not_found(self, client):
        tc, mock_reg = client
        mock_reg.get_capability.return_value = None
        resp = tc.get("/kai/capabilities/nonexistent")
        assert resp.status_code == 404
        assert "not found" in resp.json()["detail"]


class TestCheckHealth:
    def test_found(self, client):
        tc, mock_reg = client
        updated_cap = {"capability_id": "notifications", "status": "healthy", "health_history": []}
        mock_reg.get_capability.return_value = updated_cap
        resp = tc.get("/kai/capabilities/notifications/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["data"]["status"] == "healthy"
        mock_reg.refresh_health.assert_called_once_with("notifications")
        mock_reg.save.assert_called_once()

    def test_not_found(self, client):
        tc, mock_reg = client
        mock_reg.get_capability.return_value = None
        resp = tc.get("/kai/capabilities/nonexistent/health")
        assert resp.status_code == 404


class TestRegisterCapability:
    def test_success(self, client):
        tc, mock_reg = client
        saved_cap = {"capability_id": "notifications", "name": "Notifications", "status": "unknown"}
        mock_reg.get_capability.return_value = saved_cap
        resp = tc.post("/kai/capabilities", json={"capability_id": "notifications", "name": "Notifications"}, headers=auth_headers())
        assert resp.status_code == 200
        assert resp.json()["ok"] is True
        mock_reg.upsert_capability.assert_called_once()
        mock_reg.save.assert_called_once()

    def test_missing_capability_id(self, client):
        tc, mock_reg = client
        resp = tc.post("/kai/capabilities", json={"name": "Notifications"}, headers=auth_headers())
        assert resp.status_code == 400
        assert "capability_id" in resp.json()["detail"]


class TestUpdateCapability:
    def test_success(self, client):
        tc, mock_reg = client
        updated_cap = {"capability_id": "notifications", "status": "healthy"}
        mock_reg.get_capability.side_effect = [
            {"capability_id": "notifications", "status": "unknown"},  # check existence
            updated_cap,  # return after update
        ]
        resp = tc.put("/kai/capabilities/notifications", json={"status": "healthy"}, headers=auth_headers())
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    def test_not_found(self, client):
        tc, mock_reg = client
        mock_reg.get_capability.return_value = None
        resp = tc.put("/kai/capabilities/nonexistent", json={"status": "healthy"}, headers=auth_headers())
        assert resp.status_code == 404


class TestDeregisterCapability:
    def test_success(self, client):
        tc, mock_reg = client
        mock_reg.delete_capability.return_value = True
        resp = tc.delete("/kai/capabilities/notifications", headers=auth_headers())
        assert resp.status_code == 200
        assert resp.json()["data"]["deleted"] is True

    def test_not_found(self, client):
        tc, mock_reg = client
        mock_reg.delete_capability.return_value = False
        resp = tc.delete("/kai/capabilities/nonexistent", headers=auth_headers())
        assert resp.status_code == 404


class TestAddImplementation:
    def test_success(self, client):
        tc, mock_reg = client
        mock_reg.get_capability.return_value = {"capability_id": "notifications", "implementations": []}
        resp = tc.post("/kai/capabilities/notifications/implementations", json={"service_id": "svc-1", "role": "primary"}, headers=auth_headers())
        assert resp.status_code == 200
        mock_reg.add_implementation.assert_called_once_with("notifications", "svc-1", role="primary")
        mock_reg.save.assert_called_once()

    def test_missing_service_id(self, client):
        tc, mock_reg = client
        resp = tc.post("/kai/capabilities/notifications/implementations", json={"role": "primary"}, headers=auth_headers())
        assert resp.status_code == 400
        assert "service_id" in resp.json()["detail"]

    def test_capability_not_found(self, client):
        tc, mock_reg = client
        mock_reg.get_capability.return_value = None
        resp = tc.post("/kai/capabilities/nonexistent/implementations", json={"service_id": "svc-1"}, headers=auth_headers())
        assert resp.status_code == 404


class TestRemoveImplementation:
    def test_success(self, client):
        tc, mock_reg = client
        mock_reg.get_capability.return_value = {"capability_id": "notifications", "implementations": []}
        mock_reg.remove_implementation.return_value = True
        resp = tc.delete("/kai/capabilities/notifications/implementations/svc-1", headers=auth_headers())
        assert resp.status_code == 200
        mock_reg.remove_implementation.assert_called_once_with("notifications", "svc-1")
        mock_reg.save.assert_called_once()

    def test_capability_not_found(self, client):
        tc, mock_reg = client
        mock_reg.get_capability.return_value = None
        resp = tc.delete("/kai/capabilities/nonexistent/implementations/svc-1", headers=auth_headers())
        assert resp.status_code == 404

    def test_implementation_not_found(self, client):
        tc, mock_reg = client
        mock_reg.get_capability.return_value = {"capability_id": "notifications", "implementations": []}
        mock_reg.remove_implementation.return_value = False
        resp = tc.delete("/kai/capabilities/notifications/implementations/nonexistent", headers=auth_headers())
        assert resp.status_code == 404


class TestTriggerDiscovery:
    def test_success(self, client):
        tc, mock_reg = client
        mock_reg.list_capabilities.return_value = {"notifications": {}, "telegram": {}}
        resp = tc.post("/kai/capabilities/discover", headers=auth_headers())
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["data"]["discovered"] is True
        assert data["data"]["total_capabilities"] == 2
        assert data["meta"]["count"] == 2


class TestGetDependents:
    def test_found(self, client):
        tc, mock_reg = client
        mock_reg.get_capability.return_value = {
            "capability_id": "notifications",
            "consumed_by": ["kai-mobile"],
            "consumed_by_override": [],
        }
        resp = tc.get("/kai/capabilities/notifications/dependents")
        assert resp.status_code == 200
        data = resp.json()
        assert data["data"]["consumed_by"] == ["kai-mobile"]
        assert data["data"]["consumed_by_override"] == []

    def test_not_found(self, client):
        tc, mock_reg = client
        mock_reg.get_capability.return_value = None
        resp = tc.get("/kai/capabilities/nonexistent/dependents")
        assert resp.status_code == 404
