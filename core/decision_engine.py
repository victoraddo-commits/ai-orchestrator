from core.approval_manager import get_or_create_request
from core.approval import load_requests, approve
from core.memory import load, save
from core.lifecycle import new_object, transition
from core.remediation_memory import get_action_success_rate
from core.action_policy import requires_approval as policy_requires_approval


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


MIN_TRACK_RECORD_ATTEMPTS = 3


def analyze_incident(incident, related_incidents=None):

    related_incidents = related_incidents or []

    severity = incident.get("severity", "unknown")
    occurrences = incident.get("occurrences", 1)

    recommended_action = "monitor"
    confidence = 50

    if severity == "critical" and occurrences >= 3:
        recommended_action = "restart_container"
        confidence = 85

    reasons = [f"severity={severity}", f"occurrences={occurrences}"]

    if recommended_action != "monitor":

        track_record = get_action_success_rate(recommended_action)

        if track_record["attempts"] >= MIN_TRACK_RECORD_ATTEMPTS:

            success_rate = track_record["success_rate"]

            if success_rate >= 80:
                confidence = min(confidence + 10, 95)
                reasons.append(f"{recommended_action} historically succeeds {success_rate}% of the time")

            elif success_rate < 50:
                confidence = max(confidence - 20, 10)
                reasons.append(f"{recommended_action} historically only succeeds {success_rate}% of the time")


        other_open_issues = {
            i.get("issue")
            for i in related_incidents
            if i.get("service") == incident.get("service")
            and i.get("id") != incident.get("id")
            and i.get("status") not in ("closed", "resolved")
        }

        if len(other_open_issues) >= 2:
            confidence = max(confidence - 10, 10)
            reasons.append(
                f"{len(other_open_issues)} other open issues on {incident.get('service')} "
                "make a single-cause fix less certain"
            )


    return {
        "problem": incident.get("issue"),
        "recommended_action": recommended_action,
        "confidence": confidence,
        "reason": ", ".join(reasons)
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

        analysis = analyze_incident(incident, related_incidents=incidents)

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

        approval_required = policy_requires_approval(analysis["recommended_action"])

        if not approval_required and request["status"] == "pending":
            approve(
                request["id"],
                note="auto-approved: low-risk action, autonomous mode enabled",
                operator="system(autonomous)"
            )

        decision = new_object(
            "proposed",
            trace_id=incident["id"],
            incident_id=incident["id"],
            problem=analysis["problem"],
            cause_probability=round(analysis["confidence"] / 100, 2),
            recommended_action=analysis["recommended_action"],
            confidence=analysis["confidence"],
            risk_score=risk["risk_score"],
            risk_level=risk["risk_level"],
            risk_auto_execute=risk["auto_execute"],
            requires_approval=approval_required,
            approval_id=request["id"],
            reason=analysis["reason"]
        )

        decisions.append(decision)
        made.append(decision)

    save_decisions(decisions)

    return made


if __name__ == "__main__":
    print(evaluate_incidents())
