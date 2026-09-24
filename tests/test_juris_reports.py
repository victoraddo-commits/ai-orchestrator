"""TDD tests for exportable reports (Phase 8 Task 2).

Render a research result to PDF (fpdf2) and DOCX (python-docx); the rendered
content carries a header (query/date/plan), the sections, the 📚 Sources footer
(temporal status included) and the "informational, not legal advice" disclaimer,
after the citation firewall has run on the narrative. Reports are stored under a
reports dir and exposed over the operator/entitlement-gated API; free tier gets
an upgrade prompt, paid tiers are metered.
"""
import os
import sys
import tempfile
import uuid
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent))

os.environ.setdefault(
    "JURIS_KAI_DB_DIR",
    str(Path(tempfile.gettempdir()) / "juris_kai_test"),
)

from core.juris_kai import reports  # noqa: E402

BRIDGE = {"Authorization": "Bearer test-bridge-token"}


DOCS = [{
    "id": 101,
    "title": "Criminal Offences Act, 1960",
    "citation": "Act 29",
    "year": 1960,
    "store_mode": "full",
    "temporal_status": "AMENDED",
    "chunk_content": "Stealing is defined in section 124. " * 20,
}]


def _deep_result(bad_citation=False):
    rule = "Section 124 of Act 29 defines stealing."
    if bad_citation:
        rule = "Act 9999 defines stealing and Act 29 applies."
    return {
        "query": "Is stealing an offence?",
        "docs": list(DOCS),
        "verdict": "GROUNDED",
        "advocate": {"pass": "advocate", "argument": "Stealing is an offence.",
                     "authorities": ["Act 29"], "docs": list(DOCS)},
        "opponent": {"pass": "opponent", "argument": "No contrary authority.",
                     "authorities": ["Act 29", "Act 500"],
                     "contrary_authorities": ["Act 500"],
                     "contrary_terms": [], "docs": list(DOCS)},
        "judge": {
            "pass": "judge",
            "established": [{"proposition": "Stealing is an offence."}],
            "disputed": [{"proposition": "The exception for minors."}],
            "unresolved": [],
            "authorities": ["Act 29", "Act 500"],
            "confidence": 0.35,
            "irac": {"issue": "Is stealing an offence?", "rule": rule,
                     "application": "Act 29 applies.",
                     "conclusion": "Established, but disputed on minors."},
            "uncertainty": {"advocate": [], "opponent": []},
            "docs": list(DOCS),
        },
        "authorities": ["Act 29", "Act 500"],
        "uncertainty": {"advocate": [], "opponent": []},
        "degraded": False,
        "latency": {"total": 1.0},
    }


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    db_dir = tmp_path / "juris_db"
    db_dir.mkdir()
    import core.juris_kai.accounts as accts
    monkeypatch.setattr(accts, "DB_DIR", str(db_dir))
    monkeypatch.setattr(accts, "DB_PATH", str(db_dir / "juris_kai_accounts.db"))
    accts._account_manager = None
    monkeypatch.setenv("JURIS_REPORTS_DIR", str(tmp_path / "reports"))
    monkeypatch.setattr(
        "core.legal_brain_client.verify_citations",
        lambda text, record=True: {"citations": [], "summary": {},
                                   "all_verified": True})
    yield
    accts._account_manager = None


def _mgr():
    from core.juris_kai.accounts import get_account_manager
    return get_account_manager()


def _account(tier=None):
    acct = _mgr().get_or_create(str(uuid.uuid4().int)[:9], "Tester")
    if tier:
        _mgr().set_subscription(acct["account_id"], tier)
    return acct


# ---------------------------------------------------------------------------
# renderers
# ---------------------------------------------------------------------------

