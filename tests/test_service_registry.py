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
        assert "name" in c or "Names" in c


def test_systemd_probe_returns_list(isolated_registry):
    """Systemd probe returns running services."""
    services = isolated_registry.discover_systemd()
    assert isinstance(services, list)


def test_port_probe_on_localhost(isolated_registry):
    """Port probe hits known ports, returns reachable services."""
    results = isolated_registry.discover_ports(hosts=["localhost"], ports=[20128])
    assert isinstance(results, list)
