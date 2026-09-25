"""Route contract tests: the smart-home API must be operator-gated (401 without
a session) and must expose the expected paths in OpenAPI."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from fastapi.testclient import TestClient


def _client():
    from core.api import app
    return TestClient(app, raise_server_exceptions=False)


def test_smarthome_routes_require_operator():
    c = _client()
    for path in ("/api/smarthome/devices", "/api/smarthome/rooms",
                 "/api/smarthome/providers"):
        r = c.get(path)
        assert r.status_code == 401, f"{path} -> {r.status_code}"


def test_smarthome_routes_in_openapi():
    from core.api import app
    paths = app.openapi()["paths"]
    for p in ("/api/smarthome/devices", "/api/smarthome/rooms",
              "/api/smarthome/providers", "/api/smarthome/refresh",
              "/api/smarthome/devices/{device_id}/control"):
        assert p in paths, f"missing route {p}"
