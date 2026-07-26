from core.action_policy import is_allowed
from core.action_log import record


def execute(action, service):


    if not is_allowed(action, service):

        return record(
            action,
            service,
            "blocked"
        )


    result = "dry-run-success"


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
