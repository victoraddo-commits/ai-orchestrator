"""TDD tests for the uncertainty engine (Phase 5 Task 1).

Each proposition a reasoning pass emits is classified against the retrieved
authorities alone. The rules are deliberately conservative and honest:
single-source is never ``settled``, conflicting authorities are ``disputed``,
no authority is ``unresolved``, and missing facts are flagged.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.juris_kai import uncertainty  # noqa: E402


def _doc(citation, **kw):
    doc = {
        "id": kw.pop("id", abs(hash(citation)) % 100000),
        "title": kw.pop("title", f"{citation} instrument"),
        "citation": citation,
        "authority_level": kw.pop("authority_level", "act"),
        "store_mode": "full",
        "chunk_content": "x" * 500,
    }
    doc.update(kw)
    return doc


ACT29 = _doc("Act 29", title="Criminal Offences Act, 1960")
CONST = _doc("1992 Constitution", title="Constitution of the Republic of Ghana, 1992",
             authority_level="constitution")
REPEALED = _doc("Act 100", title="Old Levy Act, 1970",
                temporal_status="REPEALED")


# ---------------------------------------------------------------------------
# single source -> at most probable
# ---------------------------------------------------------------------------

def test_single_source_is_at_most_probable():
    res = uncertainty.classify(["Rape is defined by Act 29."], [ACT29])
    assert len(res) == 1
    assert res[0]["status"] == uncertainty.PROBABLE
    assert res[0]["authorities"] == ["Act 29"]
    assert "single" in res[0]["reason"].lower()


def test_no_false_settled_from_one_distinct_source():
    # The same authority retrieved twice is still a single source.
    res = uncertainty.classify(["Act 29 governs rape."], [ACT29, dict(ACT29)])
    assert res[0]["status"] == uncertainty.PROBABLE
    assert res[0]["status"] != uncertainty.SETTLED


def test_two_distinct_authorities_settle():
    res = uncertainty.classify(
        ["Act 29 and the 1992 Constitution both apply."], [ACT29, CONST])
    assert res[0]["status"] == uncertainty.SETTLED
    assert set(res[0]["authorities"]) == {"Act 29", "1992 Constitution"}


# ---------------------------------------------------------------------------
# conflicting authorities -> disputed
# ---------------------------------------------------------------------------

def test_repealed_conflicting_authority_is_disputed():
    res = uncertainty.classify(
        ["Act 29 and Act 100 govern this offence."], [ACT29, REPEALED])
    assert res[0]["status"] == uncertainty.DISPUTED
    assert "Act 100" in res[0]["contrary"]
    assert "Act 29" in res[0]["authorities"]


def test_explicit_contrary_stance_is_disputed():
    contrary = _doc("Act 500", title="Excluding Act", stance="contrary")
    res = uncertainty.classify(
        ["Act 29 applies but Act 500 says otherwise."], [ACT29, contrary])
    assert res[0]["status"] == uncertainty.DISPUTED


def test_only_contrary_authority_is_unresolved_not_probable():
    contrary = _doc("Act 500", title="Excluding Act", stance="contrary")
    res = uncertainty.classify(["Act 500 alone governs this."], [contrary])
    assert res[0]["status"] == uncertainty.UNRESOLVED
    assert res[0]["authorities"] == []


# ---------------------------------------------------------------------------
# no authority -> unresolved
# ---------------------------------------------------------------------------

def test_no_authority_is_unresolved():
    res = uncertainty.classify(
        ["Quantum entanglement governs the penalty."], [ACT29])
    assert res[0]["status"] == uncertainty.UNRESOLVED
    assert res[0]["authorities"] == []


def test_empty_docs_is_unresolved():
    res = uncertainty.classify(["Anything at all about the matter."], [])
    assert res[0]["status"] == uncertainty.UNRESOLVED


def test_unsupported_invented_citation_cannot_settle():
    res = uncertainty.classify(
        ["Act 29 and Act 5555 establish the rule."], [ACT29])
    assert res[0]["status"] == uncertainty.PROBABLE
    assert res[0]["status"] != uncertainty.SETTLED
    assert "5555" in res[0]["reason"] or "unverifiable" in res[0]["reason"].lower()


# ---------------------------------------------------------------------------
# missing facts
# ---------------------------------------------------------------------------

def test_missing_facts_are_flagged_without_model():
    res = uncertainty.classify(
        [{"text": "The penalty depends on the offender's age.", "missing_facts": True}],
        [ACT29])
    assert res[0]["status"] == uncertainty.MISSING_FACTS


def test_inline_missing_fact_marker_is_detected():
    res = uncertainty.classify(
        ["Whether the accused was a minor [missing fact] matters here."], [ACT29])
    assert res[0]["status"] == uncertainty.MISSING_FACTS


# ---------------------------------------------------------------------------
# aggregation helpers
# ---------------------------------------------------------------------------

def test_summarize_counts_and_overall():
    results = uncertainty.classify(
        ["Act 29 governs."], [ACT29])
    s = uncertainty.summarize(results)
    assert s["counts"][uncertainty.PROBABLE] == 1
    assert s["overall"] == uncertainty.PROBABLE
    assert 0.0 <= s["confidence"] <= 0.95


def test_summarize_dispute_dominates_and_caps_confidence():
    results = uncertainty.classify(
        ["Act 29 and Act 100 govern."], [ACT29, REPEALED])
    s = uncertainty.summarize(results)
    assert s["overall"] == uncertainty.DISPUTED
    assert s["confidence"] <= 0.5


def test_empty_propositions_summarize_unresolved():
    s = uncertainty.summarize([])
    assert s["overall"] == uncertainty.UNRESOLVED
    assert s["confidence"] == 0.0
