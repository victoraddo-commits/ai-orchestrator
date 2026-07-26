from datetime import datetime

from core.memory import load, save


def get_incidents():

    incidents = load(
        "incidents.json"
    )

    if not incidents:
        return []

    return incidents



def update_incident(incident_id, status):

    incidents = get_incidents()

    for incident in incidents:

        if incident.get("id") == incident_id:

            incident["status"] = status

            incident["updated"] = (
                datetime.now().isoformat()
            )


    save(
        "incidents.json",
        incidents
    )


    return True



def get_active_incidents():

    incidents = get_incidents()

    return [
        i for i in incidents
        if i.get("status", "open")
        not in (
            "resolved",
            "closed"
        )
    ]



def mark_approved(incident_id):

    return update_incident(
        incident_id,
        "approved"
    )



def mark_executing(incident_id):

    return update_incident(
        incident_id,
        "executing"
    )



def mark_resolved(incident_id):

    return update_incident(
        incident_id,
        "resolved"
    )



def mark_closed(incident_id):

    return update_incident(
        incident_id,
        "closed"
    )



if __name__ == "__main__":

    print(
        get_active_incidents()
    )
