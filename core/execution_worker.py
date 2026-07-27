from core.actions.container_actions import restart_container
from core.execution_policy import allowed
from core.execution_audit import record


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

        result = restart_container(
            item.get("service")
        )

        record(
            {
                "incident": item.get("incident"),
                "service": item.get("service"),
                "action": action,
                "result": result.get("status")
            }
        )

        return result


    return {
        "status": "success",
        "action": action
    }
