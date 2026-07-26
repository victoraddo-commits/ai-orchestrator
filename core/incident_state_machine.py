from datetime import datetime
from core.memory import load, save


ALLOWED_TRANSITIONS = {

    "open": [
        "investigating"
    ],

    "investigating": [
        "approved",
        "closed"
    ],

    "approved": [
        "executing",
        "closed"
    ],

    "executing": [
        "verifying",
        "failed"
    ],

    "verifying": [
        "resolved",
        "failed"
    ],

    "resolved": [
        "closed"
    ],

    "failed": [
        "investigating",
        "closed"
    ],

    "closed": []

}



def get_incidents():

    return load(
        "incidents.json"
    ) or []



def save_incidents(data):

    save(
        "incidents.json",
        data
    )



def transition(
    incident_id,
    new_state
):

    incidents = get_incidents()


    for incident in incidents:

        if str(incident["id"]) == str(incident_id):

            current = incident.get(
                "status",
                "open"
            )


            if new_state not in ALLOWED_TRANSITIONS.get(
                current,
                []
            ):

                return {
                    "status": "blocked",
                    "reason":
                    f"{current} cannot transition to {new_state}"
                }



            incident["status"] = new_state

            incident["updated"] = (
                datetime.now().isoformat()
            )


            save_incidents(
                incidents
            )


            return {
                "status": "success",
                "incident": incident
            }



    return {
        "status": "not_found"
    }



if __name__ == "__main__":

    print(
        transition(
            "5",
            "investigating"
        )
    )
