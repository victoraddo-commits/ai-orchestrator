from core.memory import load
from core.execution_queue import get_pending
from core.agents.execution_guard import can_execute
from core.lifecycle_controller import (
    move_to_approved,
    move_to_executing,
    move_to_verifying,
    move_to_resolved,
    move_to_failed
)


def execute_autonomous_actions():

    decisions = get_pending()

    results = []


    for item in decisions:

        if item.get("decision") != "auto_execute":
            continue


        incident_id = str(
            item.get("incident")
        )


        incident = None

        for candidate in load("incidents.json") or []:
            if str(candidate.get("id")) == incident_id:
                incident = candidate
                break


        if not incident:
            results.append(
                {
                    "incident": incident_id,
                    "status": "blocked",
                    "reason": "incident_not_found"
                }
            )
            continue


        if not can_execute(incident):
            results.append(
                {
                    "incident": incident_id,
                    "status": "blocked",
                    "reason": f"invalid_state:{incident.get('status')}"
                }
            )
            continue


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
