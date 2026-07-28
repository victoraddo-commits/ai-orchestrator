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


def _transition_and_tag(request_id, new_status, note, tag_field, operator):

    requests = load_requests()

    for request in requests:

        if request.get("id") == request_id:

            transition(request, new_status, ALLOWED_TRANSITIONS, note=note)

            request[tag_field] = operator

            save_requests(requests)

            return request

    return None


def approve(request_id, note=None, operator=None):
    request = _transition_and_tag(request_id, "approved", note, "approved_by", operator)

    if request is not None:
        _resume_build_after_approval(request, operator=operator, note=note)

    return request


def reject(request_id, note=None, operator=None):
    request = _transition_and_tag(request_id, "rejected", note, "rejected_by", operator)

    if request is not None:
        _stop_build_after_rejection(request, operator=operator, note=note)

    return request


def mark_executed(request_id, note=None):
    return transition_request(request_id, "executed", note=note)


def create_build_approval(build_id, phase_id, approval_type, title, description, risk, requested_action):
    """Builds route their ARCHITECTURE_APPROVED/DEPLOY_APPROVAL gate decisions
    through this same remediation-approval queue/API/CloudCLI tab, rather than
    a separate build-only mechanism, so a human has one place to review every
    pending Kai decision. `action`/`service`/`reason` are populated too so the
    existing Approval Center row rendering (which only knows about those
    fields) still shows something meaningful for build approvals."""

    requests = load_requests()

    request = new_object(
        "pending",
        trace_id=build_id,
        build_id=build_id,
        phase_id=phase_id,
        approval_type=approval_type,
        title=title,
        description=description,
        risk=risk,
        requested_action=requested_action,
        action=requested_action,
        service=f"kai-build:{build_id}",
        reason=description,
        incident=None,
    )

    requests.append(request)
    save_requests(requests)

    return request


def _resume_build_after_approval(request, operator, note):
    build_id = request.get("build_id")

    if not build_id:
        return

    from core.build_manager import approve_architecture, approve_deploy, start_generation

    approval_type = request.get("approval_type")

    if approval_type == "architecture":
        approve_architecture(build_id, operator=operator, note=note)
        # Architecture approval alone only reaches ARCHITECTURE_APPROVED --
        # a build sitting there never gets picked up by advance_builds()'s
        # scheduler loop (it only dispatches REQUESTED/PLANNING/GENERATING/
        # DEPLOYING). Kicking off generation here is what "resume Kai
        # automatically" on approval actually means for this gate.
        start_generation(build_id)
    elif approval_type == "deploy":
        approve_deploy(build_id, operator=operator, note=note)


def _stop_build_after_rejection(request, operator, note):
    build_id = request.get("build_id")

    if not build_id:
        return

    from core.build_manager import reject_architecture, reject_deploy

    approval_type = request.get("approval_type")

    if approval_type == "architecture":
        reject_architecture(build_id, operator=operator, note=note)
    elif approval_type == "deploy":
        reject_deploy(build_id, operator=operator, note=note)


if __name__ == "__main__":

    print(
        create_request(
            "restart_container",
            "pulse",
            "Container unhealthy"
        )
    )
