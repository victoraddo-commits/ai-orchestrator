from core.memory import load
from core.approval import create_request


def existing_request(action, service):

    requests = load(
        "approval_queue.json"
    )

    if not requests:
        return None


    for req in requests:

        if (
            req.get("action") == action
            and req.get("service") == service
            and req.get("status") in (
                "pending",
                "approved",
                "executing"
            )
        ):
            return req


    return None



def get_or_create_request(action, service, reason):

    existing = existing_request(
        action,
        service
    )


    if existing:

        return existing


    return create_request(
        action,
        service,
        reason
    )
