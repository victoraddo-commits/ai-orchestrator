"""TDD tests for the hallucination firewall (Phase 2 Task 2, CT111 side).

The firewall audits the citations in an already-grounded answer: unverifiable
citations are stripped and replaced with a visible marker. Verification is done
by the legal-brain (CT100); here the verifier is injected so the firewall is
exercised without a network. It must be **fail-open** — a verifier outage must
never crash or blank a legal answer, because the strict retrieval gate already
guaranteed grounding.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.juris_kai import citation_firewall  # noqa: E402


def test_strips_fabricated_and_keeps_verified():
    text = "Rape is under Act 29. Evidence is under NRCD 323."
    #              0123456789012345678901234567890123456789012345678
    start29 = text.index("Act 29")
    startnrcd = text.index("NRCD 323")
    verifier = lambda t: {
        "citations": [
            {"display": "Act 29", "status": "VERIFIED",
             "start": start29, "end": start29 + len("Act 29")},
            {"display": "NRCD 323", "status": "EXISTS_NOT_IN_CORPUS",
             "start": startnrcd, "end": startnrcd + len("NRCD 323")},
        ],
        "summary": {"VERIFIED": 1, "EXISTS_NOT_IN_CORPUS": 1},
        "all_verified": False,
    }
    out = citation_firewall.apply_citation_firewall(text, verifier=verifier)
    assert "Act 29" in out["text"]
    assert "NRCD 323" not in out["text"]
    assert citation_firewall.UNVERIFIED_MARKER in out["text"]
    assert out["changed"] is True and out["error"] is None


def test_fail_open_on_verifier_exception():
    def boom(text):
        raise RuntimeError("legal brain down")

    text = "Rape is under Act 29."
    out = citation_firewall.apply_citation_firewall(text, verifier=boom)
    assert out["text"] == text
    assert out["changed"] is False
    assert out["error"] and "RuntimeError" in out["error"]


def test_fail_open_on_garbage_report():
    text = "Rape is under Act 29."
    out = citation_firewall.apply_citation_firewall(text, verifier=lambda t: None)
    assert out["text"] == text and out["error"]


def test_empty_answer_is_untouched_and_never_verified():
    called = []

    def verifier(text):
        called.append(text)
        return {"citations": []}

    out = citation_firewall.apply_citation_firewall("", verifier=verifier)
    assert out["text"] == "" and out["changed"] is False
    assert called == []


def test_all_verified_answer_is_unchanged():
    text = "Rape is under Act 29."
    s = text.index("Act 29")
    verifier = lambda t: {"citations": [
        {"display": "Act 29", "status": "VERIFIED", "start": s, "end": s + 6}],
        "summary": {"VERIFIED": 1}, "all_verified": True}
    out = citation_firewall.apply_citation_firewall(text, verifier=verifier)
    assert out["text"] == text and out["changed"] is False


def test_report_is_passed_through_for_audit():
    text = "Rape is under Act 29."
    s = text.index("Act 29")
    verifier = lambda t: {"citations": [
        {"display": "Act 29", "status": "MISMATCH", "start": s, "end": s + 6}],
        "summary": {"MISMATCH": 1}, "all_verified": False}
    out = citation_firewall.apply_citation_firewall(text, verifier=verifier)
    assert out["report"]["summary"]["MISMATCH"] == 1


def test_out_of_range_spans_do_not_corrupt_text():
    text = "Rape is under Act 29."
    verifier = lambda t: {"citations": [
        {"display": "Act 29", "status": "UNVERIFIED", "start": 999,
         "end": 1005}], "summary": {"UNVERIFIED": 1}, "all_verified": False}
    out = citation_firewall.apply_citation_firewall(text, verifier=verifier)
    assert out["text"] == text


# ---------------------------------------------------------------------------
# wiring / strict-grounding invariant
# ---------------------------------------------------------------------------

def test_ungrounded_query_never_reaches_firewall(monkeypatch):
    """The strict rule is preserved: a refusal returns before the firewall.

    ``_build_legal_reply`` only builds the answer transform for a groundable
    plan, and an UNGROUNDED plan returns its refusal without ever calling
    ``_generate_reply`` — so no verifier is consulted for a refusal.
    """
    import os
    import tempfile
    import uuid

    os.environ.setdefault("JURIS_KAI_DB_DIR",
                          str(Path(tempfile.gettempdir()) / "juris_kai_test"))
    import core.juris_kai.bot as bot
    from core.juris_kai import grounding

    calls = []
    monkeypatch.setattr(citation_firewall, "apply_citation_firewall",
                        lambda *a, **k: calls.append(a) or
                        {"text": a[0], "report": {}, "changed": False,
                         "error": None})
    monkeypatch.setattr(
        grounding, "retrieve",
        lambda q, limit=3: {"docs": [], "verdict": "UNGROUNDED", "stage": 0})
    monkeypatch.setattr(
        bot, "_generate_reply",
        lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("no model call for UNGROUNDED")))

    import core.juris_kai.accounts as accts
    tmp = Path(tempfile.mkdtemp())
    monkeypatch.setattr(accts, "DB_DIR", str(tmp))
    monkeypatch.setattr(accts, "DB_PATH", str(tmp / "accounts.db"))
    accts._account_manager = None
    mgr = accts.get_account_manager()
    acct = mgr.get_or_create(str(uuid.uuid4().int)[:9], "T")
    mgr.accept_disclaimer(acct["account_id"])

    resp = bot._build_legal_reply("quantum entanglement tax", 123456, acct)
    assert resp["text"] == bot.UNGROUNDED_REPLY
    assert calls == [], "firewall must not run for a refusal"


# ---------------------------------------------------------------------------
# temporal / currency handling (Phase 3 T4)
# ---------------------------------------------------------------------------

def _span(text, needle, status="VERIFIED", temporal_status=None):
    start = text.index(needle)
    cit = {"display": needle, "status": status, "start": start,
           "end": start + len(needle)}
    if temporal_status is not None:
        cit["temporal_status"] = temporal_status
    return cit


def test_repealed_citation_is_flagged_repealed_not_generic():
    text = "Old levy is under Act 100."
    verifier = lambda t: {
        "citations": [_span(text, "Act 100", "MISMATCH", "REPEALED")],
        "summary": {"MISMATCH": 1}, "all_verified": False}
    out = citation_firewall.apply_citation_firewall(text, verifier=verifier)
    assert citation_firewall.REPEALED_MARKER in out["text"]
    assert "Act 100" not in out["text"]
    assert citation_firewall.UNVERIFIED_MARKER not in out["text"]
    assert out["changed"] is True and out["error"] is None


def test_unknown_status_is_kept_but_noted():
    text = "The Mystery Act applies."
    verifier = lambda t: {
        "citations": [_span(text, "Mystery Act", "VERIFIED", "UNKNOWN")],
        "summary": {"VERIFIED": 1}, "all_verified": True}
    out = citation_firewall.apply_citation_firewall(text, verifier=verifier)
    assert "Mystery Act" in out["text"]
    assert "status unknown" in out["text"]
    assert out["changed"] is True


def test_current_status_is_kept_unchanged():
    text = "The Companies Act applies."
    verifier = lambda t: {
        "citations": [_span(text, "Companies Act", "VERIFIED", "CURRENT")],
        "summary": {"VERIFIED": 1}, "all_verified": True}
    out = citation_firewall.apply_citation_firewall(text, verifier=verifier)
    assert out["text"] == text and out["changed"] is False


def test_amended_status_is_kept_unchanged():
    text = "The Criminal Offences Act applies."
    verifier = lambda t: {
        "citations": [_span(text, "Criminal Offences Act", "VERIFIED",
                            "AMENDED")],
        "summary": {"VERIFIED": 1}, "all_verified": True}
    out = citation_firewall.apply_citation_firewall(text, verifier=verifier)
    assert out["text"] == text and out["changed"] is False


def test_repealed_and_unknown_combined_rewrite_is_correct():
    text = "Old Act and New Act both apply."
    citations = [
        _span(text, "Old Act", "MISMATCH", "REPEALED"),
        _span(text, "New Act", "VERIFIED", "UNKNOWN"),
    ]
    verifier = lambda t: {"citations": citations,
                          "summary": {"MISMATCH": 1, "VERIFIED": 1},
                          "all_verified": False}
    out = citation_firewall.apply_citation_firewall(text, verifier=verifier)
    assert citation_firewall.REPEALED_MARKER in out["text"]
    assert "Old Act" not in out["text"]
    assert "New Act" in out["text"]
    assert "status unknown" in out["text"]

