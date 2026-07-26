from core.incident_state_machine import transition


def move_to_investigating(incident_id):
    return transition(
        incident_id,
        "investigating"
    )


def move_to_approved(incident_id):
    return transition(
        incident_id,
        "approved"
    )


def move_to_executing(incident_id):
    return transition(
        incident_id,
        "executing"
    )


def move_to_verifying(incident_id):
    return transition(
        incident_id,
        "verifying"
    )


def move_to_resolved(incident_id):
    return transition(
        incident_id,
        "resolved"
    )


def move_to_closed(incident_id):
    return transition(
        incident_id,
        "closed"
    )
