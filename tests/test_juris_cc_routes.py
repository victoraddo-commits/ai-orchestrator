"""Tests for the Juris Kai Command Center control-plane router (Part B).

Runs the router on a bare FastAPI app so core.api (and the KLAUS scheduler it
starts at import) is never loaded. No real Telegram/Ollama/legal-brain calls:
all upstreams are monkeypatched.
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.juris_kai import cc_routes

BRIDGE = {"Authorization": "Bearer test-bridge-token"}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr("core.bridge_auth._load_api_token",
                        lambda: "test-bridge-token")
    app = FastAPI()
    app.include_router(cc_routes.router)
    return TestClient(app)


# ── auth ──────────────────────────────────────────────────────────────────

class TestAuth:
    def test_read_requires_credentials(self, client):
        assert client.get("/api/juris-kai/health").status_code == 401

    def test_write_requires_credentials(self, client):
        r = client.post("/api/juris-kai/bot/restart")
        assert r.status_code == 401

    def test_bridge_token_allowed_for_read(self, client, monkeypatch):
        monkeypatch.setattr(cc_routes, "_bot_health", lambda: {"state": "active"})
        r = client.get("/api/juris-kai/metrics", headers=BRIDGE)
        assert r.status_code == 200

    def test_session_without_capability_forbidden(self, client, monkeypatch):
        monkeypatch.setattr("core.authz.check_capability", lambda *a, **k: False)
        r = client.post("/api/juris-kai/bot/restart",
                        headers={"X-Kai-Session": "viewer-token"})
        assert r.status_code == 403

    def test_session_with_capability_allowed(self, client, monkeypatch):
        monkeypatch.setattr("core.authz.check_capability", lambda *a, **k: True)
        monkeypatch.setattr(cc_routes.subprocess, "run",
                            lambda *a, **k: type("P", (), {"returncode": 0,
                                                           "stdout": "", "stderr": ""})())
        r = client.post("/api/juris-kai/bot/restart",
                        headers={"X-Kai-Session": "op-token"})
        assert r.status_code == 200
        assert r.json()["success"] is True


# ── health / routing / metrics ────────────────────────────────────────────

class TestHealth:
    def test_health_shape(self, client, monkeypatch):
        monkeypatch.setattr(cc_routes, "_bot_health", lambda: {"state": "active"})
        monkeypatch.setattr(cc_routes, "_legal_brain_health", lambda: {"ok": True})
        monkeypatch.setattr(cc_routes, "_juris_routing", lambda: {"chains": {}})
        monkeypatch.setattr(cc_routes, "_juris_metrics", lambda **k: {"samples": 0})
        monkeypatch.setattr(cc_routes, "_cache_stats", lambda: {"generation": {}})
        body = client.get("/api/juris-kai/health", headers=BRIDGE).json()
        assert body["bot"]["state"] == "active"
        assert body["legal_brain"]["ok"] is True
        assert "cache" in body

    def test_routing_filters_juris(self, client, monkeypatch):
        monkeypatch.setattr("core.ai.ai_router.provider_chain_report", lambda: {
            "chains": {"juris_research": ["kai_brain", "llama_coder_cpu"],
                       "coding": ["kai_coder"]},
            "default_chains": {"juris_research": ["kai_brain"]},
            "provider_state": {},
        })
        body = client.get("/api/juris-kai/routing", headers=BRIDGE).json()
        assert "juris_research" in body["chains"]
        assert "coding" not in body["chains"]

    def test_metrics_aggregate(self, client, monkeypatch):
        monkeypatch.setattr("core.ai.ai_router.get_usage_history", lambda: [
            {"task_type": "juris_research", "success": True, "duration_ms": 100},
            {"task_type": "juris_research", "success": False, "duration_ms": 300},
            {"task_type": "coding", "success": True, "duration_ms": 1},
        ])
        body = client.get("/api/juris-kai/metrics", headers=BRIDGE).json()
        m = body["metrics"]
        assert m["samples"] == 2
        assert m["avg_ms"] == 200.0
        assert m["success_rate"] == 0.5


# ── corpus ────────────────────────────────────────────────────────────────

class TestCorpus:
    def test_search_delegates(self, client, monkeypatch):
        monkeypatch.setattr("core.legal_brain_client.search",
                            lambda q, limit=20: [{"title": "Act", "id": 1}])
        body = client.get("/api/juris-kai/corpus/search?q=contract",
                          headers=BRIDGE).json()
        assert body["ok"] is True
        assert body["results"][0]["title"] == "Act"

    def test_search_handles_upstream_error(self, client, monkeypatch):
        def boom(q, limit=20):
            raise RuntimeError("brain down")
        monkeypatch.setattr("core.legal_brain_client.search", boom)
        body = client.get("/api/juris-kai/corpus/search?q=x",
                          headers=BRIDGE).json()
        assert body["ok"] is False and body["results"] == []

    def test_ingest_validation(self, client):
        r = client.post("/api/juris-kai/corpus/ingest", headers=BRIDGE,
                        json={"title": "x", "content": "short"})
        body = r.json()
        assert body["success"] is False
        assert len(body["validation_errors"]) == 2

    def test_ingest_success(self, client, monkeypatch):
        monkeypatch.setattr("core.legal_brain_client.ingest",
                            lambda **kw: {"ok": True, "id": 99})
        monkeypatch.setattr(cc_routes, "_log_admin", lambda *a, **k: None)
        r = client.post("/api/juris-kai/corpus/ingest", headers=BRIDGE,
                        json={"title": "Contract Act 1960",
                              "content": "A sufficiently long legal content body."})
        body = r.json()
        assert body["success"] is True and body["result"]["id"] == 99

    def test_document_combines_versions_and_integrity(self, client, monkeypatch):
        monkeypatch.setattr("core.legal_brain_client.get_document",
                            lambda d: {"id": d, "title": "Act"})
        monkeypatch.setattr("core.legal_brain_client.versions",
                            lambda d: [{"version": 1}])
        monkeypatch.setattr("core.legal_brain_client.integrity",
                            lambda d: {"ok": True})
        body = client.get("/api/juris-kai/corpus/document/5",
                          headers=BRIDGE).json()
        assert body["document"]["id"] == 5
        assert body["versions"] == [{"version": 1}]
        assert body["integrity"]["ok"] is True


# ── accounts / activity ───────────────────────────────────────────────────

class TestAccounts:
    def test_accounts_list(self, client, monkeypatch):
        monkeypatch.setattr("core.juris_kai.dashboard.list_accounts",
                            lambda **k: {"accounts": [{"account_id": "a1"}],
                                         "total": 1})
        body = client.get("/api/juris-kai/cc/accounts", headers=BRIDGE).json()
        assert body["accounts"][0]["account_id"] == "a1"

    def test_account_detail_404(self, client, monkeypatch):
        monkeypatch.setattr("core.juris_kai.dashboard.get_account_detail",
                            lambda a: None)
        assert client.get("/api/juris-kai/cc/accounts/missing",
                          headers=BRIDGE).status_code == 404

    def test_account_detail_enriched(self, client, monkeypatch):
        monkeypatch.setattr("core.juris_kai.dashboard.get_account_detail",
                            lambda a: {"account_id": a})
        monkeypatch.setattr("core.juris_kai.dashboard.get_payment_history",
                            lambda **k: [{"amount": 50}])
        monkeypatch.setattr("core.juris_kai.dashboard.get_usage_log",
                            lambda **k: [{"query": "x"}])
        body = client.get("/api/juris-kai/cc/accounts/a1",
                          headers=BRIDGE).json()
        assert body["payments"] == [{"amount": 50}]
        assert body["usage_log"] == [{"query": "x"}]

    def test_activity_shape(self, client, monkeypatch):
        class _FakeMgr:
            def get_security_logs(self, **k):
                return [{"event": "admin_action"}]
        monkeypatch.setattr("core.juris_kai.accounts.get_account_manager",
                            lambda: _FakeMgr())
        monkeypatch.setattr("core.juris_kai.dashboard.get_usage_log",
                            lambda **k: [{"query": "x"}])
        body = client.get("/api/juris-kai/activity", headers=BRIDGE).json()
        assert "security" in body and "usage" in body


# ── bot control ───────────────────────────────────────────────────────────

class TestBotControl:
    def test_invalid_action(self, client):
        r = client.post("/api/juris-kai/bot/nuke", headers=BRIDGE)
        assert r.status_code == 400

    def test_restart(self, client, monkeypatch):
        monkeypatch.setattr(cc_routes, "_log_admin", lambda *a, **k: None)
        monkeypatch.setattr(cc_routes.subprocess, "run",
                            lambda *a, **k: type("P", (), {"returncode": 0,
                                                           "stdout": "ok",
                                                           "stderr": ""})())
        body = client.post("/api/juris-kai/bot/restart", headers=BRIDGE).json()
        assert body["success"] is True and body["action"] == "restart"


# ── test query ────────────────────────────────────────────────────────────

class TestTestQuery:
    def test_requires_query(self, client):
        r = client.post("/api/juris-kai/test-query", headers=BRIDGE, json={})
        assert r.json() == {"success": False, "error": "query is required"}

    def test_streamed_query(self, client, monkeypatch):
        monkeypatch.setattr("core.juris_kai.legal_context.query_knowledge_base",
                            lambda q: [])
        monkeypatch.setattr("core.juris_kai.legal_context.build_context_preamble",
                            lambda docs: "")
        monkeypatch.setattr("core.juris_kai.streaming.stream_chat",
                            lambda prompt, task_type="legal_research", **k:
                            iter(["Ghana ", "law."]))
        body = client.post("/api/juris-kai/test-query", headers=BRIDGE,
                           json={"query": "contract law", "stream": True}).json()
        assert body["success"] is True
        assert body["text"] == "Ghana law."
        assert body["streamed"] is True
        assert body["ttft_ms"] is not None
        assert body["budget_tokens"] > 0

    def test_blocking_query_falls_back(self, client, monkeypatch):
        monkeypatch.setattr("core.juris_kai.legal_context.query_knowledge_base",
                            lambda q: [])
        monkeypatch.setattr("core.juris_kai.legal_context.build_context_preamble",
                            lambda docs: "")
        monkeypatch.setattr("core.juris_kai.streaming.generate",
                            lambda prompt, task_type="legal_research", **k:
                            "blocking text")
        body = client.post("/api/juris-kai/test-query", headers=BRIDGE,
                           json={"query": "contract law", "stream": False}).json()
        assert body["text"] == "blocking text"
        assert body["streamed"] is False


# ── Part B: cc-prefixed aliases, service control, cache clear ─────────────

class TestCcReadAuth:
    def test_read_session_without_capability_is_401(self, client, monkeypatch):
        monkeypatch.setattr("core.authz.check_capability", lambda *a, **k: False)
        r = client.get("/api/juris-kai/cc/cache",
                       headers={"X-Kai-Session": "viewer-token"})
        assert r.status_code == 401


class TestCcService:
    def test_status_requires_credentials(self, client):
        assert client.get("/api/juris-kai/cc/service").status_code == 401

    def test_status(self, client, monkeypatch):
        monkeypatch.setattr(cc_routes, "_bot_health",
                            lambda: {"state": "active", "active": True})
        body = client.get("/api/juris-kai/cc/service", headers=BRIDGE).json()
        assert body["state"] == "active"

    def test_action_requires_credentials(self, client):
        assert client.post("/api/juris-kai/cc/service/restart").status_code == 401

    def test_action_restart(self, client, monkeypatch):
        monkeypatch.setattr(cc_routes, "_log_admin", lambda *a, **k: None)
        monkeypatch.setattr(cc_routes.subprocess, "run",
                            lambda *a, **k: type("P", (), {"returncode": 0,
                                                           "stdout": "ok",
                                                           "stderr": ""})())
        body = client.post("/api/juris-kai/cc/service/restart",
                           headers=BRIDGE).json()
        assert body["success"] is True and body["action"] == "restart"

    def test_action_invalid(self, client):
        assert client.post("/api/juris-kai/cc/service/nuke",
                           headers=BRIDGE).status_code == 400


class TestCcTestQuery:
    def test_requires_credentials(self, client):
        r = client.post("/api/juris-kai/cc/test-query", json={"query": "x"})
        assert r.status_code == 401

    def test_requires_query(self, client):
        r = client.post("/api/juris-kai/cc/test-query", headers=BRIDGE, json={})
        assert r.json() == {"success": False, "error": "query is required"}

    def test_streamed_query(self, client, monkeypatch):
        monkeypatch.setattr("core.juris_kai.legal_context.query_knowledge_base",
                            lambda q: [])
        monkeypatch.setattr("core.juris_kai.legal_context.build_context_preamble",
                            lambda docs: "")
        monkeypatch.setattr("core.juris_kai.streaming.stream_chat",
                            lambda prompt, task_type="legal_research", **k:
                            iter(["Ghana ", "law."]))
        body = client.post("/api/juris-kai/cc/test-query", headers=BRIDGE,
                           json={"query": "contract law", "stream": True}).json()
        assert body["success"] is True
        assert body["text"] == "Ghana law."


class TestCcCache:
    def test_get_requires_credentials(self, client):
        assert client.get("/api/juris-kai/cc/cache").status_code == 401

    def test_clear_requires_credentials(self, client):
        assert client.post("/api/juris-kai/cc/cache/clear").status_code == 401

    def test_clear(self, client, monkeypatch):
        monkeypatch.setattr("core.juris_kai.cache.clear_caches",
                            lambda: {"cleared": 3, "generation_cleared": 2,
                                     "retrieval_cleared": 1})
        monkeypatch.setattr(cc_routes, "_log_admin", lambda *a, **k: None)
        body = client.post("/api/juris-kai/cc/cache/clear",
                           headers=BRIDGE).json()
        assert body["success"] is True and body["cleared"] == 3

    def test_clear_session_without_capability_is_403(self, client, monkeypatch):
        monkeypatch.setattr("core.authz.check_capability", lambda *a, **k: False)
        r = client.post("/api/juris-kai/cc/cache/clear",
                        headers={"X-Kai-Session": "viewer-token"})
        assert r.status_code == 403
