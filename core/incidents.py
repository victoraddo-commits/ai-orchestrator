from core.memory import load, save
from datetime import datetime
import uuid


def create_incident(
    severity,
    service,
    issue
):

    incidents = load("incidents.json")

    if not isinstance(incidents, list):
        incidents = []


    incident = {
        "id": str(uuid.uuid4())[:8],
        "severity": severity,
        "service": service,
        "issue": issue,
        "created": datetime.now().isoformat(),
        "status": "open"
    }


    incidents.append(incident)

    save(
        "incidents.json",
        incidents
    )

    return incident



def list_incidents():

    incidents = load("incidents.json")

    if not isinstance(incidents, list):
        return []

    return incidents



if __name__ == "__main__":

    print(list_incidents())
