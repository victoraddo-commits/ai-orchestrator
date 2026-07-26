from datetime import datetime
from core.memory import save, load


def record(action, service, result):

    logs = load(
        "action_history.json"
    )


    if not logs:

        logs = []


    logs.append({

        "timestamp": datetime.now().isoformat(),

        "action": action,

        "service": service,

        "result": result

    })


    save(
        "action_history.json",
        logs
    )


    return logs[-1]
