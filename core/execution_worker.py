from core.actions.container_actions import restart_container
from core.execution_policy import allowed


SUPPORTED_ACTIONS = [
    "restart_container",
    "monitor",
]


def execute_item(item):

    action = item.get("action")

    if action not in SUPPORTED_ACTIONS:
        return {
            "status": "failed",
            "reason": f"unsupported_action:{action}"
        }


    if action == "restart_container":

        return restart_container(
            item.get("service")
        )


    return {
        "status": "success",
        "action": action
    }
