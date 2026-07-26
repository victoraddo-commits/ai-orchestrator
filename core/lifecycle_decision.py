from core.decision_engine import evaluate_incidents
from core.memory import load
from core.lifecycle_controller import move_to_investigating


def get_incident(incident_id):

    incidents = load(
        "incidents.json"
    ) or []

    for incident in incidents:

        if str(incident.get("id")) == str(incident_id):
            return incident

    return None



def evaluate_with_lifecycle():

    decisions = evaluate_incidents()

    results = []


    for decision in decisions:

        incident_id = decision.get(
            "incident"
        )


        incident = get_incident(
            incident_id
        )


        if not incident:

            results.append({
                "incident": incident_id,
                "status": "missing"
            })

            continue


        if incident.get("status") == "open":

            lifecycle = move_to_investigating(
                str(incident_id)
            )

        else:

            lifecycle = {
                "status": "skipped",
                "reason": (
                    f"already {incident.get('status')}"
                )
            }


        results.append({

            "incident": incident_id,

            "decision": decision,

            "lifecycle": lifecycle

        })


    return results



if __name__ == "__main__":

    print(
        evaluate_with_lifecycle()
    )
