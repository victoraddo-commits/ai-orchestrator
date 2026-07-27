from core.ai_reasoner import analyze_incident
from core.risk_engine import evaluate_risk
from core.memory import load


def evaluate_risk_decisions():

    incidents = load(
        "incidents.json"
    ) or []

    results = []


    for incident in incidents:

        recommendation = analyze_incident(
            incident
        )

        risk = evaluate_risk(
            incident,
            recommendation
        )


        if risk.get("auto_execute"):

            decision = "auto_execute"

        elif risk.get("approval_required"):

            decision = "requires_approval"

        else:

            decision = "blocked"


        results.append(
            {
                "incident": incident.get("id"),
                "recommendation": recommendation,
                "risk": risk,
                "decision": decision
            }
        )


    return results



if __name__ == "__main__":

    for result in evaluate_risk_decisions():

        print(result)