class TestRender:
    def test_build_report_has_header_and_sections(self):
        rep = reports.build_report("deep", _deep_result(), plan="monthly_pro")
        assert rep["query"] == "Is stealing an offence?"
        assert rep["plan"] == "monthly_pro"
        assert rep["date"]
        headings = [s["heading"] for s in rep["sections"]]
        for h in ("Issue", "Rule", "Application", "Conclusion", "Authorities",
                  "Counter-authorities", "Uncertainty"):
            assert h in headings, h
        assert rep["docs"]

    def test_pdf_is_valid(self):
        rep = reports.build_report("deep", _deep_result())
        data = reports.render_pdf(rep)
        assert data[:4] == b"%PDF"
        assert len(data) > 800

    def test_docx_is_valid(self):
        rep = reports.build_report("deep", _deep_result())
        data = reports.render_docx(rep)
        assert data[:2] == b"PK"
        assert len(data) > 800

    def test_firewall_applied_before_render(self):
        def ver(text):
            target = "Act 9999"
            if target in text:
                s = text.index(target)
                return {"citations": [{"display": target,
                                       "status": "UNVERIFIED",
                                       "start": s, "end": s + len(target)}],
                        "summary": {"UNVERIFIED": 1}, "all_verified": False}
            return {"citations": [], "summary": {}, "all_verified": True}

        rep = reports.build_report("deep", _deep_result(bad_citation=True),
                                   verifier=ver)
        rule = next(s for s in rep["sections"] if s["heading"] == "Rule")
        rule_text = "\n".join(rule["lines"])
        assert "Act 9999" not in rule_text
        assert "unverified" in rule_text.lower()

    def test_sources_footer_includes_temporal_status(self):
        rep = reports.build_report("deep", _deep_result())
        assert any("AMENDED" in ln for ln in rep["source_lines"])
        assert any("Act 29" in ln for ln in rep["source_lines"])

    def test_disclaimer_present(self):
        assert "not legal advice" in reports.DISCLAIMER.lower()

    def test_authority_bundle_and_matrix_render(self):
        bundle = {"bundle": [{"issue": "Bail", "authorities": [
            {"title": "Bail Act", "citation": "Act 200", "year": 2002,
             "temporal_status": "AMENDED"}]}],
            "authorities": list(DOCS)}
        rep = reports.build_report("authority_bundle", bundle, query="Bail")
        assert rep["sections"]
        assert reports.render_pdf(rep)[:4] == b"%PDF"

        matrix = {"rows": [{"issue": "Bail", "law": "Bail Act",
                            "authority": "Act 200", "facts": "—",
                            "counterargument": "—", "status": "ESTABLISHED"}],
                  "authorities": list(DOCS)}
        rep2 = reports.build_report("issue_matrix", matrix, query="Bail")
        assert rep2["sections"]
        assert reports.render_docx(rep2)[:2] == b"PK"


# ---------------------------------------------------------------------------
# storage
# ---------------------------------------------------------------------------

class TestStorage:
    def test_create_report_writes_both_files(self):
        out = reports.create_report("deep", result=_deep_result())
        assert out["id"]
        pdf = Path(out["pdf_path"])
        docx = Path(out["docx_path"])
        assert pdf.exists() and docx.exists()
        assert pdf.name == f"juris_report_{out['id']}.pdf"
        assert pdf.read_bytes()[:4] == b"%PDF"
        assert docx.read_bytes()[:2] == b"PK"

    def test_list_reports_returns_created(self):
        out = reports.create_report("deep", result=_deep_result())
        listing = reports.list_reports()
        assert any(r["id"] == out["id"] for r in listing)

    def test_report_path_validates_id(self):
        assert reports.report_path("../../etc/passwd", "pdf") is None
        assert reports.report_path("nope", "pdf") is None

    def test_stash_roundtrip(self):
        token = reports.stash_result(_deep_result(), query="Is stealing an offence?")
        loaded = reports.load_stash(token)
        assert loaded["result"]["query"] == "Is stealing an offence?"
        assert reports.load_stash("does-not-exist") is None


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr("core.bridge_auth._load_api_token",
                        lambda: "test-bridge-token")
    from core.juris_kai import cc_routes
    app = FastAPI()
    app.include_router(cc_routes.router)
    return TestClient(app)


