from core.approval import create_request
from core.memory import load


def evaluate_incidents():

    incidents = load(
        "incidents.json"
    )

    if not incidents:
        return []


    decisions = []


    for incident in incidents:

        if (
            incident.get("severity") == "critical"
            and incident.get("occurrences", 0) >= 3
        ):

            request = create_request(
                "restart_container",
                incident["service"],
                f"Repeated critical incident: {incident['issue']}"
            )

            decisions.append({
                "incident": incident["id"],
                "action": "restart_container",
                "approval_id": request["id"]
            })


    return decisions


if __name__ == "__main__":
    print(evaluate_incidents())
