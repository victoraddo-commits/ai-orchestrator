from core.approval_manager import get_or_create_request
from core.approval import load_requests
from core.memory import load, save
from core.lifecycle import new_object, transition


SAFE_ACTIONS = ("restart_container", "restart_service")

DECISION_TRANSITIONS = {
    "proposed": ["approved", "rejected"],
    "approved": ["executed"],
    "rejected": [],
    "executed": []
}


def load_decisions():

    decisions = load("decisions.json")

    if not isinstance(decisions, list):
        decisions = []

    return decisions


def save_decisions(decisions):

    save("decisions.json", decisions)


def analyze_incident(incident):

    severity = incident.get("severity", "unknown")
    occurrences = incident.get("occurrences", 1)

    recommended_action = "monitor"
    confidence = 50

    if severity == "critical" and occurrences >= 3:
        recommended_action = "restart_container"
        confidence = 85

    return {
        "recommended_action": recommended_action,
        "confidence": confidence,
        "reason": f"severity={severity}, occurrences={occurrences}"
    }


def evaluate_risk(incident, analysis):

    severity = incident.get("severity", "unknown")
    occurrences = incident.get("occurrences", 1)
    confidence = analysis.get("confidence", 0)
    action = analysis.get("recommended_action", "unknown")

    if action not in SAFE_ACTIONS:
        return {
            "risk_score": 100,
            "risk_level": "high",
            "auto_execute": False,
            "reason": "Unknown remediation action"
        }

    if severity == "critical" and confidence >= 85 and occurrences < 10:
        return {
            "risk_score": 20,
            "risk_level": "low",
            "auto_execute": True,
            "reason": f"severity={severity}, confidence={confidence}, action={action}"
        }

    if confidence >= 70:
        return {
            "risk_score": 45,
            "risk_level": "medium",
            "auto_execute": False,
            "reason": f"severity={severity}, confidence={confidence}, action={action}"
        }

    return {
        "risk_score": 80,
        "risk_level": "high",
        "auto_execute": False,
        "reason": f"severity={severity}, confidence={confidence}, action={action}"
    }


def find_open_decision(decisions, incident_id, action):

    for decision in reversed(decisions):

        if (
            decision.get("incident_id") == incident_id
            and decision.get("recommended_action") == action
            and decision.get("status") != "rejected"
        ):

            return decision

    return None


def find_request(requests, request_id):

    for request in requests:

        if request.get("id") == request_id:

            return request

    return None


def sync_decision_with_approval(decision, request):

    if request is None:
        return

    if decision["status"] == "proposed":

        if request["status"] == "approved":
            transition(decision, "approved", DECISION_TRANSITIONS)

        elif request["status"] == "rejected":
            transition(decision, "rejected", DECISION_TRANSITIONS)

    elif decision["status"] == "approved":

        if request["status"] == "executed":
            transition(decision, "executed", DECISION_TRANSITIONS)


def evaluate_incidents():

    incidents = load("incidents.json")

    if not incidents:
        return []

    decisions = load_decisions()
    requests = load_requests()

    made = []

    for incident in incidents:

        analysis = analyze_incident(incident)

        if analysis["recommended_action"] == "monitor":
            continue

        existing = find_open_decision(
            decisions, incident["id"], analysis["recommended_action"]
        )

        if existing:

            sync_decision_with_approval(
                existing, find_request(requests, existing.get("approval_id"))
            )

            made.append(existing)

            continue

        risk = evaluate_risk(incident, analysis)

        request = get_or_create_request(
            analysis["recommended_action"],
            incident["service"],
            f"Repeated {incident.get('severity')} incident: {incident['issue']}",
            incident["id"]
        )

        decision = new_object(
            "proposed",
            trace_id=incident["id"],
            incident_id=incident["id"],
            problem=incident.get("issue"),
            cause_probability=round(analysis["confidence"] / 100, 2),
            recommended_action=analysis["recommended_action"],
            confidence=analysis["confidence"],
            risk_score=risk["risk_score"],
            risk_level=risk["risk_level"],
            risk_auto_execute=risk["auto_execute"],
            requires_approval=True,
            approval_id=request["id"]
        )

        decisions.append(decision)
        made.append(decision)

    save_decisions(decisions)

    return made


if __name__ == "__main__":
    print(evaluate_incidents())