class TestReportsAPI:
    def test_create_requires_auth(self, client):
        r = client.post("/api/juris-kai/reports", json={"query": "x"})
        assert r.status_code == 401

    def test_free_tier_blocked_with_upgrade_prompt(self, client):
        acct = _account()
        r = client.post("/api/juris-kai/reports", headers=BRIDGE,
                        json={"result": _deep_result(),
                              "account_id": acct["account_id"]})
        assert r.status_code == 403
        assert "/subscribe" in r.json()["detail"]

    def test_pro_tier_allowed_and_metered(self, client):
        acct = _account("monthly_pro")
        before = _mgr()._count_usage_today(acct["account_id"], "report_export")
        r = client.post("/api/juris-kai/reports", headers=BRIDGE,
                        json={"result": _deep_result(),
                              "account_id": acct["account_id"]})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["success"] is True
        assert body["id"]
        assert body["pdf"].endswith(".pdf")
        assert body["docx"].endswith(".docx")
        after = _mgr()._count_usage_today(acct["account_id"], "report_export")
        assert after == before + 1

    def test_list_and_download_content_types(self, client):
        out = reports.create_report("deep", result=_deep_result())
        listing = client.get("/api/juris-kai/reports", headers=BRIDGE).json()
        assert any(x["id"] == out["id"] for x in listing["reports"])

        pdf = client.get(f"/api/juris-kai/reports/{out['id']}.pdf",
                         headers=BRIDGE)
        assert pdf.status_code == 200
        assert pdf.headers["content-type"] == "application/pdf"
        assert "attachment" in pdf.headers.get("content-disposition", "")
        assert pdf.content[:4] == b"%PDF"

        docx = client.get(f"/api/juris-kai/reports/{out['id']}.docx",
                          headers=BRIDGE)
        assert docx.status_code == 200
        assert "wordprocessingml" in docx.headers["content-type"]
        assert docx.content[:2] == b"PK"

    def test_missing_report_is_404(self, client):
        assert client.get("/api/juris-kai/reports/nope.pdf",
                          headers=BRIDGE).status_code == 404


# ---------------------------------------------------------------------------
# bot action
# ---------------------------------------------------------------------------

class TestBotExport:
    def test_deep_answer_offers_export_action(self, monkeypatch):
        import core.juris_kai.bot as bot
        from core.juris_kai import grounding
        monkeypatch.setattr(
            grounding, "retrieve",
            lambda q, limit=3, context="": {
                "docs": list(DOCS), "verdict": "GROUNDED", "stage": 1})
        monkeypatch.setattr(
            "core.juris_kai.reasoning.run_deep",
            lambda query, docs=None, context="": _deep_result())
        acct = _account()
        resp = bot._build_deep_reply("Is stealing an offence?", 920001, acct)
        markup = resp["reply_markup"] or ""
        assert "report:" in markup
        assert "Export" in markup

    def test_export_callback_creates_report_for_pro(self, monkeypatch):
        import core.juris_kai.bot as bot
        acct = _account("monthly_pro")
        token = reports.stash_result(_deep_result(),
                                     query="Is stealing an offence?")
        monkeypatch.setattr(bot, "answer_callback", lambda *a, **k: {})
        sent = {}
        monkeypatch.setattr(bot, "send_document",
                            lambda chat_id, path, **k: sent.update(
                                chat_id=chat_id, path=path) or {})
        cb = {"id": "cb1", "data": f"report:{token}",
              "message": {"chat": {"id": 920002}}, "from": {"id": acct["telegram_id"]}}
        out = bot.handle_callback(cb)
        assert out and out["text"]
        assert "juris_report_" in out["text"] or ".pdf" in out["text"]
        assert _mgr()._count_usage_today(acct["account_id"],
                                         "report_export") == 1

    def test_export_callback_blocks_free(self, monkeypatch):
        import core.juris_kai.bot as bot
        acct = _account()
        token = reports.stash_result(_deep_result(), query="q")
        monkeypatch.setattr(bot, "answer_callback", lambda *a, **k: {})
        cb = {"id": "cb2", "data": f"report:{token}",
              "message": {"chat": {"id": 920003}}, "from": {"id": acct["telegram_id"]}}
        out = bot.handle_callback(cb)
        assert "/subscribe" in out["text"]
        assert _mgr()._count_usage_today(acct["account_id"],
                                         "report_export") == 0
