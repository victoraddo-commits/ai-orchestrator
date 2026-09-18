"""Tests for the Kai Directives Reports API (KAI 2.0 Phase 3).

The reports surface reuses the directives service auth (Duo session, or the
machine token from loopback only). Discovery is restricted to a configurable set
of roots under a base directory; ids are opaque base64url tokens and every
resolution is checked to stay inside an allowed root, so no directory traversal
can escape.
"""
from __future__ import annotations

import base64
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import core.directives_api as da  # noqa: E402

TOKEN = "test-machine-token"


@pytest.fixture
def reports_env(tmp_path, monkeypatch):
    """A throwaway repo: docs/ + reports/ + directives/ with a few artifacts."""
    base = tmp_path / "repo"
    docs = base / "docs"
    reports = base / "reports"
    directives = base / "directives"
    for d in (docs, reports, directives):
        d.mkdir(parents=True)

    (docs / "KAIBET_DISCOVERY_REPORT.md").write_text(
        "# KaiBet Discovery Report\n\nSome **bold** body.\n", encoding="utf-8")
    (docs / "KAI_AUDIT_REPORT_2026-09-18.md").write_text(
        "# KAI ECOSYSTEM AUDIT REPORT\n\n## Findings\n\n- one\n- two\n", encoding="utf-8")
    (docs / "KAI_PHASE1_TEAMMATE_FACTORY_REPORT.md").write_text(
        "# Phase 1 Teammate Factory Report\n", encoding="utf-8")
    (reports / "roadmap_build_reconciliation_20260918T101133Z.md").write_text(
        "no heading in this file\nsecond line\n", encoding="utf-8")
    (directives / "operator-note.md").write_text("# Operator Note\n", encoding="utf-8")

    # Non-report material that must be ignored.
    (docs / "notes.txt").write_text("plain text, not markdown\n", encoding="utf-8")
    (base / "README.md").write_text("# Repo README (outside allowed roots)\n", encoding="utf-8")

    monkeypatch.setattr(da, "REPORTS_BASE", base)
    monkeypatch.setattr(da, "REPORTS_DIRS", ("docs", "reports", "directives"))
    monkeypatch.setattr(da, "_token", lambda: TOKEN)
    return base


@pytest.fixture
def client(reports_env):
    return TestClient(da.app, client=("127.0.0.1", 50000))


def _auth_headers():
    return {"X-Directive-Token": TOKEN}


def _id_for(rel: str) -> str:
    return base64.urlsafe_b64encode(rel.encode()).decode().rstrip("=")


# ── auth ────────────────────────────────────────────────────────────────────


def test_list_reports_requires_auth(client):
    r = client.get("/api/reports")
    assert r.status_code == 401


def test_get_report_requires_auth(client, reports_env):
    rid = _id_for("docs/KAI_AUDIT_REPORT_2026-09-18.md")
    r = client.get(f"/api/reports/{rid}")
    assert r.status_code == 401


def test_list_reports_accepts_machine_token(client):
    r = client.get("/api/reports", headers=_auth_headers())
    assert r.status_code == 200
    assert "reports" in r.json()


# ── discovery ───────────────────────────────────────────────────────────────


def test_lists_markdown_from_allowed_roots_only(client):
    rows = client.get("/api/reports", headers=_auth_headers()).json()["reports"]
    paths = {r["path"] for r in rows}
    assert "docs/KAIBET_DISCOVERY_REPORT.md" in paths
    assert "docs/KAI_AUDIT_REPORT_2026-09-18.md" in paths
    assert "reports/roadmap_build_reconciliation_20260918T101133Z.md" in paths
    assert "directives/operator-note.md" in paths
    # txt files and md outside the allowed roots are excluded
    assert "docs/notes.txt" not in paths
    assert "README.md" not in paths


def test_list_item_metadata_shape(client):
    rows = client.get("/api/reports", headers=_auth_headers()).json()["reports"]
    for r in rows:
        assert set(r) == {"id", "title", "path", "type", "size", "mtime", "source"}
        assert r["size"] > 0
        assert r["id"]
        assert r["mtime"].endswith("Z")


def test_source_is_top_level_dir(client):
    rows = client.get("/api/reports", headers=_auth_headers()).json()["reports"]
    by_path = {r["path"]: r for r in rows}
    assert by_path["docs/KAI_AUDIT_REPORT_2026-09-18.md"]["source"] == "docs"
    assert by_path["reports/roadmap_build_reconciliation_20260918T101133Z.md"]["source"] == "reports"
    assert by_path["directives/operator-note.md"]["source"] == "directives"


def test_title_derived_from_first_heading(client):
    rows = client.get("/api/reports", headers=_auth_headers()).json()["reports"]
    by_path = {r["path"]: r for r in rows}
    assert by_path["docs/KAI_AUDIT_REPORT_2026-09-18.md"]["title"] == "KAI ECOSYSTEM AUDIT REPORT"
    assert by_path["docs/KAIBET_DISCOVERY_REPORT.md"]["title"] == "KaiBet Discovery Report"


def test_title_falls_back_to_filename_without_heading(client):
    rows = client.get("/api/reports", headers=_auth_headers()).json()["reports"]
    by_path = {r["path"]: r for r in rows}
    title = by_path["reports/roadmap_build_reconciliation_20260918T101133Z.md"]["title"]
    assert "roadmap build reconciliation" in title.lower()
    assert title  # never empty


