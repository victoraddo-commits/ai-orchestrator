import pytest
from datetime import datetime, timedelta

from core.incident_manager import (
    create_incident,
    load_incidents,
    save_incidents,
    prune_incidents,
    transition_incident,
    mark_investigating,
    mark_approved,
    mark_executing,
    mark_verifying,
    mark_resolved,
    mark_closed,
    INCIDENT_ARCHIVE_FILE,
)
from core.memory import load
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


STABLE_ISSUE = "Telegram reminder delivery failing"
WINDOW = 6 * 60 * 60


def test_variable_detail_does_not_mint_new_incidents():
    t0 = datetime(2026, 9, 20, 9, 0, 0)

    for i, detail in enumerate([
        "RuntimeError: reminder 940h27m",
        "RuntimeError: reminder 940h30m",
        "RuntimeError: reminder 940h33m",
    ]):
        create_incident(
            "telegram", STABLE_ISSUE, severity="warning",
            detail=detail, cooldown_seconds=WINDOW,
            now=t0 + timedelta(minutes=i),
        )

    incidents = load_incidents()
    telegram = [i for i in incidents if i["service"] == "telegram"]

    assert len(telegram) == 1
    assert telegram[0]["issue"] == STABLE_ISSUE
    assert telegram[0]["occurrences"] == 3
    assert telegram[0]["detail"] == "RuntimeError: reminder 940h33m"


def test_cooldown_reopens_resolved_incident_instead_of_minting_a_new_one():
    t0 = datetime(2026, 9, 20, 9, 0, 0)

    first = create_incident("telegram", STABLE_ISSUE, cooldown_seconds=WINDOW, now=t0)
    mark_investigating(first["id"])
    mark_approved(first["id"])
    mark_executing(first["id"])
    mark_verifying(first["id"])
    mark_resolved(first["id"], note="flap")

    second = create_incident(
        "telegram", STABLE_ISSUE, severity="warning",
        detail="again", cooldown_seconds=WINDOW,
        now=t0 + timedelta(minutes=5),
    )

    assert second["id"] == first["id"]
    assert second["status"] == "open"
    assert second["occurrences"] == 2
    assert len(load_incidents()) == 1


def test_cooldown_expiry_allows_a_fresh_incident():
    t0 = datetime(2026, 9, 20, 9, 0, 0)

    first = create_incident("telegram", STABLE_ISSUE, cooldown_seconds=WINDOW, now=t0)
    mark_investigating(first["id"])
    mark_approved(first["id"])
    mark_executing(first["id"])
    mark_verifying(first["id"])
    mark_resolved(first["id"])

    second = create_incident(
        "telegram", STABLE_ISSUE, cooldown_seconds=WINDOW,
        now=t0 + timedelta(hours=7),
    )

    assert second["id"] != first["id"]
    assert second["occurrences"] == 1


def test_prune_archives_old_resolved_incidents(isolated_memory):
    now = datetime(2026, 9, 20, 12, 0, 0)
    old = {
        "id": "old00001", "trace_id": "old00001", "status": "resolved",
        "created": (now - timedelta(days=45)).isoformat(),
        "updated": (now - timedelta(days=45)).isoformat(),
        "service": "telegram", "issue": "stale", "severity": "warning",
        "occurrences": 1, "history": [],
    }
    fresh = {
        "id": "fresh001", "trace_id": "fresh001", "status": "resolved",
        "created": now.isoformat(), "updated": now.isoformat(),
        "service": "telegram", "issue": "recent", "severity": "warning",
        "occurrences": 1, "history": [],
    }
    save_incidents([old, fresh])

    summary = prune_incidents(resolved_older_than_days=30, now=now)

    remaining = load_incidents()
    assert summary["archived_resolved"] == 1
    assert {i["id"] for i in remaining} == {"fresh001"}
    archive = load(INCIDENT_ARCHIVE_FILE)
    assert [i["id"] for i in archive] == ["old00001"]


def test_prune_collapses_duplicate_open_incidents():
    now = datetime(2026, 9, 20, 12, 0, 0)
    duplicates = [
        {
            "id": f"dup{i:05d}", "trace_id": f"dup{i:05d}", "status": "open",
            "created": (now - timedelta(hours=3 - i)).isoformat(),
            "updated": (now - timedelta(hours=3 - i)).isoformat(),
            "service": "telegram", "issue": STABLE_ISSUE, "severity": "warning",
            "occurrences": 1, "history": [],
        }
        for i in range(3)
    ]
    save_incidents(duplicates)

    summary = prune_incidents(now=now)

    remaining = load_incidents()
    assert len(remaining) == 1
    assert remaining[0]["id"] == "dup00002"
    assert remaining[0]["occurrences"] == 3
    assert summary["collapsed_duplicates"] == 2
