import pytest

from core.incident_manager import (
    create_incident,
    load_incidents,
    transition_incident,
    mark_investigating,
    mark_approved,
    mark_executing,
    mark_verifying,
    mark_resolved,
    mark_closed,
)
from core.lifecycle import InvalidTransition


def test_create_incident_uses_normalized_string_id():
    incident = create_incident("svc-a", "Container unhealthy", "warning")

    assert isinstance(incident["id"], str)
    assert len(incident["id"]) == 8


def test_create_incident_sets_trace_id_and_history():
    incident = create_incident("svc-a", "Container unhealthy", "warning")

    assert incident["trace_id"] == incident["id"]
    assert incident["status"] == "open"
    assert incident["history"] == [
        {"status": "open", "timestamp": incident["created"]}
    ]


def test_two_incidents_never_collide_on_id():
    first = create_incident("svc-a", "Container unhealthy", "warning")
    second = create_incident("svc-b", "Disk pressure", "warning")

    assert first["id"] != second["id"]


def test_repeated_identical_finding_deduplicates_instead_of_appending():
    create_incident("proxmox-health-score", "Healthy", "info")
    create_incident("proxmox-health-score", "Healthy", "info")
    create_incident("proxmox-health-score", "Healthy", "info")

    incidents = load_incidents()

    matching = [
        i for i in incidents
        if i["service"] == "proxmox-health-score" and i["issue"] == "Healthy"
    ]

    assert len(matching) == 1
    assert matching[0]["occurrences"] == 3


def test_repeated_finding_appends_recurrence_to_history():
    create_incident("proxmox-health-score", "Healthy", "info")
    incident = create_incident("proxmox-health-score", "Healthy", "info")

    assert len(incident["history"]) == 2
    assert incident["history"][-1]["note"] == "recurrence"


def test_different_issue_on_same_service_creates_separate_incident():
    create_incident("proxmox-cluster", "CPU pressure detected: 91%", "warning")
    create_incident("proxmox-cluster", "Memory pressure detected: 92%", "warning")

    incidents = load_incidents()

    assert len(incidents) == 2


def test_resolved_incident_does_not_absorb_new_occurrence():
    first = create_incident("proxmox-health-score", "Healthy", "info")

    mark_investigating(first["id"])
    mark_approved(first["id"])
    mark_executing(first["id"])
    mark_verifying(first["id"])
    result = mark_resolved(first["id"])

    assert result["status"] == "success"
    assert result["incident"]["status"] == "resolved"

    second = create_incident("proxmox-health-score", "Healthy", "info")

    assert second["id"] != first["id"]
    assert second["occurrences"] == 1


def test_full_incident_lifecycle_reaches_closed():
    incident = create_incident("svc-a", "boom", "critical")

    mark_investigating(incident["id"])
    mark_approved(incident["id"])
    mark_executing(incident["id"])
    mark_verifying(incident["id"])
    mark_resolved(incident["id"])
    result = mark_closed(incident["id"])

    assert result["incident"]["status"] == "closed"
    statuses = [h["status"] for h in result["incident"]["history"]]
    assert statuses == [
        "open", "investigating", "approved", "executing",
        "verifying", "resolved", "closed",
    ]


def test_illegal_transition_is_rejected():
    incident = create_incident("svc-a", "boom", "critical")

    with pytest.raises(InvalidTransition):
        transition_incident(incident["id"], "resolved")


def test_transition_unknown_incident_reports_not_found():
    result = transition_incident("doesnotexist", "investigating")

    assert result == {"status": "not_found"}


def test_dedup_tolerates_legacy_incident_missing_status_field(isolated_memory):
    import json

    legacy = [{
        "id": "legacy01",
        "timestamp": "2026-01-01T00:00:00",
        "service": "proxmox-health-score",
        "issue": "Healthy",
        "severity": "info",
        "occurrences": 1
    }]
    (isolated_memory / "incidents.json").write_text(json.dumps(legacy))

    incident = create_incident("proxmox-health-score", "Healthy", "info")

    assert incident["id"] == "legacy01"
    assert incident["occurrences"] == 2
    assert incident["history"][-1]["note"] == "recurrence"
