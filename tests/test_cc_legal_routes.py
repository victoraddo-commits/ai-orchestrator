"""Tests for the CC Legal Brain proxy routes (Legal Brain 2.0 Phase 8, T3).

The Command Center browser must never reach CT100 directly: these orchestrator
routes proxy the legal brain through ``core.legal_brain_client`` and are all
operator-gated. Mounted on a bare FastAPI app so ``core.api`` (and its
scheduler) is never imported; no real brain/model calls (clients monkeypatched).
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core import cc_extra_routes as cc

IDENT = {"X-Kai-User": "owner@kai", "X-Kai-User-Id": "owner"}

READ_ROUTES = [
    "/api/legal/health",
    "/api/legal/coverage",
    "/api/legal/gaps",
    "/api/legal/everyday",
    "/api/legal/everyday/tenancy-rent",
    "/api/legal/licences",
    "/api/legal/relations/1",
    "/api/legal/status/1",
]


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(cc.cc_extra_router)
    return TestClient(app)


# ── auth ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("path", READ_ROUTES)
def test_reads_require_operator(client, path):
    assert client.get(path).status_code == 401


def test_ask_requires_operator(client):
    r = client.post("/api/legal/ask", json={"query": "contract law"})
    assert r.status_code == 401


def test_acquire_requires_operator(client):
    assert client.post("/api/legal/gaps/1/acquire").status_code == 401


def test_reads_accept_auth_proxy_identity(client, monkeypatch):
    monkeypatch.setattr("core.legal_brain_client.everyday_topics",
                        lambda: [{"key": "x"}])
    assert client.get("/api/legal/everyday", headers=IDENT).status_code == 200


# ── brain proxy (reads) ───────────────────────────────────────────────────

def test_health_delegates(client, monkeypatch):
    monkeypatch.setattr("core.legal_brain_client.legal_health",
                        lambda stale_days=None: {"documents": 1440})
    body = client.get("/api/legal/health", headers=IDENT,
                      params={"stale_days": 30}).json()
    assert body["ok"] is True and body["documents"] == 1440


def test_coverage_delegates(client, monkeypatch):
    monkeypatch.setattr("core.legal_brain_client.coverage",
                        lambda threshold=None: {"total": 5, "areas": {}})
    body = client.get("/api/legal/coverage", headers=IDENT).json()
    assert body["ok"] is True and body["total"] == 5


def test_gaps_delegates(client, monkeypatch):
    monkeypatch.setattr(
        "core.legal_brain_client.list_gaps",
        lambda status=None, limit=50: [
            {"id": 1, "status": "open", "domain": "land",
             "ministry": "Lands Commission", "question": "?"}])
    body = client.get("/api/legal/gaps", headers=IDENT).json()
    assert body["ok"] is True and body["count"] == 1
    assert body["gaps"][0]["domain"] == "land"


def test_acquire_delegates(client, monkeypatch):
    monkeypatch.setattr(
        "core.legal_brain_client.acquire_gap",
        lambda gap_id, per_source=5, delay=0.5: {"ok": True, "id": gap_id,
                                                 "found": 2})
    body = client.post("/api/legal/gaps/7/acquire", headers=IDENT,
                       json={}).json()
    assert body["ok"] is True and body["id"] == 7


def test_acquire_surfaces_filled_result(client, monkeypatch):
    """The Gaps tab must receive status/doc_ids/sources_tried/evidence."""
    monkeypatch.setattr(
        "core.legal_brain_client.acquire_gap",
        lambda gap_id, per_source=5, delay=0.5: {
            "ok": True, "gap_id": gap_id, "status": "filled",
            "doc_ids": [77], "sources_tried": ["parliament-dspace"],
            "evidence": [{"source": "parliament-dspace", "found": 1,
                          "enactments": 1, "skipped_non_enactment": 0,
                          "ingested": [77]}]})
    body = client.post("/api/legal/gaps/9/acquire", headers=IDENT,
                       json={}).json()
    assert body["status"] == "filled"
    assert body["doc_ids"] == [77]
    assert body["sources_tried"] == ["parliament-dspace"]
    assert body["evidence"][0]["ingested"] == [77]


def test_everyday_topics_delegates(client, monkeypatch):
    monkeypatch.setattr("core.legal_brain_client.everyday_topics",
                        lambda: [{"key": "tenancy-rent"}])
    body = client.get("/api/legal/everyday", headers=IDENT).json()
    assert body["ok"] is True and body["topics"][0]["key"] == "tenancy-rent"


def test_everyday_topic_delegates(client, monkeypatch):
    monkeypatch.setattr("core.legal_brain_client.everyday",
                        lambda topic: {"grounded": True, "explainer": "x",
                                       "sources": []})
    body = client.get("/api/legal/everyday/tenancy-rent",
                      headers=IDENT).json()
    assert body["ok"] is True and body["grounded"] is True


def test_licences_delegates(client, monkeypatch):
    monkeypatch.setattr(
        "core.legal_brain_client.licences",
        lambda: {"version": "2026-09-24",
                 "register": {"a": {"commercial": True}}, "markdown": "m"})
    body = client.get("/api/legal/licences", headers=IDENT).json()
    assert body["ok"] is True and body["register"]["a"]["commercial"] is True


def test_relations_delegates(client, monkeypatch):
    monkeypatch.setattr("core.legal_brain_client.relations",
                        lambda doc_id: {"document_id": doc_id, "relations": []})
    body = client.get("/api/legal/relations/3", headers=IDENT).json()
    assert body["ok"] is True and body["document_id"] == 3


def test_status_delegates(client, monkeypatch):
    monkeypatch.setattr("core.legal_brain_client.status",
                        lambda doc_id: {"document_id": doc_id, "status": "CURRENT"})
    body = client.get("/api/legal/status/3", headers=IDENT).json()
    assert body["ok"] is True and body["status"] == "CURRENT"


def test_brain_failure_is_502(client, monkeypatch):
    def boom(stale_days=None):
        raise RuntimeError("brain down")
    monkeypatch.setattr("core.legal_brain_client.legal_health", boom)
    r = client.get("/api/legal/health", headers=IDENT)
    assert r.status_code == 502 and r.json()["ok"] is False


# ── ask (grounding / deep) ────────────────────────────────────────────────

def test_ask_requires_query(client):
    body = client.post("/api/legal/ask", headers=IDENT, json={}).json()
    assert body["success"] is False


def test_ask_quick_grounded(client, monkeypatch):
    plan = {
        "groundable": True, "out_of_scope": False, "verdict": "GROUNDED",
        "docs": [{"id": 1, "title": "Contracts Act, 1960", "citation": "Act 25",
                  "year": 1960, "store_mode": "full"}],
        "prompt": "p", "banner": "", "footer": "\n\n📚 *Sources*",
        "source_key": "k", "refusal": None,
    }
    monkeypatch.setattr(
        "core.juris_kai.grounding.build_grounded_plan",
        lambda q, tt="juris_research", context="", commercial=False,
        asker=None: plan)
    monkeypatch.setattr(
        "core.juris_kai.streaming.generate",
        lambda prompt, task_type="x", **k: "Ghana contract law answer.")
    body = client.post("/api/legal/ask", headers=IDENT,
                       json={"query": "contract law"}).json()
    assert body["success"] is True and body["grounded"] is True
    assert "Ghana contract law answer." in body["text"]
    assert body["sources"][0]["title"] == "Contracts Act, 1960"
    assert body["verdict"] == "GROUNDED"


def test_ask_ungrounded_refuses_without_model(client, monkeypatch):
    plan = {"groundable": False, "out_of_scope": False, "verdict": "UNGROUNDED",
            "docs": [], "prompt": "", "banner": "", "footer": "",
            "source_key": "", "refusal": "⚖️ no source"}
    monkeypatch.setattr("core.juris_kai.grounding.build_grounded_plan",
                        lambda *a, **k: plan)
    monkeypatch.setattr(
        "core.juris_kai.streaming.generate",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("no model call")))
    body = client.post("/api/legal/ask", headers=IDENT,
                       json={"query": "xylophone zzz"}).json()
    assert body["grounded"] is False and body["refusal"] == "⚖️ no source"


def test_ask_deep_uses_run_deep(client, monkeypatch):
    result = {
        "query": "land", "verdict": "GROUNDED",
        "docs": [{"id": 2, "title": "Land Act 2020", "citation": "Act 1036",
                  "year": 2020, "store_mode": "full"}],
        "judge": {"established": [{}], "disputed": [], "unresolved": [{}],
                  "confidence": 0.7},
        "authorities": [], "degraded": False, "latency": {"total": 21.0},
    }
    monkeypatch.setattr("core.juris_kai.reasoning.run_deep",
                        lambda q, docs=None, context="", **k: result)
    body = client.post("/api/legal/ask", headers=IDENT,
                       json={"query": "land", "deep": True}).json()
    assert body["mode"] == "deep" and body["grounded"] is True
    assert body["fast"] is False
    assert body["uncertainty"] == {"established": 1, "disputed": 0,
                                   "unresolved": 1, "confidence": 0.7}
    assert body["sources"][0]["title"] == "Land Act 2020"


def test_ask_deep_fast_passes_fast_flag(client, monkeypatch):
    captured = {}
    result = {"query": "land", "verdict": "GROUNDED",
              "docs": [{"id": 2, "title": "Land Act 2020", "citation": "Act 1036",
                        "year": 2020, "store_mode": "full"}],
              "judge": {"established": [], "disputed": [], "unresolved": [],
                        "confidence": 0.5},
              "authorities": [], "degraded": False,
              "latency": {"total": 19.0, "judge_ttft": 0.4}}

    def fake(q, docs=None, context="", fast=False, **k):
        captured["fast"] = fast
        return result

    monkeypatch.setattr("core.juris_kai.reasoning.run_deep", fake)
    body = client.post("/api/legal/ask", headers=IDENT,
                       json={"query": "land", "deep": True, "fast": True}).json()
    assert captured["fast"] is True
    assert body["mode"] == "deep_fast" and body["fast"] is True
    assert body["ttft_ms"] is not None


def test_ask_deep_stream_emits_sse_events(client, monkeypatch):
    def fake(q, docs=None, context="", fast=False, stream_judge=False,
             on_judge_chunk=None, **k):
        if on_judge_chunk:
            on_judge_chunk("ISSUE: land\n")
            on_judge_chunk("RULE: Act 1036\n")
        return {"query": q, "verdict": "GROUNDED",
                "docs": [{"id": 2, "title": "Land Act 2020", "year": 2020}],
                "judge": {"established": [], "disputed": [], "unresolved": [],
                          "confidence": 0.5, "irac": {}},
                "authorities": [], "degraded": False,
                "latency": {"total": 5.0, "judge_ttft": 0.2}}

    monkeypatch.setattr("core.juris_kai.reasoning.run_deep", fake)
    with client.stream("POST", "/api/legal/ask", headers=IDENT,
                       json={"query": "land", "deep": True, "fast": True,
                             "stream": True}) as r:
        assert r.status_code == 200
        assert "text/event-stream" in r.headers["content-type"]
        body = "".join(r.iter_text())
    assert "event: status" in body
    assert "event: delta" in body and "ISSUE: land" in body
    assert "event: final" in body
    assert "deep_fast" in body
