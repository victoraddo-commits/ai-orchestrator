from core.action_policy import is_allowed
from core.action_log import record
from core.config import AUTONOMOUS_MODE


def execute(action, service):

    if not is_allowed(action, service):

        return record(
            action,
            service,
            "blocked"
        )


    if not AUTONOMOUS_MODE:

        return record(
            action,
            service,
            "approval-required"
        )


    result = "executed"

    return record(
        action,
        service,
        result
    )


if __name__ == "__main__":

    print(
        execute(
            "restart_container",
            "pulse"
        )
    )
