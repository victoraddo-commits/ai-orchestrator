# ai-orchestrator/tests/test_service_registry.py
import json
import pytest
from pathlib import Path
from core.service_registry import ServiceRegistry


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
    """
    monkeypatch.setattr("core.service_registry.MEMORY_DIR", tmp_path)
    reg = ServiceRegistry()
    reg.upsert_service({"id": "svc", "name": "S", "status": "unknown", "source": "manual"})
    reg.save()  # first save: creates main file

    reg.upsert_service({"id": "svc", "name": "S v2", "status": "healthy", "source": "manual"})
    reg.save()  # second save: .bak created from existing main file

    assert (tmp_path / "kai_services.json").exists()
    assert (tmp_path / "kai_services.json.bak").exists()
