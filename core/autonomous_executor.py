from core.risk_decision import evaluate_risk_decisions
from core.lifecycle_controller import (
    move_to_approved,
    move_to_executing,
    move_to_verifying,
    move_to_resolved,
    move_to_failed
)


def execute_autonomous_actions():

    decisions = evaluate_risk_decisions()

    results = []


    for item in decisions:

        if item.get("decision") != "auto_execute":
            continue


        incident_id = str(
            item.get("incident")
        )


        approval = move_to_approved(
            incident_id
        )


        if approval.get("status") != "success":
            results.append({
                "incident": incident_id,
                "status": "blocked",
                "reason": approval
            })
            continue


        execution = move_to_executing(
            incident_id
        )


        if execution.get("status") != "success":
            results.append({
                "incident": incident_id,
                "status": "failed",
                "reason": execution
            })
            continue


        verification = move_to_verifying(
            incident_id
        )


        resolved = move_to_resolved(
            incident_id
        )


        results.append({
            "incident": incident_id,
            "status": "completed",
            "execution": execution,
            "verification": verification,
            "resolution": resolved
        })


    return results



if __name__ == "__main__":

    for result in execute_autonomous_actions():
        print(result)
