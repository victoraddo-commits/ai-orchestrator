from datetime import datetime

from core.autonomous_executor import execute_autonomous_actions


SUPPORTED_ACTIONS = [
    "restart_container",
    "monitor",
]


def execute_item(item):

    action = item.get(
        "action"
    )


    if action not in SUPPORTED_ACTIONS:

        return {
            "status": "failed",
            "reason": f"unsupported_action:{action}"
        }


    try:

        result = execute_autonomous_actions()


        return {
            "status": "success",
            "incident": item.get("incident"),
            "action": action,
            "executed": datetime.now().isoformat(),
            "result": result
        }


    except Exception as error:

        return {
            "status": "failed",
            "incident": item.get("incident"),
            "action": action,
            "error": str(error)
        }



if __name__ == "__main__":

    print(
        execute_item(
            {
                "incident": "5",
                "action": "restart_container"
            }
        )
    )
