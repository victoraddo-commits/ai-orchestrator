from datetime import datetime

from core.memory import load, save
from core.docker_actions import execute_action


def get_approved():

    requests = load(
        "approval_queue.json"
    )

    if not requests:
        return []

    return [
        r for r in requests
        if r.get("status") == "approved"
    ]



def update_request(request_id, status):

    requests = load(
        "approval_queue.json"
    )

    for req in requests:

        if req.get("id") == request_id:

            req["status"] = status

            req["updated"] = datetime.now().isoformat()


    save(
        "approval_queue.json",
        requests
    )



def process():

    results = []

    approved = get_approved()


    for request in approved:

        request_id = request["id"]

        update_request(
            request_id,
            "executing"
        )


        try:

            result = execute_action(
                request["action"],
                request["service"]
            )


            update_request(
                request_id,
                "completed"
            )


            results.append({

                "request_id": request_id,

                "status": "completed",

                "result": result

            })


        except Exception as e:

            update_request(
                request_id,
                "failed"
            )


            results.append({

                "request_id": request_id,

                "status": "failed",

                "error": str(e)

            })


    return results



if __name__ == "__main__":

    print(process())
