"""Tests for AI-4: Serverless Endpoints — handler.py and vercel_handler.py.

Covers: handle_health, handle_completion (auto + direct routing),
error responses, and Vercel handler HTTP interface.
"""

import json
import pytest
from unittest.mock import patch


# ---------------------------------------------------------------------------
# handle_health tests
# ---------------------------------------------------------------------------


class TestHandleHealth:
    def test_returns_healthy_status(self):
        from core.ai_serverless.handler import handle_health

        result = handle_health()
        assert result["status"] == "healthy"
        assert isinstance(result["providers"], dict)

    def test_includes_text_task_providers(self):
        from core.ai_serverless.handler import handle_health

        result = handle_health()
        # Should include providers with text_task capability
        for name, info in result["providers"].items():
            assert "available" in info
            assert "kind" in info
            assert "cost_tier" in info
            assert isinstance(info["available"], bool)


# ---------------------------------------------------------------------------
# handle_completion tests
# ---------------------------------------------------------------------------


class TestHandleCompletion:
    def test_auto_route_returns_openai_shape(self, monkeypatch):
        from core.ai_serverless.handler import handle_completion

        def fake_delegate(prompt, task_type=None, capability=None,
                          return_attempts=None, provider=None):
            return {
                "response": "Hello!",
                "provider": "gemini",
                "attempts": [],
            }

        monkeypatch.setattr(
            "core.ai.ai_router.delegate", fake_delegate
        )

        result = handle_completion("Say hi", model="auto")
        assert result["object"] == "chat.completion"
        assert len(result["choices"]) == 1
        assert result["choices"][0]["message"]["content"] == "Hello!"
        assert result["model"] == "gemini"
        assert "usage" in result
        assert result["usage"]["completion_tokens"] > 0

    def test_direct_route_passes_provider(self, monkeypatch):
        from core.ai_serverless.handler import handle_completion

        received_provider = None

        def fake_delegate(prompt, task_type=None, capability=None,
                          return_attempts=None, provider=None):
            nonlocal received_provider
            received_provider = provider
            return {
                "response": "OK",
                "provider": provider or "gemini",
                "attempts": [],
            }

        monkeypatch.setattr(
            "core.ai.ai_router.delegate", fake_delegate
        )

        result = handle_completion("Say hi", model="gemini")
        assert received_provider == "gemini"
        assert result["model"] == "gemini"

    def test_unknown_model_returns_error(self, monkeypatch):
        from core.ai_serverless.handler import handle_completion

        # The get_provider call returns None for unknown providers
        result = handle_completion("Say hi", model="nonexistent_provider_xyz")
        assert result["model"] == "error"
        assert "error" in result
        assert result["error"]["code"] == 400

    def test_includes_meta_information(self, monkeypatch):
        from core.ai_serverless.handler import handle_completion

        def fake_delegate(prompt, task_type=None, capability=None,
                          return_attempts=None, provider=None):
            return {
                "response": "Test",
                "provider": "groq",
                "attempts": [{"provider": "groq", "ok": True}],
            }

        monkeypatch.setattr(
            "core.ai.ai_router.delegate", fake_delegate
        )

        result = handle_completion("Test")
        assert "_meta" in result
        assert result["_meta"]["provider"] == "groq"
        assert "elapsed_s" in result["_meta"]
        assert "task_type" in result["_meta"]


class TestErrorResponse:
    def test_error_response_shape(self):
        from core.ai_serverless.handler import _error_response

        err = _error_response("Something went wrong", status=502)
        assert err["object"] == "chat.completion"
        assert err["model"] == "error"
        assert err["choices"] == []
        assert err["error"]["message"] == "Something went wrong"
        assert err["error"]["code"] == 502


# ---------------------------------------------------------------------------
# Vercel handler tests
# ---------------------------------------------------------------------------


