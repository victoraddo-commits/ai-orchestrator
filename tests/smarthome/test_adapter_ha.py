import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core.smarthome.adapters.homeassistant import HomeAssistantAdapter


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path == "/api/states":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps([
                {"entity_id": "light.kitchen", "state": "on",
                 "attributes": {"friendly_name": "Kitchen light", "brightness": 120}},
            ]).encode())
        elif self.path == "/api/states/light.kitchen":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(
                {"entity_id": "light.kitchen", "state": "on",
                 "attributes": {"friendly_name": "Kitchen light", "brightness": 120}}).encode())
        else:
            self.send_response(404)
            self.end_headers()


def _server():
    srv = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def test_identify_and_state():
    srv = _server()
    host, port = srv.server_address
    a = HomeAssistantAdapter(base_url=f"http://{host}:{port}", token="t", session=None)
    ids = a.identify()
    assert ids[0]["provider_id"] == "light.kitchen"
    assert ids[0]["name"] == "Kitchen light"
    st = a.get_state("light.kitchen")
    assert st["state"] == "on" and st["brightness"] == 120
    srv.shutdown()


def test_missing_token_raises():
    try:
        HomeAssistantAdapter(base_url="http://127.0.0.1:1", token="", session=None).identify()
        assert False
    except Exception as e:
        assert "token" in str(e).lower()
