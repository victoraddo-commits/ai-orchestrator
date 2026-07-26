from datetime import datetime
import uuid

from core.memory import load, save


def create_request(action, service, reason):

    requests = load(
        "approval_queue.json"
    )

    if not requests:
        requests = []


    request = {

        "id": str(uuid.uuid4())[:8],

        "action": action,

        "service": service,

        "reason": reason,

        "status": "pending",

        "created": datetime.now().isoformat()

    }


    requests.append(request)


    save(
        "approval_queue.json",
        requests
    )


    return request



def list_pending():

    requests = load(
        "approval_queue.json"
    )

    return [
        r for r in requests
        if r.get("status") == "pending"
    ]



def approve(request_id):

    requests = load(
        "approval_queue.json"
    )


    for request in requests:

        if request.get("id") == request_id:

            request["status"] = "approved"

            save(
                "approval_queue.json",
                requests
            )

            return request


    return None



def reject(request_id):

    requests = load(
        "approval_queue.json"
    )


    for request in requests:

        if request.get("id") == request_id:

            request["status"] = "rejected"

            save(
                "approval_queue.json",
                requests
            )

            return request


    return None



if __name__ == "__main__":

    print(
        create_request(
            "restart_container",
            "pulse",
            "Container unhealthy"
        )
    )