class TestVercelHandler:
    def test_get_health_returns_200(self):
        from core.ai_serverless.vercel_handler import handler as VercelHandler
        from io import BytesIO

        # Construct handler manually
        h = VercelHandler.__new__(VercelHandler)
        h.rfile = BytesIO(b"")
        h.wfile = BytesIO()
        h.headers = {}
        h.path = "/api/ai_completion"
        h.command = "GET"
        h.requestline = "GET /api/ai_completion HTTP/1.1"
        h.log_message = lambda fmt, *args: None

        sent_status = [None]
        sent_body = [b""]

        def cap_send(code):
            sent_status[0] = code
        def cap_write(data):
            sent_body[0] += data

        h.send_response = cap_send
        h.end_headers = lambda: None
        h.wfile.write = cap_write
        h.send_header = lambda k, v: None

        h.do_GET()
        assert sent_status[0] == 200
        result = json.loads(sent_body[0])
        assert result["status"] == "healthy"

    def test_get_unknown_path_returns_404(self):
        from core.ai_serverless.vercel_handler import handler as VercelHandler
        from io import BytesIO

        h = VercelHandler.__new__(VercelHandler)
        h.rfile = BytesIO(b"")
        h.wfile = BytesIO()
        h.headers = {}
        h.path = "/unknown"
        h.command = "GET"

        sent_status = [None]
        sent_body = [b""]

        def cap_send(code):
            sent_status[0] = code
        def cap_write(data):
            sent_body[0] += data

        h.send_response = cap_send
        h.end_headers = lambda: None
        h.wfile.write = cap_write
        h.send_header = lambda k, v: None

        h.do_GET()
        assert sent_status[0] == 404

    def test_post_completion_returns_result(self, monkeypatch):
        from core.ai_serverless.vercel_handler import handler as VercelHandler
        from io import BytesIO

        def fake_handle(prompt, model="auto", max_tokens=None,
                        task_type=None, temperature=0.7):
            return {
                "id": "test-1",
                "object": "chat.completion",
                "model": model,
                "choices": [
                    {"message": {"role": "assistant", "content": f"Echo: {prompt}"}}
                ],
            }

        monkeypatch.setattr(
            "core.ai_serverless.vercel_handler.handle_completion", fake_handle
        )

        h = VercelHandler.__new__(VercelHandler)
        body = json.dumps({"prompt": "Hello serverless"}).encode()
        h.rfile = BytesIO(body)
        h.wfile = BytesIO()
        h.headers = {"Content-Length": str(len(body))}
        h.path = "/api/ai_completion"
        h.command = "POST"

        sent_status = [None]
        sent_body = [b""]

        def cap_send(code):
            sent_status[0] = code
        def cap_write(data):
            sent_body[0] += data

        h.send_response = cap_send
        h.end_headers = lambda: None
        h.wfile.write = cap_write
        h.send_header = lambda k, v: None

        h.do_POST()
        assert sent_status[0] == 200
        result = json.loads(sent_body[0])
        assert "Echo: Hello serverless" in result["choices"][0]["message"]["content"]

    def test_post_missing_prompt_returns_400(self, monkeypatch):
        from core.ai_serverless.vercel_handler import handler as VercelHandler
        from io import BytesIO

        h = VercelHandler.__new__(VercelHandler)
        body = json.dumps({"model": "gemini"}).encode()
        h.rfile = BytesIO(body)
        h.wfile = BytesIO()
        h.headers = {"Content-Length": str(len(body))}
        h.path = "/api/ai_completion"
        h.command = "POST"

        sent_status = [None]
        sent_body = [b""]

        def cap_send(code):
            sent_status[0] = code
        def cap_write(data):
            sent_body[0] += data

        h.send_response = cap_send
        h.end_headers = lambda: None
        h.wfile.write = cap_write
        h.send_header = lambda k, v: None

        h.do_POST()
        assert sent_status[0] == 400

    def test_post_invalid_json_returns_400(self, monkeypatch):
        from core.ai_serverless.vercel_handler import handler as VercelHandler
        from io import BytesIO

        h = VercelHandler.__new__(VercelHandler)
        body = b"not valid json {{{"
        h.rfile = BytesIO(body)
        h.wfile = BytesIO()
        h.headers = {"Content-Length": str(len(body))}
        h.path = "/api/ai_completion"
        h.command = "POST"

        sent_status = [None]
        sent_body = [b""]

        def cap_send(code):
            sent_status[0] = code
        def cap_write(data):
            sent_body[0] += data

        h.send_response = cap_send
        h.end_headers = lambda: None
        h.wfile.write = cap_write
        h.send_header = lambda k, v: None

        h.do_POST()
        assert sent_status[0] == 400

    def test_cors_header_set(self, monkeypatch):
        from core.ai_serverless.vercel_handler import handler as VercelHandler
        from io import BytesIO

        h = VercelHandler.__new__(VercelHandler)
        body = json.dumps({"prompt": "test"}).encode()
        h.rfile = BytesIO(body)
        h.wfile = BytesIO()
        h.headers = {"Content-Length": str(len(body))}
        h.path = "/api/ai_completion"
        h.command = "POST"

        sent_headers = {}

        def cap_send(code):
            pass
        def cap_header(k, v):
            sent_headers[k] = v
        def cap_write(data):
            pass

        def fake_handle(**kw):
            return {"id": "x", "object": "chat.completion", "model": "auto",
                    "choices": [{"message": {"role": "assistant", "content": "ok"}}]}

        monkeypatch.setattr(
            "core.ai_serverless.vercel_handler.handle_completion", fake_handle
        )

        h.send_response = cap_send
        h.end_headers = lambda: None
        h.wfile.write = cap_write
        h.send_header = cap_header

        h.do_POST()
        assert sent_headers.get("Access-Control-Allow-Origin") == "*"
