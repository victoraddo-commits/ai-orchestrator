from core.action_policy import is_allowed
from core.action_log import record
from core.config import AUTONOMOUS_MODE
from core.approval import create_request


def execute(action, service, reason="No reason provided"):


    if not is_allowed(action, service):

        return record(
            action,
            service,
            "blocked"
        )


    if not AUTONOMOUS_MODE:

        request = create_request(
            action,
            service,
            reason
        )

        return record(
            action,
            service,
            f"approval-required:{request['id']}"
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
            "pulse",
            "Container unhealthy"
        )
    )
