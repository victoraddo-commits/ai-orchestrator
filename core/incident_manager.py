from core.memory import load, save
from core.lifecycle import new_object, transition
from datetime import datetime


ALLOWED_TRANSITIONS = {
    "open": ["investigating"],
    "investigating": ["approved", "closed"],
    "approved": ["executing", "closed"],
    "executing": ["verifying", "failed"],
    "verifying": ["resolved", "failed"],
    "resolved": ["closed"],
    "failed": ["investigating", "closed"],
    "closed": []
}


def load_incidents():

    incidents = load("incidents.json")

    if not isinstance(incidents, list):
        incidents = []

    return incidents


def save_incidents(incidents):

    save("incidents.json", incidents)


def find_open_duplicate(incidents, service, issue):

    for incident in reversed(incidents):

        if (
            incident.get("service") == service
            and incident.get("issue") == issue
            and incident.get("status") not in ("closed", "resolved")
        ):

            return incident

    return None


def create_incident(service, issue, severity="info"):

    incidents = load_incidents()

    existing = find_open_duplicate(incidents, service, issue)

    if existing:

        existing["occurrences"] = existing.get("occurrences", 1) + 1
        existing["severity"] = severity
        existing.setdefault("status", "open")

        now = datetime.now().isoformat()
        existing["updated"] = now
        existing.setdefault("history", []).append(
            {"status": existing["status"], "timestamp": now, "note": "recurrence"}
        )

        save_incidents(incidents)

        return existing

    incident = new_object(
        "open",
        service=service,
        issue=issue,
        severity=severity,
        occurrences=1
    )

    incidents.append(incident)

    save_incidents(incidents)

    return incident


def transition_incident(incident_id, new_status, note=None):

    incidents = load_incidents()

    for incident in incidents:

        if str(incident.get("id")) == str(incident_id):

            transition(incident, new_status, ALLOWED_TRANSITIONS, note=note)

            save_incidents(incidents)

            return {"status": "success", "incident": incident}

    return {"status": "not_found"}


def mark_investigating(incident_id, note=None):
    return transition_incident(incident_id, "investigating", note=note)


def mark_approved(incident_id, note=None):
    return transition_incident(incident_id, "approved", note=note)


def mark_executing(incident_id, note=None):
    return transition_incident(incident_id, "executing", note=note)


def mark_verifying(incident_id, note=None):
    return transition_incident(incident_id, "verifying", note=note)


def mark_resolved(incident_id, note=None):
    return transition_incident(incident_id, "resolved", note=note)


def mark_failed(incident_id, note=None):
    return transition_incident(incident_id, "failed", note=note)


def mark_closed(incident_id, note=None):
    return transition_incident(incident_id, "closed", note=note)


def get_active_incidents():

    return [
        i for i in load_incidents()
        if i.get("status") not in ("closed", "resolved")
    ]


if __name__ == "__main__":

    print(
        create_incident(
            "pulse",
            "Container unhealthy",
            "warning"
        )
    )
