from core.media_factory import patterns


def test_next_states_from_hypothesis():
    assert patterns.next_states("HYPOTHESIS") == ["DECLINED", "EMERGING"]


def test_forward_transitions_allowed():
    assert patterns.can_transition("HYPOTHESIS", "EMERGING")
    assert patterns.can_transition("EMERGING", "VALIDATED")
    assert patterns.can_transition("VALIDATED", "STRATEGIC")
    assert patterns.can_transition("VALIDATED", "DECLINED")


def test_illegal_transitions_rejected():
    assert not patterns.can_transition("HYPOTHESIS", "VALIDATED")
    assert not patterns.can_transition("HYPOTHESIS", "STRATEGIC")
    assert not patterns.can_transition("STRATEGIC", "EMERGING")
    assert not patterns.can_transition("DECLINED", "VALIDATED")


def test_declined_is_terminal():
    assert patterns.next_states("DECLINED") == []


def test_advance_rejects_illegal_transition(monkeypatch):
    monkeypatch.setattr(patterns.db, "query_one",
                        lambda sql, params=(): {"id": 1, "state": "HYPOTHESIS", "evidence": {}})
    result = patterns.advance(1, "VALIDATED")
    assert result["status"] == "FAILED"
    assert "illegal transition" in result["blocked_reason"]
