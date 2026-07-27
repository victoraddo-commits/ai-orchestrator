import pytest

from core.approval import create_request, approve, reject, mark_executed, list_pending
from core.approval_manager import get_or_create_request
from core.lifecycle import InvalidTransition


def test_create_request_has_unified_lifecycle_fields():
    request = create_request("restart_container", "svc", "reason", incident_id="inc1")

    assert len(request["id"]) == 8
    assert request["trace_id"] == "inc1"
    assert request["status"] == "pending"
    assert request["history"] == [
        {"status": "pending", "timestamp": request["created"]}
    ]


def test_create_request_defaults_trace_id_to_own_id_when_no_incident():
    request = create_request("restart_container", "svc", "reason")

    assert request["trace_id"] == request["id"]


def test_approve_then_mark_executed_is_a_legal_path():
    request = create_request("restart_container", "svc", "reason", incident_id="inc1")

    approved = approve(request["id"])
    assert approved["status"] == "approved"

    executed = mark_executed(request["id"])
    assert executed["status"] == "executed"
    assert [h["status"] for h in executed["history"]] == ["pending", "approved", "executed"]


def test_reject_is_terminal():
    request = create_request("restart_container", "svc", "reason", incident_id="inc1")

    rejected = reject(request["id"])
    assert rejected["status"] == "rejected"

    with pytest.raises(InvalidTransition):
        approve(request["id"])


def test_cannot_mark_executed_before_approval():
    request = create_request("restart_container", "svc", "reason", incident_id="inc1")

    with pytest.raises(InvalidTransition):
        mark_executed(request["id"])


def test_list_pending_only_returns_pending():
    r1 = create_request("restart_container", "svc-a", "reason", incident_id="inc1")
    create_request("restart_container", "svc-b", "reason", incident_id="inc2")
    approve(r1["id"])

    pending = list_pending()

    assert len(pending) == 1
    assert pending[0]["service"] == "svc-b"


def test_get_or_create_request_dedupes_while_pending_or_approved():
    first = get_or_create_request("restart_container", "svc", "reason", "inc1")
    second = get_or_create_request("restart_container", "svc", "reason", "inc1")

    assert first["id"] == second["id"]

    approve(first["id"])
    third = get_or_create_request("restart_container", "svc", "reason", "inc1")
    assert third["id"] == first["id"]


def test_get_or_create_request_makes_new_one_after_executed():
    first = get_or_create_request("restart_container", "svc", "reason", "inc1")
    approve(first["id"])
    mark_executed(first["id"])

    second = get_or_create_request("restart_container", "svc", "reason", "inc1")

    assert second["id"] != first["id"]
