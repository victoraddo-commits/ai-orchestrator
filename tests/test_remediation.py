import pytest

from core.remediation import (
    create_remediation,
    start_remediation,
    complete_remediation,
    load_remediations,
)
from core.lifecycle import InvalidTransition


def test_create_remediation_has_unified_lifecycle_fields():
    remediation = create_remediation(
        approval_id="appr1", trace_id="inc1", action="restart_container", service="svc"
    )

    assert len(remediation["id"]) == 8
    assert remediation["trace_id"] == "inc1"
    assert remediation["approval_id"] == "appr1"
    assert remediation["status"] == "queued"


def test_start_remediation_records_snapshot_and_moves_to_executing():
    remediation = create_remediation(
        approval_id="appr1", trace_id="inc1", action="restart_container", service="svc"
    )

    started = start_remediation(remediation["id"], snapshot={"before": "running"})

    assert started["status"] == "executing"
    assert started["snapshot"] == {"before": "running"}


def test_complete_remediation_success_transitions_to_completed():
    remediation = create_remediation(
        approval_id="appr1", trace_id="inc1", action="restart_container", service="svc"
    )
    start_remediation(remediation["id"], snapshot={"before": "running"})

    done = complete_remediation(remediation["id"], {"status": "success", "after": "running"})

    assert done["status"] == "completed"
    assert done["result"] == {"status": "success", "after": "running"}


def test_complete_remediation_failure_transitions_to_failed():
    remediation = create_remediation(
        approval_id="appr1", trace_id="inc1", action="restart_container", service="svc"
    )
    start_remediation(remediation["id"], snapshot={"before": "running"})

    done = complete_remediation(remediation["id"], {"status": "failed", "reason": "timeout"})

    assert done["status"] == "failed"


def test_cannot_complete_before_starting():
    remediation = create_remediation(
        approval_id="appr1", trace_id="inc1", action="restart_container", service="svc"
    )

    with pytest.raises(InvalidTransition):
        complete_remediation(remediation["id"], {"status": "success"})


def test_load_remediations_persists_across_calls():
    create_remediation(approval_id="a", trace_id="i", action="restart_container", service="s")
    create_remediation(approval_id="b", trace_id="j", action="restart_container", service="t")

    assert len(load_remediations()) == 2
