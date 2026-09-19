import ssl
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from services.kai_directory import health
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


def test_insecure_tls_hosts_defaults_to_empty(monkeypatch):
    monkeypatch.delenv("KAI_DIRECTORY_INSECURE_TLS_HOSTS", raising=False)
    assert health._insecure_tls_hosts() == set()


def test_tls_context_only_for_allowlisted_https_hosts(monkeypatch):
    monkeypatch.setenv("KAI_DIRECTORY_INSECURE_TLS_HOSTS", "192.168.1.111")

    allowed = health._tls_context_for(
        health.urllib.parse.urlparse("https://192.168.1.111:8000/health")
    )
    assert allowed is not None
    assert allowed.verify_mode == ssl.CERT_NONE
    assert allowed.check_hostname is False

    # Any other host keeps normal verification (no blanket disable).
    assert health._tls_context_for(
        health.urllib.parse.urlparse("https://8.8.8.8/")
    ) is None
    # Plain HTTP never needs a TLS context.
    assert health._tls_context_for(
        health.urllib.parse.urlparse("http://192.168.1.111:8000/health")
    ) is None


def test_check_one_uses_insecure_context_only_for_configured_host(monkeypatch):
    captured = {}

    class _FakeResp:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def fake_urlopen(req, timeout=None, context=None):
        captured["context"] = context
        return _FakeResp()

    monkeypatch.setattr(health.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setenv("KAI_DIRECTORY_INSECURE_TLS_HOSTS", "192.168.1.111")

    rec = Record(id="cc", name="cc", health_url="https://192.168.1.111:8000/health")
    assert health.check_one(rec) == "up"
    assert captured["context"] is not None
    assert captured["context"].verify_mode == ssl.CERT_NONE

    other = Record(id="web", name="web", health_url="https://example.com/health")
    health.check_one(other)
    assert captured["context"] is None

