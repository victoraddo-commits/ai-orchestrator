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