def test_type_classification(client):
    rows = client.get("/api/reports", headers=_auth_headers()).json()["reports"]
    by_path = {r["path"]: r for r in rows}
    assert by_path["docs/KAI_AUDIT_REPORT_2026-09-18.md"]["type"] == "audit"
    assert by_path["docs/KAIBET_DISCOVERY_REPORT.md"]["type"] == "discovery"
    assert by_path["docs/KAI_PHASE1_TEAMMATE_FACTORY_REPORT.md"]["type"] == "report"
    assert by_path["reports/roadmap_build_reconciliation_20260918T101133Z.md"]["type"] == "reconciliation"
    assert by_path["directives/operator-note.md"]["type"] == "doc"


# ── rendering ───────────────────────────────────────────────────────────────


def test_get_report_renders_html(client):
    rid = _id_for("docs/KAI_AUDIT_REPORT_2026-09-18.md")
    r = client.get(f"/api/reports/{rid}", headers=_auth_headers())
    assert r.status_code == 200
    body = r.json()
    assert body["title"] == "KAI ECOSYSTEM AUDIT REPORT"
    assert "<h1>" in body["html"]
    assert "<h2>" in body["html"]
    assert "<li>" in body["html"]


def test_render_escapes_raw_html_and_unsafe_links():
    md = (
        "# Hello\n\n"
        "<script>alert('xss')</script>\n\n"
        "[click](javascript:alert(1)) and <img src=x onerror=alert(1)>\n"
    )
    html = da._render_markdown(md)
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    assert "javascript:alert(1)" not in html
    assert "<img" not in html
    # safe links survive
    assert '<a href="https://example.com"' in da._render_markdown(
        "[ok](https://example.com)\n")


def test_render_inline_code_and_bold():
    html = da._render_markdown("Use `docker` for **builds**.")
    assert "<code>docker</code>" in html
    assert "<strong>builds</strong>" in html


# ── path safety ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize("rel", [
    "../README.md",
    "docs/../../README.md",
    "../../etc/passwd",
    "docs/../reports/../../README.md",
])
def test_path_traversal_ids_are_rejected(client, reports_env, rel):
    rid = _id_for(rel)
    r = client.get(f"/api/reports/{rid}", headers=_auth_headers())
    assert r.status_code == 404


def test_absolute_path_id_is_rejected(client, reports_env):
    rid = _id_for("/etc/passwd")
    r = client.get(f"/api/reports/{rid}", headers=_auth_headers())
    assert r.status_code == 404


def test_garbage_id_is_rejected(client):
    r = client.get("/api/reports/not-valid-base64!!", headers=_auth_headers())
    assert r.status_code == 404


def test_non_markdown_target_is_rejected(client, reports_env):
    rid = _id_for("docs/notes.txt")
    r = client.get(f"/api/reports/{rid}", headers=_auth_headers())
    assert r.status_code == 404


def test_symlink_escape_is_rejected(client, reports_env, tmp_path):
    secret = tmp_path / "outside-secret.md"
    secret.write_text("# Secret\n", encoding="utf-8")
    link = reports_env / "docs" / "escape.md"
    link.symlink_to(secret)
    rid = _id_for("docs/escape.md")
    r = client.get(f"/api/reports/{rid}", headers=_auth_headers())
    assert r.status_code == 404
    # and it must not be surfaced in the listing either
    rows = client.get("/api/reports", headers=_auth_headers()).json()["reports"]
    assert "docs/escape.md" not in {row["path"] for row in rows}


def test_download_returns_markdown_and_requires_auth(client):
    rid = _id_for("docs/KAI_AUDIT_REPORT_2026-09-18.md")
    assert client.get(f"/api/reports/{rid}/download").status_code == 401
    r = client.get(f"/api/reports/{rid}/download", headers=_auth_headers())
    assert r.status_code == 200
    assert r.text.startswith("# KAI ECOSYSTEM AUDIT REPORT")
    assert "attachment" in r.headers.get("content-disposition", "")


# ── helper units ────────────────────────────────────────────────────────────


def test_derive_title_strips_markdown_emphasis():
    assert da._derive_title("# **Bold** `Title` #\n", "x.md") == "Bold Title"


def test_derive_title_uses_filename_fallback():
    assert da._derive_title("no heading", "my_cool-report.md") == "my cool report"


# ── Command Center proxy wiring ─────────────────────────────────────────────


def test_cc_reports_proxy_routes_registered():
    from core import cc_extra_routes as cc

    paths = {r.path for r in cc.cc_extra_router.routes}
    assert "/api/reports/list" in paths
    assert "/api/reports/get/{report_id}" in paths
    assert "/api/reports/download/{report_id}" in paths


def test_cc_reports_proxy_forwards_with_machine_token(monkeypatch):
    import json
    import urllib.request

    from core import cc_extra_routes as cc

    captured = {}

    class FakeResp:
        headers = {"Content-Type": "application/json"}

        def __init__(self, body):
            self._body = body

        def read(self):
            return self._body

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def fake_urlopen(req, timeout=0):
        captured["url"] = req.full_url
        captured["token"] = req.get_header("X-directive-token")
        return FakeResp(json.dumps({"reports": []}).encode())

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(cc, "_directives_token", lambda: "tok-123")

    cc.reports_list()
    assert captured["url"].endswith("/api/reports")
    assert captured["token"] == "tok-123"

    cc.reports_get("abc")
    assert captured["url"].endswith("/api/reports/abc")

    cc.reports_download("abc")
    assert captured["url"].endswith("/api/reports/abc/download")
