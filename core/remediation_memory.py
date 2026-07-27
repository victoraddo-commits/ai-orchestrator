from datetime import datetime
from core.memory import load, save


def get_history():

    return load(
        "remediation_history.json"
    ) or []



def record_result(
    incident,
    action,
    result,
    issue=None,
    root_cause=None
):

    history = get_history()

    entry = {
        "incident": incident,
        "action": action,
        "result": result,
        "issue": issue,
        "root_cause": root_cause,
        "timestamp": datetime.now().isoformat()
    }

    history.append(entry)

    save(
        "remediation_history.json",
        history
    )

    return entry



def get_action_success_rate(action):

    history = get_history()

    attempts = [
        item for item in history
        if item.get("action") == action
    ]

    if not attempts:
        return {
            "success_rate": 0,
            "attempts": 0
        }


    successes = len([
        item for item in attempts
        if item.get("result") == "success"
    ])


    return {
        "success_rate": round(
            successes / len(attempts) * 100,
            2
        ),
        "attempts": len(attempts)
    }



if __name__ == "__main__":

    print(
        record_result(
            "test",
            "restart_container",
            "success"
        )
    )

    print(
        get_action_success_rate(
            "restart_container"
        )
    )
