import os, sys
sys.path.insert(0, "/opt/ai-orchestrator")
import pytest
from core.kai import infusion

LEGAL_Q = "Which Ghana Act governs extradition?"
NON_LEGAL_Q = "What model does Kai run on?"


def test_detects_legal_topics():
    assert infusion._is_legal_topic(LEGAL_Q) is True
    assert infusion._is_legal_topic(NON_LEGAL_Q) is False


def test_retrieval_failure_is_graceful():
    infusion._LEGAL_CACHE.clear()
    infusion._legal_embed = None
    import socket
    orig = infusion._http_get
    infusion._http_get = lambda url, timeout: (_ for _ in ()).throw(IOError("net down"))
    try:
        out = infusion._retrieve_legal("extradition")
        assert out is None
    finally:
        infusion._http_get = orig


def test_legal_injection_adds_snippets(monkeypatch):
    infusion._LEGAL_CACHE.clear()
    orig_get = infusion._http_get
    orig_legal = infusion._is_legal_topic
    monkeypatch.setattr(infusion, "_read_card",
                        lambda: "## Kai knowledge card (vtest)\nself-facts")
    monkeypatch.setattr(infusion, "_is_legal_topic", lambda q: True)
    monkeypatch.setattr(infusion, "_http_get",
        lambda url, timeout: '{"results": [{"title": "Extradition Act, 1960", "snippet": "Surrender of fugitive."}]}')
    try:
        block = infusion._retrieve_legal("extradition")
        assert block and "Extradition Act, 1960" in block
        infused, applied = infusion.infuse(LEGAL_Q, "text_task")
        assert applied is True
        assert "Extradition Act" in infused
    finally:
        infusion._http_get = orig_get
        infusion._is_legal_topic = orig_legal
