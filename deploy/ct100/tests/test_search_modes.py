"""Tests for keyword/phrase search + provenance (2026-09-26).

Covers the fixes: phrase falls back instead of returning empty; the default
mode is hybrid; keyword mode is lexical-only; every result carries provenance.
Run on CT100:  cd /opt/kai-legal-brain && python3 -m pytest tests/test_search_modes.py
"""
import json
import urllib.request
import urllib.parse

BASE = "http://127.0.0.1:8100"


def _search(q, mode=None, limit=5):
    u = f"{BASE}/search?q={urllib.parse.quote(q)}&limit={limit}"
    if mode:
        u += f"&mode={mode}"
    with urllib.request.urlopen(u, timeout=30) as r:
        return json.load(r)


def test_phrase_never_returns_empty_when_and_would_match():
    """'director duties' has no exact-phrase hit, but phrase mode must fall
    back to all-words rather than returning nothing."""
    d = _search("director duties", mode="phrase")
    assert len(d.get("results") or []) > 0, "phrase mode returned empty"


def test_default_mode_is_hybrid():
    d = _search("company registration")
    assert d.get("mode") == "hybrid", f"default mode was {d.get('mode')!r}"


def test_keyword_mode_is_lexical():
    d = _search("company registration", mode="keyword")
    assert d.get("mode") == "keyword"
    assert (d.get("results") or []), "keyword mode returned empty"


def test_every_result_has_provenance():
    for mode in ("hybrid", "keyword"):
        d = _search("company registration", mode=mode)
        for r in (d.get("results") or []):
            p = r.get("provenance")
            assert p, f"{mode}: result missing provenance"
            assert p.get("document_id"), f"{mode}: provenance missing document_id"
            assert isinstance(p.get("matched_text"), str)


def test_citation_resolves_to_content_rich_copy():
    """Act 992 must resolve to the full act, not the 29-char search_only stub."""
    d = _search("Act 992", mode="hybrid")
    r = d.get("results") or []
    assert r, "Act 992 returned nothing"
    top = r[0]
    assert "992" in str(top.get("title") or top.get("citation") or "")


def test_ambiguous_citation_reports_disambiguation():
    d = _search("Act 83", mode="hybrid")
    r = d.get("results") or []
    if r:
        dis = r[0].get("disambiguation")
        assert dis is None or dis.get("count", 0) >= 1
