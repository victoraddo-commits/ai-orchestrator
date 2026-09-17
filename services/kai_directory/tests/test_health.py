import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from services.kai_directory.models import Record
from services.kai_directory.health import classify, check_all


class _Ok(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.end_headers(); self.wfile.write(b"ok")
    def log_message(self, *a):
        pass


def test_classify():
    assert classify(200) == "up"
    assert classify(401) == "up"        # reachable, auth-gated
    assert classify(500) == "down"
    assert classify(None) == "down"


def test_check_all_sets_status():
    srv = HTTPServer(("127.0.0.1", 0), _Ok)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]
    recs = [Record(id="a", name="a", health_url=f"http://127.0.0.1:{port}/"),
            Record(id="b", name="b", health_url="http://127.0.0.1:1/")]
    out = check_all(recs, timeout=2)
    assert out[0].health_status == "up"
    assert out[1].health_status == "down"
    srv.shutdown()
