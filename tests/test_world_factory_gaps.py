"""Tests for two roadmap gaps:

  * the world model had no Command Center surface (aggregate read endpoint);
  * ``/kai/tools/factory/{status,reports}`` returned HTTP 500 whenever the
    factory host was unreachable (stale FACTORY_HOST + unhandled RuntimeError).
"""

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    from core.api import app
    return TestClient(app)


def _boom(*args, **kwargs):
    raise RuntimeError("factory GET /health: HTTP 503 unavailable")


# ── world model endpoint ────────────────────────────────────────────────────

def test_world_endpoint_lists_snapshot(client, monkeypatch):
    import core.world_model as wm

    monkeypatch.setattr(wm, "get_snapshot", lambda: {
        "schema_version": 1,
        "updated_at": "2026-09-21T00:00:00Z",
        "entities": {"host:pve-b": {"type": "host", "label": "PVE-B", "status": "ok"}},
        "edges": [{"src": "host:pve-b", "dst": "ct:109", "kind": "hosts"}],
        "changes_since_previous": [{"entity": "host:pve-b", "from": "down", "to": "ok"}],
        "counts": {"entities": 1, "by_type": {"host": 1}},
    })

    r = client.get("/kai/world")
    assert r.status_code == 200
    body = r.json()
    assert body["updated_at"] == "2026-09-21T00:00:00Z"
    assert body["entities"][0]["id"] == "host:pve-b"
    assert body["entities"][0]["status"] == "ok"
    assert body["edges"][0]["kind"] == "hosts"
    assert body["changes"][0]["to"] == "ok"
    assert body["counts"]["entities"] == 1


def test_world_model_get_snapshot_builds_when_empty(monkeypatch, tmp_path):
    import core.world_model as wm

    monkeypatch.setattr(wm, "WORLD_PATH", tmp_path / "world_model.json")
    monkeypatch.setattr(wm, "build_snapshot", lambda: {"entities": {"x": {}}, "built": True})
    snap = wm.get_snapshot()
    assert snap.get("built") is True


# ── factory degradation ─────────────────────────────────────────────────────

def test_factory_host_is_ct109_ip():
    # CT109 (kai-android-factory) lives on .120; .119 was a stale constant.
    from core.kai_tools.builtin import FACTORY_HOST
    assert FACTORY_HOST == "192.168.1.120"


def test_factory_status_degrades_instead_of_raising(monkeypatch):
    import core.kai_tools.builtin as builtin

    monkeypatch.setattr(builtin, "_factory_request", _boom)
    monkeypatch.setattr(builtin, "_factory_status_cache", {"ts": 0.0, "data": None})

    out = builtin.factory_status()
    assert out["available"] is False
    assert "503" in out["error"]


def test_factory_reports_degrades_instead_of_raising(monkeypatch):
    import core.kai_tools.builtin as builtin

    monkeypatch.setattr(builtin, "_factory_request", _boom)
    out = builtin.factory_reports(limit=3)
    assert out["available"] is False
    assert out["reports"] == []


def test_factory_status_route_is_200_not_500(client, monkeypatch):
    import core.kai_tools.builtin as builtin

    monkeypatch.setattr(builtin, "_factory_request", _boom)
    monkeypatch.setattr(builtin, "_factory_status_cache", {"ts": 0.0, "data": None})

    r = client.get("/kai/tools/factory/status")
    assert r.status_code == 200
    assert r.json()["available"] is False


def test_factory_reports_route_is_200_not_500(client, monkeypatch):
    import core.kai_tools.builtin as builtin

    monkeypatch.setattr(builtin, "_factory_request", _boom)
    r = client.get("/kai/tools/factory/reports?limit=2")
    assert r.status_code == 200
    assert r.json()["available"] is False
