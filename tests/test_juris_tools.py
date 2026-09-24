"""TDD: Juris Kai practice + research tools (Legal Brain 2.0 Phase 6, Task 4).

Each tool renders correctly, is authority-only (authorities come from retrieval,
never invented), passes its output through the AgentGuard output gate (citation
firewall applied), keeps a user document out of the authoritative corpus, and
Case-law mode honestly reports that there is no judgment corpus.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.agentguard.legal_policy import LegalGuard  # noqa: E402
from core.juris_kai import tools  # noqa: E402


def _noop_guard():
    """A gate with no network verifier and no audit side effects."""
    return LegalGuard(audit_fn=lambda **k: None,
                      verifier=lambda text: {"citations": []})


# ---------------------------------------------------------------------------
# Contract Analysis (zero-trust workspace)
# ---------------------------------------------------------------------------

CONTRACT = (
    "1. The Supplier shall deliver the goods by 12 March 2021.\n\n"
    "2. The Customer must pay the invoice within 30 days.\n\n"
    "3. If the Customer fails to pay, a penalty of 5% shall apply and the "
    "Customer shall be liable for damages.\n\n"
    "4. Either party may terminate this agreement on 30 days written notice.\n\n"
    "5. The Supplier shall indemnify the Customer against all liabilities.\n"
)


def test_contract_analysis_detects_sections():
    result = tools.contract_analysis(CONTRACT, title="Supply Agreement",
                                     guard=_noop_guard())
    assert result["workspace_only"] is True
    assert result["obligations"]
    assert result["risks"]
    assert result["termination"]
    assert result["liabilities"]
    assert "Supply Agreement" in result["rendered"]
    for heading in ("Obligations", "Risks", "Termination", "Liabilities"):
        assert heading in result["rendered"]


def test_contract_analysis_never_touches_corpus(monkeypatch):
    import core.legal_brain_client as lb

    def boom(*a, **k):
        raise AssertionError("contract analysis must not touch the corpus")

    monkeypatch.setattr(lb, "search", boom)
    monkeypatch.setattr(lb, "ingest", boom)
    result = tools.contract_analysis(CONTRACT, guard=_noop_guard())
    assert result["clauses"]
    assert result["workspace_only"] is True


# ---------------------------------------------------------------------------
# Authority Bundle (retrieval only)
# ---------------------------------------------------------------------------

_DOCS = [
    {"id": 1, "title": "Contracts Act 1960", "citation": "Act 25",
     "year": 1960, "type": "act", "authority_level": "primary"},
    {"id": 2, "title": "Criminal Offences Act 1960", "citation": "Act 29",
     "year": 1960, "type": "act", "authority_level": "primary"},
]


def _retrieve(query, limit=6):
    return list(_DOCS)


def test_authority_bundle_renders_retrieved_authorities():
    result = tools.authority_bundle(["breach of contract", "theft"],
                                    retrieve=_retrieve, guard=_noop_guard())
    assert len(result["bundle"]) == 2
    assert result["bundle"][0]["authorities"][0]["title"] == "Contracts Act 1960"
    assert "Authority Bundle" in result["rendered"]
    assert "Contracts Act 1960" in result["rendered"]
    # authority-only: the two authorities are exactly the retrieved docs
    assert {tools._identity(d) for d in result["authorities"]} == {"Act 25", "Act 29"}


def test_authority_bundle_honest_when_none_found():
    result = tools.authority_bundle(["interstellar law"],
                                    retrieve=lambda q, limit=6: [],
                                    guard=_noop_guard())
    assert "No authority found" in result["rendered"]


# ---------------------------------------------------------------------------
# Legal Issue Matrix (retrieval + Deep reasoning)
# ---------------------------------------------------------------------------

def _deep_result():
    doc = {"id": 1, "title": "Contracts Act 1960", "citation": "Act 25",
           "year": 1960, "type": "act"}
    return {
        "docs": [doc],
        "authorities": ["Act 25"],
        "opponent": {"contrary_authorities": ["Act 29"]},
        "judge": {
            "established": [{"proposition": "A contract needs offer and acceptance.",
                             "status": "settled", "authorities": ["Act 25"]}],
            "disputed": [{"proposition": "Consideration is always required.",
                          "status": "disputed", "authorities": ["Act 25"]}],
            "unresolved": [{"proposition": "Whether promissory estoppel applies.",
                            "status": "unresolved", "authorities": []}],
        },
    }


def test_issue_matrix_renders_all_columns():
    result = tools.issue_matrix("contract formation", facts="A agreed to sell.",
                                deep=lambda q: _deep_result(),
                                guard=_noop_guard())
    text = result["rendered"]
    for header in ("Issue", "Law", "Authority", "Facts", "Counterargument",
                   "Status"):
        assert header in text
    assert "Contracts Act 1960" in text       # Law resolved from retrieval
    assert "Act 29" in text                    # counterargument from opponent
    statuses = {r["status"] for r in result["rows"]}
    assert {"SETTLED", "DISPUTED", "UNRESOLVED"} <= statuses
    assert len(result["rows"]) == 3


def test_issue_matrix_honest_without_authority():
    result = tools.issue_matrix("xylophone law", deep=lambda q: {},
                                guard=_noop_guard())
    assert result["rows"][0]["status"] == "NO_AUTHORITY"
    assert "NO_AUTHORITY" in result["rendered"]


# ---------------------------------------------------------------------------
# Legal Chronology (user facts)
# ---------------------------------------------------------------------------

FACTS = (
    "On 12 March 2020 the parties signed the lease. "
    "The tenant defaulted on 2021-04-05. "
    "A notice to quit was served on 5 April 2021. "
    "No dates here at all."
)


def test_chronology_extracts_dates_and_sources():
    result = tools.legal_chronology(FACTS, guard=_noop_guard())
    dates = [e["date"] for e in result["events"]]
    assert "2020-03-12" in dates
    assert "2021-04-05" in dates
    assert dates == sorted(dates)
    assert all(e["source"] == "User-provided facts" for e in result["events"])
    assert "Legal Chronology" in result["rendered"]
    assert "2020-03-12" in result["rendered"]


def test_chronology_honest_when_no_dates():
    result = tools.legal_chronology("There are no dates in this text.",
                                    guard=_noop_guard())
    assert result["events"] == []
    assert "No dates" in result["rendered"]


# ---------------------------------------------------------------------------
# Research modes
# ---------------------------------------------------------------------------

def test_case_law_mode_reports_no_corpus_honestly():
    result = tools.research("donoghue v stevenson", mode="case_law",
                            retrieve=_retrieve, guard=_noop_guard())
    assert result["honest"] is True
    assert result["authorities"] == []
    assert "no reported judgments" in result["rendered"].lower()


def test_case_law_mode_lists_real_judgments_when_present():
    judgments = [{"id": 9, "title": "Foo v Bar", "citation": "[2020] GHSC 1",
                  "year": 2020, "type": "judgment"}]
    result = tools.research("foo", mode="case_law",
                            retrieve=lambda q, limit=6: judgments,
                            guard=_noop_guard())
    assert result["honest"] is False
    assert "Foo v Bar" in result["rendered"]


def test_statute_mode_filters_to_enactments():
    mixed = _DOCS + [{"id": 9, "title": "Foo v Bar", "citation": "[2020] GHSC 1",
                      "year": 2020, "type": "judgment"}]
    result = tools.research("contract", mode="statute",
                            retrieve=lambda q, limit=6: mixed,
                            guard=_noop_guard())
    assert "Contracts Act 1960" in result["rendered"]
    assert "Foo v Bar" not in result["rendered"]


def test_quick_mode_delegates_to_grounded_generation():
    seen = {}

    def fake_generate(prompt):
        seen["prompt"] = prompt
        return "Grounded answer."

    # patch grounding.build_grounded_plan to a groundable plan
    from core.juris_kai import grounding
    original = grounding.build_grounded_plan
    grounding.build_grounded_plan = lambda q, *a, **k: {
        "refusal": None, "banner": "", "prompt": "PROMPT", "docs": _DOCS,
        "footer": "\n\n📚 *Sources*", "verdict": "GROUNDED"}
    try:
        result = tools.research("contract", mode="quick",
                                generate=fake_generate, guard=_noop_guard())
    finally:
        grounding.build_grounded_plan = original
    assert seen["prompt"] == "PROMPT"
    assert "Grounded answer." in result["rendered"]


def test_quick_mode_refusal_is_honest():
    from core.juris_kai import grounding
    original = grounding.build_grounded_plan
    grounding.build_grounded_plan = lambda q, *a, **k: {
        "refusal": "I won't guess.", "banner": "", "prompt": "", "docs": [],
        "footer": "", "verdict": "UNGROUNDED"}
    try:
        result = tools.research("zzz", mode="quick",
                                generate=lambda p: "should not run",
                                guard=_noop_guard())
    finally:
        grounding.build_grounded_plan = original
    assert result["honest"] is True
    assert "I won't guess." in result["rendered"]


# ---------------------------------------------------------------------------
# Output gate: the citation firewall is applied to tool output
# ---------------------------------------------------------------------------

def _unverified_verifier(text):
    marker = "Fabricated Act 1999"
    i = text.find(marker)
    if i == -1:
        return {"citations": []}
    return {"citations": [{"start": i, "end": i + len(marker),
                           "status": "UNVERIFIED", "temporal_status": ""}]}


def test_authority_bundle_output_is_firewalled():
    docs = [{"id": 5, "title": "Fabricated Act 1999",
             "citation": "Fabricated Act 1999", "year": 1999, "type": "act"}]
    guard = LegalGuard(audit_fn=lambda **k: None,
                       verifier=_unverified_verifier)
    result = tools.authority_bundle(["some issue"],
                                    retrieve=lambda q, limit=6: docs,
                                    guard=guard)
    assert "Fabricated Act 1999" not in result["rendered"]
    assert "unverified" in result["rendered"].lower()


def test_contract_output_is_gated(monkeypatch):
    called = {}

    class RecordingGuard:
        def guard_output(self, text, source="juris_kai"):
            called["source"] = source
            return {"text": text, "changed": False, "denied": False}

    tools.contract_analysis(CONTRACT, guard=RecordingGuard())
    assert called["source"] == "juris_tool:contract_analysis"
