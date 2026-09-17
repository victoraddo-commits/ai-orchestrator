import os
import tempfile

from fastapi.testclient import TestClient

from services.kai_directory.api import create_app
from services.kai_directory.store import RecordStore


def client_with_store():
    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    return TestClient(create_app(RecordStore(path)))


def test_health_and_services_list():
    c = client_with_store()
    assert c.get("/health").json()["ok"] is True
    assert c.get("/services").json() == []


def test_create_get_delete():
    c = client_with_store()
    body = {"id": "money", "name": "money", "display_name": "Money",
            "category": "finances", "ip": "192.168.1.118", "port": 8095}
    assert c.post("/services", json=body).status_code == 201
    assert c.get("/services/money").json()["display_name"] == "Money"
    assert c.delete("/services/money").status_code == 200
    assert c.get("/services/money").status_code == 404


def test_policy_endpoint_returns_json():
    c = client_with_store()
    body = {"id": "money", "name": "money", "ip": "192.168.1.118", "port": 8095}
    c.post("/services", json=body)
    assert "svc:money" in c.get("/policy").text


def test_names_persist_and_appear_everywhere():
    c = client_with_store()
    body = {"id": "money", "name": "money", "display_name": "Money",
            "ip": "192.168.1.118", "port": 8095}
    c.post("/services", json=body)
    svc = c.get("/services/money").json()
    assert svc["tailnet_name"] == "money.tail82a9ca.ts.net"
    assert svc["proxy_node"] == "proxmox-b"
    assert "svc:money" in c.get("/policy").text
    cmds = c.get("/serve-commands", params={"node": "proxmox-b"}).json()
    assert any("svc:money" in x for x in cmds)
    assert "money.tail82a9ca.ts.net" in c.get("/").text


def test_missing_id_returns_422():
    c = client_with_store()
    assert c.post("/services", json={"name": "x"}).status_code == 422


def test_source_null_does_not_break_discover():
    c = client_with_store()
    c.post("/services", json={"id": "m", "name": "m", "source": None,
                              "ip": "192.168.1.118", "port": 8095})
    assert c.post("/discover").status_code == 200
