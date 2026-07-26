from core.memory import load
from core.lifecycle_controller import (
    move_to_executing,
    move_to_verifying,
    move_to_resolved,
    move_to_failed
)
from core.remediation_runner import process


def execute_approved_actions():

    approvals = load(
        "approval_queue.json"
    ) or []


    results = []


    for approval in approvals:

        if approval.get("status") != "approved":
            continue


        incident_id = str(
            approval.get("incident")
        )


        action = approval.get(
            "action"
        )


        move_to_executing(
            incident_id
        )


        try:

            execution = process(
                approval
            )


            move_to_verifying(
                incident_id
            )


            verification = {
                "status": "verification_pending"
            }


            results.append({
                "incident": incident_id,
                "action": action,
                "execution": execution,
                "verification": verification
            })


        except Exception as e:

            move_to_failed(
                incident_id
            )


            results.append({
                "incident": incident_id,
                "status": "failed",
                "error": str(e)
            })


    return results



if __name__ == "__main__":

    print(
        execute_approved_actions()
    )
