"""Unit tests for real image generation (mocked HTTP; no real API/DB calls)."""
from __future__ import annotations

import base64
import hashlib
import json

import pytest

from core.media_factory import assets, config, routes

PNG = b"\x89PNG\r\n\x1a\n" + b"fake-png-payload-0123456789"


class FakeResp:
    def __init__(self, status_code=200, payload=None, content=b"",
                 headers=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.content = content
        self.headers = headers or {}
        self.text = text

    def json(self):
        if self._payload is None:
            raise ValueError("no json body")
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise assets.requests.HTTPError("http error")


@pytest.fixture
def media_root(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "MEDIA_ROOT", str(tmp_path))
    return tmp_path


@pytest.fixture
def db_stub(monkeypatch):
    calls = {"inserted": []}

    def fake_insert(sql, params=(), database=None):
        calls["inserted"].append(params)
        return {
            "id": 42,
            "content_id": params[0],
            "kind": params[1],
            "uri": params[2],
            "sha256": params[3],
            "bytes": params[4],
            "status": params[6],
        }

    monkeypatch.setattr(assets.db, "insert_returning", fake_insert)
    monkeypatch.setattr(assets.db, "query_one", lambda *a, **k: None)
    monkeypatch.setattr(assets.db, "audit", lambda *a, **k: {"ok": True, "id": 1})
    monkeypatch.setattr(assets.db, "record_event", lambda *a, **k: 1)
    return calls


def _capture_post(monkeypatch, response):
    seen = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        seen["url"] = url
        seen["headers"] = headers
        seen["json"] = json
        seen["timeout"] = timeout
        return response

    monkeypatch.setattr(assets.requests, "post", fake_post)
    return seen


def test_b64_json_image_is_saved_and_registered(media_root, db_stub, monkeypatch):
    monkeypatch.setattr(assets, "resolve_openai_key", lambda: "test-key")
    b64 = base64.b64encode(PNG).decode()
    seen = _capture_post(monkeypatch, FakeResp(payload={"data": [{"b64_json": b64}]}))

    result = assets.generate_image("a flat cartoon mascot")

    assert result["status"] == config.STATUS_VERIFIED
    assert result["asset_id"] == 42
    assert result["count"] == 1
    # Request contract
    assert seen["url"].endswith("/v1/images/generations")
    assert seen["headers"]["Authorization"] == "Bearer test-key"
    assert seen["json"]["prompt"] == "a flat cartoon mascot"
    assert seen["json"]["model"] == config.DEFAULT_IMAGE_MODEL
    assert "response_format" not in seen["json"]  # gpt-image returns b64_json
    # File + sidecar
    digest = hashlib.sha256(PNG).hexdigest()
    path = media_root / "assets" / f"{digest}.png"
    assert path.is_file()
    assert path.read_bytes() == PNG
    sidecar = media_root / "assets" / f"{digest}.json"
    meta = json.loads(sidecar.read_text())
    assert meta["sha256"] == digest
    assert meta["bytes"] == len(PNG)
    assert meta["prompt"] == "a flat cartoon mascot"
    assert meta["model"] == config.DEFAULT_IMAGE_MODEL
    # DB row recorded kind=image with hash/bytes
    params = db_stub["inserted"][0]
    assert params[1] == "image"
    assert params[3] == digest
    assert params[4] == len(PNG)
    assert result["files"][0]["sha256"] == digest


def test_url_response_is_downloaded(media_root, db_stub, monkeypatch):
    monkeypatch.setattr(assets, "resolve_openai_key", lambda: "test-key")
    _capture_post(monkeypatch, FakeResp(payload={"data": [{"url": "https://img.test/a"}]}))
    seen_get = {}

    def fake_get(url, timeout=None):
        seen_get["url"] = url
        return FakeResp(content=PNG, headers={"Content-Type": "image/png"})

    monkeypatch.setattr(assets.requests, "get", fake_get)

    result = assets.generate_image("prompt", size="1024x1024")

    assert result["status"] == config.STATUS_VERIFIED
    assert seen_get["url"] == "https://img.test/a"
    assert result["files"][0]["sha256"] == hashlib.sha256(PNG).hexdigest()


def test_missing_key_is_blocked_without_calling_provider(media_root, db_stub, monkeypatch):
    monkeypatch.setattr(assets, "resolve_openai_key", lambda: None)
    called = {"post": False}

    def fake_post(*a, **k):
        called["post"] = True
        raise AssertionError("provider must not be called without a key")

    monkeypatch.setattr(assets.requests, "post", fake_post)

    result = assets.generate_image("anything")

    assert result["status"] == config.STATUS_BLOCKED
    assert result["blocked_reason"] == "no image provider key (gemini/openai)"
    assert result["assets"] == []
    assert called["post"] is False


def test_provider_http_error_is_failed_and_writes_nothing(media_root, db_stub, monkeypatch):
    monkeypatch.setattr(assets, "resolve_openai_key", lambda: "test-key")
    _capture_post(monkeypatch, FakeResp(
        status_code=400, payload={"error": {"message": "invalid size"}}))

    result = assets.generate_image("prompt")

    assert result["status"] == config.STATUS_FAILED
    assert "provider HTTP 400" in result["reason"]
    assert result["assets"] == []
    assert not (media_root / "assets").exists()


def test_undecodable_payload_is_failed(media_root, db_stub, monkeypatch):
    monkeypatch.setattr(assets, "resolve_openai_key", lambda: "test-key")
    _capture_post(monkeypatch, FakeResp(payload={"data": [{"b64_json": base64.b64encode(
        b"not an image at all").decode()}]}))

    result = assets.generate_image("prompt")

    assert result["status"] == config.STATUS_FAILED
    assert "registration failed" in result["reason"] or "save" in result["reason"]


def test_video_generate_still_blocked_with_accurate_reason(media_root, db_stub, monkeypatch):
    monkeypatch.setattr(assets, "resolve_openai_key", lambda: None)

    result = assets.generate(None, "video", prompt="a clip")

    assert result["status"] == config.STATUS_BLOCKED
    assert "video generation" in result["blocked_reason"]
    assert "image generation available" in config.BLOCKED_ASSET_GEN


def test_image_capability_partial_then_verified(monkeypatch):
    monkeypatch.setattr(assets, "resolve_openai_key", lambda: "test-key")
    monkeypatch.setattr(assets.db, "query_one", lambda *a, **k: {"n": 0})
    partial = assets.image_capability()
    assert partial["status"] == config.STATUS_PARTIALLY_VERIFIED
    assert partial["blocked_reason"] == config.BLOCKED_ASSET_VIDEO

    monkeypatch.setattr(assets.db, "query_one", lambda *a, **k: {"n": 3})
    verified = assets.image_capability()
    assert verified["status"] == config.STATUS_VERIFIED
    assert verified["images_produced"] == 3


def test_image_capability_blocked_without_key(monkeypatch):
    monkeypatch.setattr(assets, "resolve_openai_key", lambda: None)
    cap = assets.image_capability()
    assert cap["status"] == config.STATUS_BLOCKED
    assert cap["blocked_reason"] == "no image provider key"
    assert cap["providers"] == {"gemini": False, "openai": False}


def test_generate_asset_route_wires_through(monkeypatch):
    monkeypatch.setattr(routes.assets_mod, "generate_image",
                        lambda *a, **k: {"status": config.STATUS_BLOCKED,
                                         "asset_id": None, "assets": []})
    monkeypatch.setattr(routes.db, "audit", lambda *a, **k: {"ok": True, "id": 1})
    result = routes.generate_asset(
        routes.AssetGenerateRequest(prompt="hello"), operator="tester")
    assert result["status"] == config.STATUS_BLOCKED


def test_list_assets_route_paginates(monkeypatch):
    monkeypatch.setattr(routes.assets_mod, "latest", lambda limit, offset: [{"id": 1}])
    result = routes.list_assets(limit=5, offset=0)
    assert result == {"data": [{"id": 1}], "count": 1, "limit": 5, "offset": 0}


# ── Gemini provider (primary) ──────────────────────────────────────────────
JPEG = b"\xff\xd8\xff" + b"fake-jpeg-payload-0123456789"


def _gemini_payload(data=PNG, mime="image/png", camel=True):
    inline_key = "inlineData" if camel else "inline_data"
    mime_key = "mimeType" if camel else "mime_type"
    return {"candidates": [{"content": {"parts": [
        {inline_key: {mime_key: mime,
                      "data": base64.b64encode(data).decode()}}
    ]}}]}


def _capture_posts(monkeypatch, handler):
    seen = []

    def fake_post(url, **kwargs):
        seen.append({"url": url, **kwargs})
        return handler(url, kwargs)

    monkeypatch.setattr(assets.requests, "post", fake_post)
    return seen


def test_gemini_inline_image_is_saved_and_registered(media_root, db_stub, monkeypatch):
    monkeypatch.setattr(assets, "resolve_gemini_key", lambda: "gkey")
    monkeypatch.setattr(assets, "resolve_openai_key", lambda: "okey")
    seen = _capture_posts(monkeypatch,
                          lambda url, kw: FakeResp(payload=_gemini_payload()))

    result = assets.generate_image("a friendly robot")

    assert result["status"] == config.STATUS_VERIFIED
    assert result["provider"] == "gemini"
    assert result["model"] == config.DEFAULT_GEMINI_IMAGE_MODEL
    assert result["asset_id"] == 42
    # Request contract: Gemini called, OpenAI never attempted.
    assert len(seen) == 1
    assert seen[0]["url"].endswith(
        f"/models/{config.DEFAULT_GEMINI_IMAGE_MODEL}:generateContent")
    assert seen[0]["params"] == {"key": "gkey"}
    assert seen[0]["json"] == {"contents": [{"parts": [{"text": "a friendly robot"}]}]}
    # File + sidecar carry the gemini provenance.
    digest = hashlib.sha256(PNG).hexdigest()
    assert (media_root / "assets" / f"{digest}.png").is_file()
    meta = json.loads((media_root / "assets" / f"{digest}.json").read_text())
    assert meta["provider"] == "gemini"
    assert meta["model"] == config.DEFAULT_GEMINI_IMAGE_MODEL
    assert meta["mime"] == "image/png"
    assert result["files"][0]["sha256"] == digest


def test_generate_image_falls_back_to_openai_when_gemini_fails(media_root, db_stub,
                                                               monkeypatch):
    monkeypatch.setattr(assets, "resolve_gemini_key", lambda: "gkey")
    monkeypatch.setattr(assets, "resolve_openai_key", lambda: "okey")

    def handler(url, kwargs):
        if "generativelanguage" in url:
            return FakeResp(status_code=500, payload={"error": {"message": "boom"}})
        return FakeResp(payload={"data": [{"b64_json": base64.b64encode(PNG).decode()}]})

    seen = _capture_posts(monkeypatch, handler)

    result = assets.generate_image("fallback please")

    assert result["status"] == config.STATUS_VERIFIED
    assert result["provider"] == "openai"
    assert len(seen) == 2
    assert "generativelanguage" in seen[0]["url"]
    assert seen[1]["url"].endswith("/images/generations")
    assert seen[1]["headers"]["Authorization"] == "Bearer okey"


def test_generate_image_failed_when_all_providers_fail(media_root, db_stub, monkeypatch):
    monkeypatch.setattr(assets, "resolve_gemini_key", lambda: "gkey")
    monkeypatch.setattr(assets, "resolve_openai_key", lambda: "okey")
    _capture_posts(monkeypatch, lambda url, kw: FakeResp(
        status_code=503, payload={"error": {"message": "down"}}))

    result = assets.generate_image("nope")

    assert result["status"] == config.STATUS_FAILED
    assert "all providers failed" in result["reason"]
    assert result["assets"] == []
    assert not (media_root / "assets").exists()


def test_generate_image_gemini_only_does_not_fall_back(media_root, db_stub, monkeypatch):
    monkeypatch.setattr(assets, "resolve_gemini_key", lambda: "gkey")
    seen = _capture_posts(monkeypatch, lambda url, kw: FakeResp(
        status_code=500, payload={"error": {"message": "boom"}}))

    result = assets.generate_image("only gemini")

    assert result["status"] == config.STATUS_FAILED
    assert result["provider"] == "gemini"
    assert len(seen) == 1


def test_gemini_http_error_is_failed_and_writes_nothing(media_root, db_stub, monkeypatch):
    monkeypatch.setattr(assets, "resolve_gemini_key", lambda: "gkey")
    _capture_posts(monkeypatch, lambda url, kw: FakeResp(
        status_code=400, payload={"error": {"message": "bad model"}}))

    result = assets.generate_image_gemini("prompt")

    assert result["status"] == config.STATUS_FAILED
    assert result["provider"] == "gemini"
    assert "provider HTTP 400" in result["reason"]
    assert not (media_root / "assets").exists()


def test_gemini_missing_inline_data_is_failed(media_root, db_stub, monkeypatch):
    monkeypatch.setattr(assets, "resolve_gemini_key", lambda: "gkey")
    _capture_posts(monkeypatch, lambda url, kw: FakeResp(payload={
        "candidates": [{"content": {"parts": [{"text": "no image here"}]}}]}))

    result = assets.generate_image_gemini("prompt")

    assert result["status"] == config.STATUS_FAILED
    assert "no image data" in result["reason"]
    assert not (media_root / "assets").exists()


def test_gemini_snake_case_inline_data_parsed(media_root, db_stub, monkeypatch):
    monkeypatch.setattr(assets, "resolve_gemini_key", lambda: "gkey")
    _capture_posts(monkeypatch, lambda url, kw: FakeResp(
        payload=_gemini_payload(data=JPEG, mime="image/jpeg", camel=False)))

    result = assets.generate_image_gemini("a photo")

    assert result["status"] == config.STATUS_VERIFIED
    digest = hashlib.sha256(JPEG).hexdigest()
    assert (media_root / "assets" / f"{digest}.jpg").is_file()
    assert result["files"][0]["mime"] == "image/jpeg"


def test_gemini_missing_key_is_blocked(media_root, db_stub, monkeypatch):
    result = assets.generate_image_gemini("anything")
    assert result["status"] == config.STATUS_BLOCKED
    assert result["provider"] == "gemini"
    assert result["blocked_reason"] == "no gemini key"


def test_image_capability_reports_configured_providers(monkeypatch):
    monkeypatch.setattr(assets, "resolve_gemini_key", lambda: "gkey")
    monkeypatch.setattr(assets, "resolve_openai_key", lambda: "okey")
    monkeypatch.setattr(assets.db, "query_one", lambda *a, **k: {"n": 0})

    cap = assets.image_capability()

    assert cap["status"] == config.STATUS_PARTIALLY_VERIFIED
    assert cap["provider"] == "gemini"
    assert cap["providers"] == {"gemini": True, "openai": True}


def test_status_asset_gen_verified_when_image_exists(monkeypatch):
    from core.media_factory import status as status_mod

    monkeypatch.setattr(assets, "image_asset_count", lambda: 1)
    monkeypatch.setattr(assets, "generation_capability", lambda: {
        "status": config.STATUS_VERIFIED,
        "ffmpeg": True, "ffprobe": True,
        "blocked_reason": config.BLOCKED_ASSET_GEN,
        "image": {"status": config.STATUS_VERIFIED, "provider": "gemini",
                  "model": config.DEFAULT_GEMINI_IMAGE_MODEL,
                  "providers": {"gemini": True, "openai": False},
                  "detail": "x", "blocked_reason": config.BLOCKED_ASSET_VIDEO},
    })

    cap = status_mod.asset_gen_capability()

    assert cap.status == config.STATUS_VERIFIED
    assert "1 image asset(s)" in cap.detail
    assert cap.evidence["provider"] == "gemini"


def test_status_asset_gen_partial_when_provider_configured(monkeypatch):
    from core.media_factory import status as status_mod

    monkeypatch.setattr(assets, "image_asset_count", lambda: 0)
    monkeypatch.setattr(assets, "generation_capability", lambda: {
        "status": config.STATUS_PARTIALLY_VERIFIED,
        "ffmpeg": True, "ffprobe": True,
        "blocked_reason": config.BLOCKED_ASSET_GEN,
        "image": {"status": config.STATUS_PARTIALLY_VERIFIED, "provider": "gemini",
                  "model": config.DEFAULT_GEMINI_IMAGE_MODEL,
                  "providers": {"gemini": True, "openai": False},
                  "detail": "image provider configured (gemini); no image generated yet",
                  "blocked_reason": config.BLOCKED_ASSET_VIDEO},
    })

    cap = status_mod.asset_gen_capability()

    assert cap.status == config.STATUS_PARTIALLY_VERIFIED
    assert "gemini" in cap.detail
    assert cap.verified is False
