from datetime import datetime, timedelta
import uuid

from core.memory import load, save


WINDOW_MINUTES = 30


SEVERITY_LEVELS = {
    "info": 1,
    "warning": 2,
    "critical": 3
}


def load_incidents():

    incidents = load(
        "incidents.json"
    )

    if not incidents:
        incidents = []

    return incidents



def save_incidents(incidents):

    save(
        "incidents.json",
        incidents
    )



def find_recent_failures(service):

    incidents = load_incidents()

    cutoff = datetime.now() - timedelta(
        minutes=WINDOW_MINUTES
    )


    return [

        i for i in incidents

        if i.get("service") == service
        and datetime.fromisoformat(
            i.get("timestamp")
        ) > cutoff

    ]



def calculate_severity(service, current):

    previous = find_recent_failures(
        service
    )


    count = len(previous) + 1


    if count >= 3:

        return "critical"


    if count == 2:

        return "warning"


    return current



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

        existing["timestamp"] = datetime.now().isoformat()

        existing["severity"] = severity

        save_incidents(incidents)

        return existing


    incident = {

        "id": str(uuid.uuid4())[:8],

        "timestamp":
            datetime.now().isoformat(),

        "service": service,

        "issue": issue,

        "severity": severity,

        "occurrences": 1,

        "status": "open"

    }


    incidents.append(
        incident
    )


    save_incidents(
        incidents
    )


    return incident



if __name__ == "__main__":

    print(
        create_incident(
            "pulse",
            "Container unhealthy",
            "warning"
        )
    )
