"""Tests for KAI 2.0 phase 18E — Legal Brain Command Center dashboards."""
from __future__ import annotations

import os
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _isolated_memory(tmp_path, monkeypatch):
    """Every test writes to its own memory dir so state can't leak."""
    monkeypatch.setenv("AI_ORCHESTRATOR_MEMORY_DIR", str(tmp_path))
    # Reset module-level MEMORY_DIR since it was captured at import.
    import core.memory as _m
    monkeypatch.setattr(_m, "MEMORY_DIR", Path(str(tmp_path)))
    yield


# ── corpus coverage ─────────────────────────────────────────────────────────

def test_corpus_coverage_returns_16_tiers_empty_state():
    from core.legal_brain import corpus_coverage
    out = corpus_coverage.get_coverage()
    assert len(out["tiers"]) == 16
    # No index file → all actuals 0, all tiers empty
    assert all(t["actual"] == 0 for t in out["tiers"])
    assert out["totals"]["actual"] == 0
    assert out["alerts"]["empty_tier_count"] == 16
    assert out["source"] == "placeholder"


def test_corpus_coverage_counts_from_index(tmp_path, monkeypatch):
    from core.legal_brain import corpus_coverage
    from core import memory as _m
    _m.save("legal_corpus_index.json", {
        "schema_version": 1,
        "records": [
            {"id": "a", "tier": 1},
            {"id": "b", "tier": 3},
            {"id": "c", "tier": 3},
            {"id": "d", "tier": 99},  # unclassified
        ],
    })
    out = corpus_coverage.get_coverage()
    t1 = next(t for t in out["tiers"] if t["tier"] == 1)
    t3 = next(t for t in out["tiers"] if t["tier"] == 3)
    assert t1["actual"] == 1
    assert t3["actual"] == 2
    assert out["totals"]["unclassified"] == 1
    assert out["source"] == "legal_corpus_index.json"


# ── source registry ─────────────────────────────────────────────────────────

def test_add_source_dedupes_by_url():
    from core.legal_brain import source_registry
    a = source_registry.add_source("https://example.com/", "Example", 80, 3)
    b = source_registry.add_source("https://example.com", "Example dup", 80, 3)
    assert a["id"] == b["id"]
    assert len(source_registry.list_sources()) == 1


def test_remove_source_soft_deletes():
    from core.legal_brain import source_registry
    src = source_registry.add_source("https://x.test", "X", 70, 2)
    removed = source_registry.remove_source(src["id"])
    assert removed["active"] is False
    assert len(source_registry.list_sources(include_inactive=False)) == 0
    assert len(source_registry.list_sources(include_inactive=True)) == 1


def test_remove_source_unknown_returns_none():
    from core.legal_brain import source_registry
    assert source_registry.remove_source("nope") is None


def test_validate_source_uses_injected_client():
    from core.legal_brain import source_registry

    class _FakeResp:
        def __init__(self, status): self.status_code = status
    class _FakeClient:
        calls = []
        @staticmethod
        def head(url, timeout, follow_redirects):
            _FakeClient.calls.append((url, timeout))
            return _FakeResp(200)

    # example.com resolves to a global address so the SSRF guard passes;
    # the injected fake client short-circuits the real HTTP call.
    src = source_registry.add_source("https://example.com/laws", "Y", 90, 4)
    check = source_registry.validate_source(src["id"], http_client=_FakeClient)
    assert check["reachable"] is True
    assert check["http_status"] == 200
    assert _FakeClient.calls[0][0] == "https://example.com/laws"
    # Persisted on the record
    stored = source_registry.get_source(src["id"])
    assert stored["last_check"]["reachable"] is True


def test_validate_source_records_failure():
    from core.legal_brain import source_registry

    class _Boom:
        @staticmethod
        def head(url, timeout, follow_redirects): raise RuntimeError("dns fail")

    src = source_registry.add_source("https://example.com/z", "Z", 60, 5)
    check = source_registry.validate_source(src["id"], http_client=_Boom)
    assert check["reachable"] is False
    assert check["http_status"] is None
    assert "error" in check


def test_validate_source_unknown_id():
    from core.legal_brain import source_registry
    assert source_registry.validate_source("no-such-id") == {"error": "not_found"}


def test_trust_score_breakdown():
    from core.legal_brain import source_registry
    source_registry.add_source("https://a.test", "A", 95, 1)
    source_registry.add_source("https://b.test", "B", 75, 1)
    source_registry.add_source("https://c.test", "C", 55, 1)
    source_registry.add_source("https://d.test", "D", 10, 1)
    b = source_registry.trust_score_breakdown()
    assert b["90-100"] == 1
    assert b["70-89"] == 1
    assert b["50-69"] == 1
    assert b["0-49"] == 1


# ── knowledge health ────────────────────────────────────────────────────────

def test_knowledge_health_shape_empty():
    from core.legal_brain import knowledge_health
    out = knowledge_health.get_health()
    assert set(out) >= {"integrity", "freshness", "citation_graph", "sources"}
    assert out["integrity"]["total_docs"] == 0
    assert out["citation_graph"]["coverage_pct"] == 0.0


def test_knowledge_health_computes_from_index():
    from core.legal_brain import knowledge_health
    from core import memory as _m
    from datetime import datetime, timezone, timedelta
    now = datetime.now(timezone.utc)
    recent = now.isoformat()
    old = (now - timedelta(days=200)).isoformat()
    _m.save("legal_corpus_index.json", {
        "schema_version": 1,
        "records": [
            {"id": "a", "checksum_status": "ok", "citations": ["b"], "indexed_at": recent},
            {"id": "b", "checksum_status": "fail", "indexed_at": old},
            {"id": "c", "checksum_expected": "x", "checksum": "x", "citations": [], "indexed_at": recent},
        ],
    })
    _m.save("legal_sources.json", {"schema_version": 1, "records": [
        {"id": "s1", "url": "u", "active": True},
        {"id": "s2", "url": "u2", "active": False},
    ]})
    out = knowledge_health.get_health()
    assert out["integrity"]["total_docs"] == 3
    assert out["integrity"]["docs_with_valid_checksum"] == 2
    assert out["integrity"]["checksum_failures"] == 1
    # freshness: one doc older than 90d
    assert out["freshness"]["stale_count"] == 1
    # citation graph: 1 with, 2 without
    assert out["citation_graph"]["docs_with_citations"] == 1
    assert out["citation_graph"]["docs_missing_citations"] == 2
    # source counts
    assert out["sources"]["active_count"] == 1
    assert out["sources"]["total_count"] == 2


def test_validate_source_blocks_private_ip(tmp_path, monkeypatch):
    """SSRF guard: RFC1918 hostnames must be refused."""
    monkeypatch.setattr("core.memory._default_memory_dir", lambda: str(tmp_path))
    from core.legal_brain import source_registry
    src = source_registry.add_source("http://10.0.0.1/admin", "internal", 50, 6)
    check = source_registry.validate_source(src["id"])
    assert check["reachable"] is False
    assert "blocked" in (check.get("error") or "")


def test_validate_source_blocks_loopback(tmp_path, monkeypatch):
    """SSRF guard: 127.x/localhost must be refused."""
    monkeypatch.setattr("core.memory._default_memory_dir", lambda: str(tmp_path))
    from core.legal_brain import source_registry
    src = source_registry.add_source("http://127.0.0.1:8000/", "loop", 50, 6)
    check = source_registry.validate_source(src["id"])
    assert check["reachable"] is False
    assert "blocked" in (check.get("error") or "")
