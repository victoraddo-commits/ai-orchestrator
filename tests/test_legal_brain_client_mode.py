"""The legal-brain client must forward the retrieval ``mode`` to /search.

The four modes (phrase/and/or/like) drive progressive retrieval for Juris Kai
grounding; the client is the only path from the orchestrator to the service.
"""
import json
import urllib.parse

import core.legal_brain_client as lb


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return json.dumps(self._payload).encode()


def _capture(monkeypatch, payload):
    captured = {}

    def fake_urlopen(url, timeout=None):
        captured["url"] = getattr(url, "full_url", url)
        return _FakeResp(payload)

    monkeypatch.setattr(lb.urllib.request, "urlopen", fake_urlopen)
    return captured


def _params(url):
    return urllib.parse.parse_qs(urllib.parse.urlparse(url).query)


def test_search_forwards_mode(monkeypatch):
    captured = _capture(monkeypatch, {"results": [{"title": "x"}]})
    out = lb.search("bail application", limit=3, mode="phrase")
    assert out == [{"title": "x"}]
    params = _params(captured["url"])
    assert params["mode"] == ["phrase"]
    assert params["q"] == ["bail application"]
    assert params["limit"] == ["3"]


def test_search_defaults_mode_or(monkeypatch):
    captured = _capture(monkeypatch, {"results": []})
    lb.search("bail")
    assert _params(captured["url"])["mode"] == ["or"]


def test_search_forwards_hybrid_mode(monkeypatch):
    captured = _capture(monkeypatch, {"results": [{"title": "x"}]})
    out = lb.search("rape", limit=3, mode="hybrid")
    assert out == [{"title": "x"}]
    assert _params(captured["url"])["mode"] == ["hybrid"]


def test_search_hybrid_helper_sets_mode(monkeypatch):
    captured = _capture(monkeypatch, {"results": []})
    lb.search_hybrid("human rights", limit=3)
    params = _params(captured["url"])
    assert params["mode"] == ["hybrid"]
    assert params["q"] == ["human rights"]
    assert "hybrid" in lb.SEARCH_MODES


# ── Phase 3 T3/T4: relations + temporal status helpers ───────────────────

def test_status_helper_hits_status_endpoint(monkeypatch):
    captured = _capture(monkeypatch, {"document_id": 853,
                                      "status": "AMENDED"})
    out = lb.status(853)
    assert out["status"] == "AMENDED"
    assert captured["url"].endswith("/status/853")


def test_statuses_helper_bulk_query(monkeypatch):
    captured = _capture(monkeypatch, {"statuses": [{"document_id": 1}]})
    out = lb.statuses([1, 2, 3])
    assert out == [{"document_id": 1}]
    assert "/status?ids=1,2,3" in captured["url"]


def test_relations_helper_hits_relations_endpoint(monkeypatch):
    captured = _capture(monkeypatch, {"document_id": 5, "amended_by": []})
    out = lb.relations(5)
    assert out["document_id"] == 5
    assert captured["url"].endswith("/relations/5")


def test_document_authority_combines_relations_and_status(monkeypatch):
    payload = {"document_id": 9, "status": "REPEALED"}

    def fake_get_auth(path, timeout=8):
        return payload

    monkeypatch.setattr(lb, "_get_auth", fake_get_auth)
    out = lb.document_authority(9)
    assert out["document_id"] == 9
    assert out["relations"] == payload
    assert out["status"] == payload


def test_legal_health_helper_hits_endpoint(monkeypatch):
    captured = _capture(monkeypatch, {"docs": 5, "unknown_status": 2})
    out = lb.legal_health()
    assert out["docs"] == 5
    assert captured["url"].endswith("/legal/health")


def test_legal_health_helper_forwards_stale_days(monkeypatch):
    captured = _capture(monkeypatch, {"docs": 5})
    lb.legal_health(stale_days=30)
    assert "/legal/health?stale_days=30" in captured["url"]


def test_document_authority_reports_helper_errors(monkeypatch):
    def boom(path, timeout=8):
        raise RuntimeError("brain down")

    monkeypatch.setattr(lb, "_get_auth", boom)
    out = lb.document_authority(9)
    assert out["document_id"] == 9
    assert "relations_error" in out and "status_error" in out

