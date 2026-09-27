import sys
import urllib.error
from pathlib import Path

import pytest
import requests

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.juris_kai import streaming  # noqa: E402


class _FakeURLOK:
    """Stands in for urllib.request.urlopen returning an OpenAI-shaped reply."""

    def __init__(self, content="Judge: the conviction stands."):
        self._body = {
            "choices": [{"message": {"role": "assistant", "content": content}}],
        }

    def read(self):
        import json
        return json.dumps(self._body).encode()


def _enable(monkeypatch):
    monkeypatch.setattr(streaming, "FAILOVER_ENABLED", True)


def _break_primary(monkeypatch):
    """Primary Ollama endpoint fails at connect time, for any attempt model."""

    def boom(*args, **kwargs):
        raise requests.exceptions.ConnectionError(
            "ConnectionError: connection refused")

    monkeypatch.setattr(streaming, "_raw_stream_chat", boom)


def _primary_raises_gen(exc):
    def boom(*args, **kwargs):
        raise exc
        yield  # pragma: no cover - pragma to keep it a generator
    return boom


def test_judge_failover_returns_cpu_answer_with_label(monkeypatch):
    _enable(monkeypatch)
    monkeypatch.setattr(
        streaming, "_raw_stream_chat",
        _primary_raises_gen(requests.exceptions.ConnectionError(
            "connection refused")))
    monkeypatch.setattr(streaming.urllib.request, "urlopen",
                        lambda req, timeout: _FakeURLOK())
    chunks = list(streaming.stream_chat(
        "Judge this.", task_type="judge", model=streaming.DEFAULT_MODEL,
        guard=False, query="q"))
    text = "".join(chunks)
    assert "failover:llama_cpu" in text
    assert "the conviction stands" in text


def test_judge_failover_also_failing_degrades_without_raising(monkeypatch):
    _enable(monkeypatch)
    monkeypatch.setattr(
        streaming, "_raw_stream_chat",
        _primary_raises_gen(requests.exceptions.ConnectionError(
            "connection refused")))
    def boom(req, timeout):
        raise urllib.error.URLError("vm112 also down")
    monkeypatch.setattr(streaming.urllib.request, "urlopen", boom)
    # Failover failed -> degrade silently: empty stream, no exception, and
    # no fabricated answer (never guess).
    chunks = list(streaming.stream_chat(
        "Judge this.", task_type="judge", model=streaming.DEFAULT_MODEL,
        guard=False, query="q"))
    assert chunks == []


def test_failover_disabled_by_default(monkeypatch):
    monkeypatch.setattr(streaming, "FAILOVER_ENABLED", False)
    monkeypatch.setattr(
        streaming, "_raw_stream_chat",
        _primary_raises_gen(requests.exceptions.ConnectionError(
            "connection refused")))
    urlopen = lambda req, timeout: (_ for _ in ()).throw(
        AssertionError("failover must not be called when disabled"))
    monkeypatch.setattr(streaming.urllib.request, "urlopen", urlopen)
    with pytest.raises(Exception):
        list(streaming.stream_chat(
            "Judge this.", task_type="judge", model=streaming.DEFAULT_MODEL,
            guard=False, query="q"))


def test_failover_not_used_for_chat_tasks(monkeypatch):
    _enable(monkeypatch)
    monkeypatch.setattr(
        streaming, "_raw_stream_chat",
        _primary_raises_gen(requests.exceptions.ConnectionError(
            "connection refused")))
    urlopen = lambda req, timeout: (_ for _ in ()).throw(
        AssertionError("failover must not run for plain chat tasks"))
    monkeypatch.setattr(streaming.urllib.request, "urlopen", urlopen)
    with pytest.raises(Exception):
        list(streaming.stream_chat(
            "Chat please.", task_type="governance",
            model=streaming.DEFAULT_MODEL, guard=False, query="q"))


def test_failover_allowed_for_quick_legal_research_only(monkeypatch):
    _enable(monkeypatch)
    monkeypatch.setattr(
        streaming, "_raw_stream_chat",
        _primary_raises_gen(requests.exceptions.ConnectionError(
            "connection refused")))
    monkeypatch.setattr(streaming.urllib.request, "urlopen",
                        lambda req, timeout: _FakeURLOK("quick answer"))
    chunks = list(streaming.stream_chat(
        "Research this.", task_type="legal_research", deep=False,
        model=streaming.DEFAULT_MODEL, guard=False, query="q"))
    assert "failover:llama_cpu" in "".join(chunks)
    # deep legal_research must NOT use failover
    monkeypatch.setattr(
        streaming.urllib.request, "urlopen",
        lambda req, timeout: (_ for _ in ()).throw(
            AssertionError("deep legal_research must not failover")))
    with pytest.raises(Exception):
        list(streaming.stream_chat(
            "Research this.", task_type="legal_research", deep=True,
            model=streaming.DEFAULT_MODEL, guard=False, query="q"))
