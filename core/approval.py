from core.memory import load, save
from core.lifecycle import new_object, transition


ALLOWED_TRANSITIONS = {
    "pending": ["approved", "rejected"],
    "approved": ["executed"],
    "rejected": [],
    "executed": []
}


def load_requests():

    requests = load("approval_queue.json")

    if not isinstance(requests, list):
        requests = []

    return requests


def save_requests(requests):

    save("approval_queue.json", requests)


def create_request(action, service, reason, incident_id=None):

    requests = load_requests()

    request = new_object(
        "pending",
        trace_id=incident_id,
        action=action,
        service=service,
        reason=reason,
        incident=incident_id
    )

    requests.append(request)

    save_requests(requests)

    return request


def list_pending():

    return [r for r in load_requests() if r.get("status") == "pending"]


def transition_request(request_id, new_status, note=None):

    requests = load_requests()

    for request in requests:

        if request.get("id") == request_id:

            transition(request, new_status, ALLOWED_TRANSITIONS, note=note)

            save_requests(requests)

            return request

    return None


def approve(request_id):
    return transition_request(request_id, "approved")


def reject(request_id):
    return transition_request(request_id, "rejected")


def mark_executed(request_id):
    return transition_request(request_id, "executed")


if __name__ == "__main__":

    print(
        create_request(
            "restart_container",
            "pulse",
            "Container unhealthy"
        )
    )
