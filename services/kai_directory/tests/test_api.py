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
