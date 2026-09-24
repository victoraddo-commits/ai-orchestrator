"""Ask-to-Acquire wiring on CT 111 (Phase 7, Task 3).

Covers the grounding-side gap recording (UNGROUNDED / PARTIAL-without-primary)
and the scheduler pass's one-shot asker + operator notification.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.juris_kai import grounding  # noqa: E402
from scripts import legal_gap_acquire as ga  # noqa: E402


# ---------------------------------------------------------------------------
# grounding: gap recording
# ---------------------------------------------------------------------------

def test_needs_acquisition_verdicts():
    assert grounding.needs_acquisition("UNGROUNDED", [])
    assert grounding.needs_acquisition(
        "PARTIAL", [{"authority_level": "secondary"}])
    assert not grounding.needs_acquisition(
        "PARTIAL", [{"authority_level": "act"}])
    assert not grounding.needs_acquisition(
        "GROUNDED", [{"authority_level": "act"}])


def test_maybe_record_gap_records_ungrounded(monkeypatch):
    calls = []
    monkeypatch.setenv("KAI_LEGAL_GAP_RECORD", "1")
    monkeypatch.setattr(grounding, "record_gap_best_effort",
                        lambda q, a=None: calls.append((q, a)))

    recorded = grounding.maybe_record_gap("what % tint on cars?", "UNGROUNDED",
                                          [], asker="42", blocking=True)

    assert recorded is True
    assert calls == [("what % tint on cars?", "42")]


def test_maybe_record_gap_skips_grounded(monkeypatch):
    monkeypatch.setenv("KAI_LEGAL_GAP_RECORD", "1")
    monkeypatch.setattr(grounding, "record_gap_best_effort",
                        lambda *a, **k: (_ for _ in ()).throw(
                            AssertionError("must not record GROUNDED")))
    assert grounding.maybe_record_gap(
        "theft", "GROUNDED", [{"authority_level": "act"}],
        asker="42", blocking=True) is False


def test_maybe_record_gap_skips_without_asker(monkeypatch):
    monkeypatch.setenv("KAI_LEGAL_GAP_RECORD", "1")
    monkeypatch.setattr(grounding, "record_gap_best_effort",
                        lambda *a, **k: (_ for _ in ()).throw(
                            AssertionError("must not record without asker")))
    assert grounding.maybe_record_gap("q?", "UNGROUNDED", [], asker=None,
                                      blocking=True) is False


def test_maybe_record_gap_disabled_by_env(monkeypatch):
    monkeypatch.setenv("KAI_LEGAL_GAP_RECORD", "0")
    monkeypatch.setattr(grounding, "record_gap_best_effort",
                        lambda *a, **k: (_ for _ in ()).throw(
                            AssertionError("must not record when disabled")))
    assert grounding.maybe_record_gap("q?", "UNGROUNDED", [], asker="42",
                                      blocking=True) is False


# ---------------------------------------------------------------------------
# scheduler: notification
# ---------------------------------------------------------------------------

class FakeLB:
    def __init__(self):
        self.notified = []
        self.docs = {7: {"title": "Road Traffic Regulations, 2012 (LI 2180)"}}

    def get_document(self, doc_id):
        return self.docs.get(int(doc_id), {})

    def mark_gap_notified(self, gap_id):
        self.notified.append(gap_id)
        return {"ok": True}


def _gap(**kw):
    base = {"id": 1, "question": "what % tint is allowed on cars?",
            "asked_by": "42", "notified_at": None, "status": "filled",
            "filled_doc_ids": [7], "sources_tried": ["parliament-dspace"]}
    base.update(kw)
    return base


def _send_recorder(sent):
    def send(text, chat_id=None):
        sent.append((chat_id, text))
        return {"ok": True}
    return send


def test_notify_filled_sends_asker_and_operator(monkeypatch):
    fake = FakeLB()
    monkeypatch.setattr(ga, "lb", fake)
    sent = []

    out = ga.notify_gap(_gap(), send=_send_recorder(sent))

    assert out["notified"] is True
    assert fake.notified == [1]
    chats = [c for c, _ in sent]
    assert "42" in chats and None in chats
    asker_msg = [t for c, t in sent if c == "42"][0]
    assert "LI 2180" in asker_msg


def test_notify_never_twice(monkeypatch):
    fake = FakeLB()
    monkeypatch.setattr(ga, "lb", fake)
    sent = []

    out = ga.notify_gap(_gap(notified_at="2026-09-24T00:00:00Z"),
                        send=_send_recorder(sent))

    assert out.get("skipped") == "already notified"
    assert sent == [] and fake.notified == []


def test_notify_not_found_escalates_asker(monkeypatch):
    fake = FakeLB()
    monkeypatch.setattr(ga, "lb", fake)
    sent = []

    out = ga.notify_gap(_gap(status="not_found", filled_doc_ids=[]),
                        send=_send_recorder(sent))

    assert out["notified"] is True
    asker_msg = [t for c, t in sent if c == "42"][0]
    assert "escalated" in asker_msg.lower() or "couldn't find" in asker_msg.lower()


def test_run_pass_acquires_pending_then_notifies(monkeypatch):
    calls = {"acquire": [], "notify": []}

    class FakePassLB(FakeLB):
        def list_gaps(self, status=None, limit=50):
            if status == "pending":
                return [{"id": 1, "question": "tint?", "attempts": 1}]
            if status == "filled":
                return [_gap(notified_at=None)]
            return []

        def acquire_gap(self, gap_id, per_source=5, delay=0.5):
            calls["acquire"].append((gap_id, per_source, delay))
            return {"status": "filled", "doc_ids": [7]}

        def mark_gap_notified(self, gap_id):
            calls["notify"].append(gap_id)
            return {"ok": True}

    monkeypatch.setattr(ga, "lb", FakePassLB())
    sent = []

    report = ga.run_pass(limit=3, per_source=2, delay=0.5,
                         send=_send_recorder(sent))

    assert calls["acquire"] == [(1, 2, 0.5)]
    assert calls["notify"] == [1]
    assert report["counts"] == {"filled": 1}
    assert report["notified"]


# ---------------------------------------------------------------------------
# filled path, end-to-end at the acquire boundary (controlled fixture)
# ---------------------------------------------------------------------------
# Nothing on the live parliament-dspace lane was genuinely fillable at
# validation time (gap #3 returned needs_review: "only pre-existing documents
# matched"), so the `filled` path is proven here with a controlled fixture:
# a fake brain whose acquire genuinely "ingests" a lawfully-found enactment,
# then marks the gap filled and exposes the document for the notification.

class FilledBrain:
    """Fake brain that simulates ingest + mark_filled for one temp gap."""

    def __init__(self):
        self.corpus = {}
        self._next_doc = 100
        self.gaps = {
            9: {"id": 9, "question": "Electronic Communications Act, 2008",
                "asked_by": "42", "notified_at": None, "status": "pending",
                "filled_doc_ids": [], "sources_tried": []},
        }
        self.notified = []

    def list_gaps(self, status=None, limit=50):
        return [g for g in self.gaps.values()
                if status is None or g["status"] == status][:limit]

    def acquire_gap(self, gap_id, per_source=5, delay=0.5):
        gap = self.gaps[int(gap_id)]
        doc_id = self._next_doc
        self._next_doc += 1
        # Simulated ingest of the enactment the fixture source returned.
        self.corpus[doc_id] = {
            "title": "Electronic Communications Act, 2008 (Act 775)"}
        gap.update(status="filled", filled_doc_ids=[doc_id],
                   sources_tried=["parliament-dspace"])
        return {"ok": True, "gap_id": int(gap_id), "status": "filled",
                "doc_ids": [doc_id], "sources_tried": ["parliament-dspace"],
                "evidence": [{"source": "parliament-dspace", "found": 1,
                              "enactments": 1, "skipped_non_enactment": 0,
                              "ingested": [doc_id]}]}

    def get_document(self, doc_id):
        return self.corpus.get(int(doc_id), {})

    def mark_gap_notified(self, gap_id):
        self.notified.append(int(gap_id))
        self.gaps[int(gap_id)]["notified_at"] = "2026-09-24T17:00:00Z"
        return {"ok": True}


def test_filled_path_ingests_marks_filled_and_notifies(monkeypatch):
    brain = FilledBrain()
    monkeypatch.setattr(ga, "lb", brain)
    sent = []

    report = ga.run_pass(limit=3, per_source=2, delay=0.5,
                         send=_send_recorder(sent))

    # Ingest happened (doc now available) and the gap was marked filled.
    assert brain.corpus and brain.gaps[9]["status"] == "filled"
    assert brain.gaps[9]["filled_doc_ids"]
    # The pass reported the filled outcome with its evidence.
    assert report["counts"] == {"filled": 1}
    assert report["acquired"][0]["status"] == "filled"
    assert report["acquired"][0]["sources_tried"] == ["parliament-dspace"]
    # The notification fired (one-shot guard set) and names the instrument.
    assert brain.notified == [9]
    assert report["notified"][0]["notified"] is True
    assert any(chat == "42" for chat, _ in sent)
    assert "Act 775" in " ".join(t for _, t in sent)


def test_filled_path_does_not_notify_twice(monkeypatch):
    brain = FilledBrain()
    brain.gaps[9]["notified_at"] = "2026-09-24T17:00:00Z"
    monkeypatch.setattr(ga, "lb", brain)
    sent = []

    report = ga.run_pass(limit=3, per_source=2, delay=0.5,
                         send=_send_recorder(sent))

    assert brain.notified == []
    assert sent == []
    assert report["notified"] == []
