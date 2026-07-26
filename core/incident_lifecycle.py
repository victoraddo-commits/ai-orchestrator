from datetime import datetime
from core.memory import load, save


def get_incidents():

    return load(
        "incidents.json"
    ) or []



def save_incidents(data):

    save(
        "incidents.json",
        data
    )



def update_status(
    incident_id,
    status
):

    incidents = get_incidents()


    updated = None


    for incident in incidents:

        if str(incident.get("id")) == str(incident_id):

            incident["status"] = status

            incident["updated"] = (
                datetime.now()
                .isoformat()
            )

            updated = incident


    save_incidents(
        incidents
    )

    return updated



def get_active_incidents():

    incidents = get_incidents()

    return [
        i for i in incidents
        if i.get("status")
        not in (
            "closed",
            "resolved"
        )
    ]



def mark_analyzed(incident_id):

    return update_status(
        incident_id,
        "analyzed"
    )



def mark_approval_pending(incident_id):

    return update_status(
        incident_id,
        "approval_pending"
    )



def mark_executing(incident_id):

    return update_status(
        incident_id,
        "executing"
    )



def mark_resolved(incident_id):

    return update_status(
        incident_id,
        "resolved"
    )



def mark_closed(incident_id):

    return update_status(
        incident_id,
        "closed"
    )



if __name__ == "__main__":

    print(
        get_active_incidents()
    )
