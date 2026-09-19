"""Tests for the Command Center streaming test-query endpoint + UI wiring.

Covers the SSE endpoint (``/api/juris-kai/cc/test-query-stream``) and the
Command Center JavaScript that consumes it via ``fetch`` + ``ReadableStream``.
No model calls: the local generator is monkeypatched. Runs the router on a bare
FastAPI app so ``core.api`` (and the scheduler it boots on import) is never
loaded.
"""

import json
import os
import re
import shutil
import subprocess
import tempfile
from importlib import util as _importlib_util
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.juris_kai import cc_routes

BRIDGE = {"Authorization": "Bearer test-bridge-token"}
CC_HTML = Path(__file__).resolve().parents[1] / "core" / "kai" / "command_center.html"
STREAM_PATH = "/api/juris-kai/cc/test-query-stream"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr("core.bridge_auth._load_api_token",
                        lambda: "test-bridge-token")
    cc_routes._rate_state.clear()
    app = FastAPI()
    app.include_router(cc_routes.router)
    return TestClient(app)


def _has_module(name: str) -> bool:
    try:
        return _importlib_util.find_spec(name) is not None
    except Exception:
        return False


def _parse_sse(body: str) -> list[dict]:
    """Parse an SSE body into ``{"event", "data"}`` frames."""
    events = []
    for frame in body.replace("\r\n", "\n").split("\n\n"):
        frame = frame.strip("\n")
        if not frame or frame.startswith(":"):
            continue
        event, data = "message", None
        for line in frame.split("\n"):
            if line.startswith("event:"):
                event = line[len("event:"):].strip()
            elif line.startswith("data:"):
                raw = line[len("data:"):].strip()
                try:
                    data = json.loads(raw)
                except ValueError:
                    data = raw
        events.append({"event": event, "data": data})
    return events


# ── SSE endpoint ─────────────────────────────────────────────────────────

class TestTestQueryStream:
    def test_requires_credentials(self, client):
        r = client.post(STREAM_PATH, json={"query": "x"})
        assert r.status_code == 401

    def test_session_without_capability_forbidden(self, client, monkeypatch):
        monkeypatch.setattr("core.authz.check_capability", lambda *a, **k: False)
        r = client.post(STREAM_PATH, headers={"X-Kai-Session": "viewer"},
                        json={"query": "x"})
        assert r.status_code == 403

    def test_requires_query(self, client):
        r = client.post(STREAM_PATH, headers=BRIDGE, json={})
        assert r.status_code == 400

    def test_emits_token_and_done_events(self, client, monkeypatch):
        monkeypatch.setattr("core.juris_kai.legal_context.query_knowledge_base",
                            lambda q: [])
        monkeypatch.setattr(
            "core.juris_kai.legal_context.build_context_preamble",
            lambda docs: "")
        monkeypatch.setattr("core.juris_kai.streaming.stream_chat",
                            lambda prompt, task_type="legal_research", **k:
                            iter(["Ghana ", "law."]))
        r = client.post(STREAM_PATH, headers=BRIDGE,
                        json={"query": "contract law",
                              "task_type": "juris_research"})
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/event-stream")

        events = _parse_sse(r.text)
        tokens = "".join(e["data"]["text"] for e in events
                         if e["event"] == "token")
        assert tokens == "Ghana law."

        done = [e["data"] for e in events if e["event"] == "done"]
        assert done, "done event missing"
        assert done[-1]["chars"] == len("Ghana law.")
        assert done[-1]["model"]
        assert done[-1]["ttft_ms"] is not None
        assert done[-1]["total_ms"] is not None

    def test_stream_error_emits_error_and_done(self, client, monkeypatch):
        monkeypatch.setattr("core.juris_kai.legal_context.query_knowledge_base",
                            lambda q: [])
        monkeypatch.setattr(
            "core.juris_kai.legal_context.build_context_preamble",
            lambda docs: "")

        def boom(prompt, task_type="legal_research", **k):
            def gen():
                yield "partial"
                raise RuntimeError("ollama down")
            return gen()

        monkeypatch.setattr("core.juris_kai.streaming.stream_chat", boom)
        r = client.post(STREAM_PATH, headers=BRIDGE, json={"query": "x"})
        assert r.status_code == 200
        events = _parse_sse(r.text)
        assert any(e["event"] == "error" for e in events)
        assert any(e["event"] == "done" for e in events)


# ── Command Center UI wiring ─────────────────────────────────────────────

def _script_blocks(html: str) -> list[str]:
    return re.findall(r"<script[^>]*>(.*?)</script>", html, re.DOTALL)


class TestCommandCenterStreamingUI:
    @pytest.fixture(scope="class")
    def html(self):
        return CC_HTML.read_text(encoding="utf-8")

    def test_uses_fetch_and_readablestream(self, html):
        assert STREAM_PATH in html
        assert "getReader" in html
        assert "TextDecoder" in html
        assert "_sseParse" in html

    def test_renders_tokens_and_live_metrics(self, html):
        # Tokens land in the answer box via textContent (escaped by definition)
        # and the live badges carry ttft/total/chars.
        assert "ans.textContent=text" in html or "ans.textContent = text" in html
        assert "chars" in html and "ttft" in html

    def test_keeps_blocking_fallback(self, html):
        assert "/api/juris-kai/cc/test-query'" in html
        assert "jurisTestQueryFallback" in html

    def test_js_syntax(self, html):
        blocks = _script_blocks(html)
        assert blocks, "no <script> blocks found in command_center.html"

        qjs = _importlib_util.find_spec("quickjs") if _has_module("quickjs") else None
        node = shutil.which("node") or shutil.which("nodejs")
        if not qjs and not node:
            pytest.skip("neither quickjs nor node available for JS syntax check")

        for idx, block in enumerate(blocks):
            if qjs:
                import quickjs
                try:
                    # Compile without executing: `new Function(src)` parses the
                    # body and throws SyntaxError on invalid JS.
                    quickjs.Context().eval(
                        "new Function(" + json.dumps(block) + ")")
                except Exception as exc:
                    pytest.fail(f"script block {idx} syntax error: {exc}")
                continue
            path = None
            try:
                with tempfile.NamedTemporaryFile("w", suffix=".js",
                                                 delete=False) as fh:
                    fh.write(block)
                    path = fh.name
                proc = subprocess.run([node, "--check", path],
                                      capture_output=True, text=True)
            finally:
                if path and os.path.exists(path):
                    os.unlink(path)
            assert proc.returncode == 0, (
                f"script block {idx} syntax error: {proc.stderr.strip()}")
