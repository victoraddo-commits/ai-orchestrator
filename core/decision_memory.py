from datetime import datetime
from core.memory import load, save


def get_decisions():

    return load(
        "decision_history.json"
    ) or []



def record_decision(decision):

    history = get_decisions()

    decision["timestamp"] = datetime.now().isoformat()

    history.append(
        decision
    )

    save(
        "decision_history.json",
        history
    )

    return decision



def get_recent(limit=10):

    history = get_decisions()

    return history[-limit:]


if __name__ == "__main__":

    print(
        record_decision(
            {
                "incident": "test",
                "action": "restart_container",
                "confidence": 88,
                "result": "executed"
            }
        )
    )
