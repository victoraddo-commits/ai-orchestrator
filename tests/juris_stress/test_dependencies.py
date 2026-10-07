"""Dependency tests: legal brain client, graceful degradation when CT100
is down, ai_router smoke (optional real AI), and tunnel health probes."""
import os
import sys

import pytest
import requests

sys.path.insert(0, "/opt/ai-orchestrator")

LEGAL_BRAIN = "http://192.168.1.100:8100"


class TestLegalBrainLive:
    def test_health(self):
        r = requests.get(f"{LEGAL_BRAIN}/health", timeout=10)
        assert r.ok and r.json().get("ok") is True

    def test_search_endpoint(self):
        r = requests.get(f"{LEGAL_BRAIN}/search",
                         params={"q": "director duties", "limit": 5}, timeout=15)
        assert r.ok
        data = r.json()
        docs = data.get("documents") or data.get("results") or data
        assert isinstance(docs, list) and len(docs) > 0

    def test_stats(self):
        r = requests.get(f"{LEGAL_BRAIN}/stats", timeout=10)
        assert r.ok

    def test_document_integrity_endpoint(self):
        r = requests.get(f"{LEGAL_BRAIN}/documents", params={"limit": 1}, timeout=15)
        assert r.ok
        data = r.json()
        docs = data.get("documents") or data.get("results") or []
        if docs:
            doc_id = docs[0].get("id")
            ri = requests.get(f"{LEGAL_BRAIN}/integrity/{doc_id}", timeout=15)
            assert ri.ok

    def test_bad_document_id_is_404(self):
        r = requests.get(f"{LEGAL_BRAIN}/document/999999999", timeout=10)
        assert r.status_code == 404

    def test_search_rejects_garbage_gracefully(self):
        r = requests.get(f"{LEGAL_BRAIN}/search",
                         params={"q": "AND OR NOT *() NEAR(5", "limit": 3},
                         timeout=15)
        # must not 500
        assert r.status_code < 500


class TestLegalBrainDegradation:
    def test_bot_survives_dead_legal_brain(self, bot, fake_ai, monkeypatch):
        dead = "http://127.0.0.1:59999"
        monkeypatch.setenv("KAI_LEGAL_BRAIN_URL", dead)
        from conftest import SimUser
        u = SimUser(bot, user_id="444001")
        r = u.send("What are director duties under Ghana law?")
        assert r is not None, "bot crashed when legal brain unreachable"
        assert u.text_of(r) or u.menu_of(r)


class TestAIRouterSmoke:
    @pytest.mark.skipif(os.environ.get("JURIS_RUN_REAL_AI") != "1",
                        reason="real AI disabled by default")
    def test_real_delegate_answers(self):
        from core.ai.ai_router import delegate
        res = delegate("Answer in one line: what is a writ of summons?",
                       task_type="planning", capability="text_task")
        assert isinstance(res, dict) and (res.get("response") or "").strip()


class TestTunnelHealth:
    def test_ollama_tunnel_reachable_from_ct111(self):
        r = requests.get("http://127.0.0.1:11434/api/tags", timeout=10)
        assert r.ok and "qwen3-coder:kai" in r.text

    def test_vm112_llama_reachable(self):
        try:
            r = requests.get("http://192.168.1.242:5001/health", timeout=8)
        except requests.RequestException:
            r = None
            # llama.cpp may not implement /health; treat connection-refused
            # as failure only if also not serving /v1/models
            try:
                r = requests.get("http://192.168.1.242:5001/v1/models", timeout=8)
            except requests.RequestException:
                pass
        assert r is not None and r.ok, "VM112 llama.cpp unreachable"
