from core.memory import load
from core.lifecycle_controller import (
    move_to_approved,
    move_to_executing
)


def sync_approvals():

    approvals = load(
        "approval_queue.json"
    ) or []


    results = []


    for approval in approvals:

        incident_id = approval.get(
            "incident"
        )


        if not incident_id:
            continue


        status = approval.get(
            "status"
        )


        if status == "approved":

            result = move_to_approved(
                str(incident_id)
            )

            results.append({
                "incident": incident_id,
                "transition": "approved",
                "result": result
            })


        elif status == "executing":

            result = move_to_executing(
                str(incident_id)
            )

            results.append({
                "incident": incident_id,
                "transition": "executing",
                "result": result
            })


    return results



if __name__ == "__main__":

    print(
        sync_approvals()
    )
