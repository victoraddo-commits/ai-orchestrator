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
        captured["url"] = url
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
