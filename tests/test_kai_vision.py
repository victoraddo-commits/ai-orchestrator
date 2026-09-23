"""Local-only vision: kai.vision must use the VM112 CPU vision model.

Owner directive: zero third-party providers. These tests pin the local
llama.cpp endpoint (Qwen2.5-VL-3B on VM112 ``:5002``) and prove the tool
never reaches a cloud vision API and never crashes on failure.
"""
from __future__ import annotations

import pytest

import core.kai_tools.builtin as builtin


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload
        self.status_code = 200
        self.text = "ok"

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def test_vision_ask_fails_closed_when_local_server_unreachable(monkeypatch):
    monkeypatch.setattr(builtin, "_vision_available", lambda timeout=3.0: False)

    with pytest.raises(RuntimeError, match="local vision model unavailable"):
        builtin._vision_ask(b"\x89PNG\r\n\x1a\n" + b"0" * 200, "what is this?")


def test_vision_ask_posts_to_the_local_openai_endpoint(monkeypatch):
    monkeypatch.setattr(builtin, "_vision_available", lambda timeout=3.0: True)

    captured = {}

    def fake_post(url, json=None, timeout=None):
        captured["url"] = url
        captured["json"] = json
        captured["timeout"] = timeout
        return _FakeResponse(
            {"choices": [{"message": {"content": "A red circle on white."}}]}
        )

    monkeypatch.setattr("requests.post", fake_post)

    result = builtin._vision_ask(b"\x89PNG\r\n\x1a\n" + b"0" * 200, "describe")

    assert captured["url"] == f"{builtin.VISION_URL}/v1/chat/completions"
    assert "192.168.1.242:5002" in captured["url"]  # local VM112, not cloud

    content = captured["json"]["messages"][0]["content"]
    kinds = {part["type"] for part in content}
    assert kinds == {"text", "image_url"}
    image_part = next(p for p in content if p["type"] == "image_url")
    assert image_part["image_url"]["url"].startswith("data:image/png;base64,")

    assert result["description"] == "A red circle on white."
    assert result["endpoint"] == builtin.VISION_URL
    assert result["model"] == "Qwen2.5-VL-3B-Instruct"


def test_vision_ask_wraps_call_errors_without_crashing(monkeypatch):
    monkeypatch.setattr(builtin, "_vision_available", lambda timeout=3.0: True)

    def boom(url, json=None, timeout=None):
        raise ConnectionError("connection refused")

    monkeypatch.setattr("requests.post", boom)

    with pytest.raises(RuntimeError, match="local vision model call failed"):
        builtin._vision_ask(b"\x89PNG\r\n\x1a\n" + b"0" * 200, "describe")


def test_image_mime_detects_png_and_jpeg():
    assert builtin._image_mime(b"\x89PNG\r\n\x1a\n\x00") == "image/png"
    assert builtin._image_mime(b"\xff\xd8\xff\xe0\x00") == "image/jpeg"
    assert builtin._image_mime(b"random") == "image/png"


def test_vision_module_never_targets_a_cloud_vision_api():
    """Static guard: the local vision path names no third-party endpoint."""
    import inspect

    source = inspect.getsource(builtin._vision_ask)
    for forbidden in ("googleapis", "generativelanguage", "gemini",
                      "openai.com", "api.anthropic"):
        assert forbidden not in source.lower(), forbidden
    assert "192.168.1.242" in builtin.VISION_URL
