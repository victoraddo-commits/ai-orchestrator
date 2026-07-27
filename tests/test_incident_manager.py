from core.incident_manager import create_incident, load_incidents


def test_create_incident_uses_normalized_string_id():
    incident = create_incident("svc-a", "Container unhealthy", "warning")

    assert isinstance(incident["id"], str)
    assert len(incident["id"]) == 8


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


def test_different_issue_on_same_service_creates_separate_incident():
    create_incident("proxmox-cluster", "CPU pressure detected: 91%", "warning")
    create_incident("proxmox-cluster", "Memory pressure detected: 92%", "warning")

    incidents = load_incidents()

    assert len(incidents) == 2


def test_resolved_incident_does_not_absorb_new_occurrence():
    from core.incident_lifecycle import mark_resolved

    first = create_incident("proxmox-health-score", "Healthy", "info")
    mark_resolved(first["id"])

    second = create_incident("proxmox-health-score", "Healthy", "info")

    assert second["id"] != first["id"]
    assert second["occurrences"] == 1
