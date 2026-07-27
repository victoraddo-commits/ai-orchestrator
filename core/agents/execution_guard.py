VALID_EXECUTION_STATES = [
    "open",
    "approved"
]


def can_execute(incident):

    status = incident.get(
        "status"
    )

    return status in VALID_EXECUTION_STATES
