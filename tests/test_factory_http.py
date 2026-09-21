"""Factory tools call the CT109 HTTP API, not SSH.

Root cause: the tools shelled out to ``ssh root@192.168.1.120``, but CT109 has
no authorized key for the orchestrator host, so every call failed and the
routes reported ``available: false`` (500 before that guard existed). CT109
serves a working dependency-free HTTP API on :4000:

  GET  /health      GET /api/toolchain   GET /api/projects
  GET  /api/projects/{id}
  POST /api/projects            (admin: X-Factory-Token)
  POST /api/projects/{id}/build (admin: X-Factory-Token)

The admin token lives in a file (never logged, never returned), GETs are open.
"""

from __future__ import annotations

import json
import urllib.error

import pytest

PROJECT = {
    "id": "abc123def4", "name": "Deerude", "package": "com.deerude.app",
    "template": "webview", "created_at": 1.0,
}
PROJECTS = {"projects": [PROJECT], "count": 1}


class _Resp:
    def __init__(self, payload):
        self._body = json.dumps(payload).encode()

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _patch(monkeypatch, routes):
    """Install a fake ``urllib.request.urlopen``; return the captured requests.

    ``routes`` maps ``(method, path)`` to either a JSON payload or an exception
    instance to raise (e.g. ``URLError``/``HTTPError``). Any unexpected call fails.
    """
    import core.kai_tools.builtin as builtin
    import urllib.request

    calls = []

    def fake_urlopen(req, timeout=None):
        method = req.get_method()
        path = req.full_url.replace(builtin.FACTORY_BASE_URL, "")
        calls.append(req)
        handler = routes.get((method, path))
        if handler is None:
            raise AssertionError(f"unexpected factory call {method} {path}")
        if isinstance(handler, Exception):
            raise handler
        return _Resp(handler)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(builtin, "_factory_status_cache", {"ts": 0.0, "data": None})
    return calls


@pytest.fixture()
def client():
    from fastapi.testclient import TestClient

    from core.api import app
    return TestClient(app)


# ── status ──────────────────────────────────────────────────────────────────

def test_status_calls_http_and_reports_real_projects(monkeypatch):
    from core.kai_tools import builtin

    calls = _patch(monkeypatch, {
        ("GET", "/health"): {"ok": True, "service": "kai-android-factory",
                             "toolchain_ready": False},
        ("GET", "/api/toolchain"): {"java": None, "gradle": None,
                                    "android_sdk": None, "ready": False},
        ("GET", "/api/projects"): PROJECTS,
    })

    out = builtin.factory_status()
    assert out["available"] is True
    assert out["host"] == "192.168.1.120"
    assert out["service"] == "kai-android-factory"
    assert out["project_count"] == 1
    assert out["projects"][0]["id"] == "abc123def4"
    assert {c.full_url for c in calls} == {
        "http://192.168.1.120:4000/health",
        "http://192.168.1.120:4000/api/toolchain",
        "http://192.168.1.120:4000/api/projects",
    }


def test_status_degrades_when_api_unreachable(monkeypatch):
    from core.kai_tools import builtin

    _patch(monkeypatch, {("GET", "/health"): urllib.error.URLError("connection refused")})
    out = builtin.factory_status()
    assert out["available"] is False
    assert out["host"] == "192.168.1.120"
    assert "URLError" in out["error"]


# ── reports / project listing ───────────────────────────────────────────────

def test_reports_lists_projects_with_builds(monkeypatch):
    from core.kai_tools import builtin

    _patch(monkeypatch, {
        ("GET", "/api/projects"): PROJECTS,
        ("GET", "/api/projects/abc123def4"): {
            **PROJECT, "files": ["settings.gradle.kts"],
            "builds": [{"status": "toolchain_missing"}],
        },
    })
    out = builtin.factory_reports(limit=3)
    assert out["available"] is True
    assert out["count"] == 1
    assert out["reports"][0]["builds"][0]["status"] == "toolchain_missing"


def test_reports_degrades_when_api_unreachable(monkeypatch):
    from core.kai_tools import builtin

    _patch(monkeypatch, {("GET", "/api/projects"): urllib.error.URLError("down")})
    out = builtin.factory_reports(limit=3)
    assert out["available"] is False
    assert out["reports"] == []


# ── mutating actions: admin token ───────────────────────────────────────────

def _token(monkeypatch, builtin, tmp_path):
    tok = tmp_path / "factory_token"
    tok.write_text("s3cret-token\n")
    monkeypatch.setattr(builtin, "FACTORY_TOKEN_FILE", str(tok))


def test_build_posts_with_admin_token(monkeypatch, tmp_path):
    import core.kai_tools.builtin as builtin

    _token(monkeypatch, builtin, tmp_path)
    calls = _patch(monkeypatch, {
        ("POST", "/api/projects/abc123def4/build"): {"status": "toolchain_missing"},
    })

    out = builtin.factory_build("abc123def4")
    assert out["ok"] is False
    assert out["status"] == "toolchain_missing"
    req = calls[-1]
    assert req.get_method() == "POST"
    assert "s3cret-token" in req.headers.values()
    assert "s3cret-token" not in json.dumps(out)


def test_build_resolves_a_project_name(monkeypatch, tmp_path):
    import core.kai_tools.builtin as builtin

    _token(monkeypatch, builtin, tmp_path)
    _patch(monkeypatch, {
        ("GET", "/api/projects"): PROJECTS,
        ("POST", "/api/projects/abc123def4/build"): {"status": "success"},
    })
    out = builtin.factory_build("Deerude")
    assert out["ok"] is True and out["project"] == "abc123def4"


def test_build_without_token_file_fails_honestly(monkeypatch, tmp_path):
    import core.kai_tools.builtin as builtin

    monkeypatch.setattr(builtin, "FACTORY_TOKEN_FILE", str(tmp_path / "missing"))
    _patch(monkeypatch, {})  # any HTTP call would be unexpected

    out = builtin.factory_build("abc123def4")
    assert out["ok"] is False
    assert "token" in out["error"]


def test_scaffold_posts_new_project_with_admin_token(monkeypatch, tmp_path):
    import core.kai_tools.builtin as builtin

    _token(monkeypatch, builtin, tmp_path)
    calls = _patch(monkeypatch, {
        ("POST", "/api/projects"): {"ok": True, "id": "new1234", "path": "/p/new1234"},
    })
    out = builtin.factory_scaffold(name="X", package="com.x.y", template="empty")
    assert out["ok"] is True and out["id"] == "new1234"
    assert "s3cret-token" in calls[-1].headers.values()
    assert "s3cret-token" not in json.dumps(out)


# ── HTTP routes surface real data (not available:false) ─────────────────────

def test_status_route_returns_real_data(client, monkeypatch):
    _patch(monkeypatch, {
        ("GET", "/health"): {"ok": True, "service": "kai-android-factory",
                             "toolchain_ready": False},
        ("GET", "/api/toolchain"): {"ready": False},
        ("GET", "/api/projects"): PROJECTS,
    })
    r = client.get("/kai/tools/factory/status")
    assert r.status_code == 200
    body = r.json()
    assert body["available"] is True
    assert body["project_count"] == 1


def test_reports_route_returns_real_data(client, monkeypatch):
    _patch(monkeypatch, {
        ("GET", "/api/projects"): PROJECTS,
        ("GET", "/api/projects/abc123def4"): {**PROJECT, "builds": []},
    })
    r = client.get("/kai/tools/factory/reports?limit=2")
    assert r.status_code == 200
    body = r.json()
    assert body["available"] is True and body["count"] == 1
