import pytest

from core.lifecycle import new_object, transition, InvalidTransition


def test_new_object_has_unique_id_trace_id_timestamps_status_history():
    obj = new_object("open", service="svc")

    assert len(obj["id"]) == 8
    assert obj["trace_id"] == obj["id"]
    assert obj["status"] == "open"
    assert obj["created"] == obj["updated"]
    assert obj["history"] == [{"status": "open", "timestamp": obj["created"]}]
    assert obj["service"] == "svc"


def test_new_object_can_be_given_an_explicit_trace_id():
    obj = new_object("pending", trace_id="parent12")

    assert obj["trace_id"] == "parent12"
    assert obj["id"] != obj["trace_id"]


def test_two_new_objects_never_collide_on_id():
    a = new_object("open")
    b = new_object("open")

    assert a["id"] != b["id"]


def test_transition_allows_legal_move_and_appends_history():
    obj = new_object("pending")

    transition(obj, "approved", {"pending": ["approved", "rejected"], "approved": []})

    assert obj["status"] == "approved"
    assert [h["status"] for h in obj["history"]] == ["pending", "approved"]
    assert obj["updated"] >= obj["created"]


def test_transition_rejects_illegal_move():
    obj = new_object("pending")

    with pytest.raises(InvalidTransition):
        transition(obj, "executed", {"pending": ["approved", "rejected"], "approved": []})

    assert obj["status"] == "pending"
    assert len(obj["history"]) == 1


def test_transition_records_optional_note():
    obj = new_object("open")

    transition(obj, "closed", {"open": ["closed"]}, note="manual close")

    assert obj["history"][-1]["note"] == "manual close"
