ALLOWED_ACTIONS = (
    "restart_container",
)


def is_allowed(action, service):

    if action not in ALLOWED_ACTIONS:

        return False


    if not service:

        return False


    return True
