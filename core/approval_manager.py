from core.memory import load
from core.approval import create_request


def existing_request(action, service, incident_id):

    requests = load(
        "approval_queue.json"
    )

    if not requests:
        return None


    for req in requests:

        if (
            req.get("action") == action
            and req.get("service") == service
            and req.get("incident") == incident_id
            and req.get("status") in (
                "pending",
                "approved",
                "executing"
            )
        ):
            return req


    return None



def get_or_create_request(
    action,
    service,
    reason,
    incident_id
):

    existing = existing_request(
        action,
        service,
        incident_id
    )


    if existing:

        return existing


    request = create_request(
        action,
        service,
        reason
    )


    request["incident"] = incident_id

    return request
