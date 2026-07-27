from datetime import datetime
from core.memory import load, save


QUEUE_FILE = "execution_queue.json"


def get_queue():

    return load(
        QUEUE_FILE
    ) or []



def enqueue(decision):

    queue = get_queue()

    for existing in queue:
        if (
            existing.get("incident") == decision.get("incident")
            and existing.get("action") == decision.get("action")
            and existing.get("status") in ["pending", "running"]
        ):
            return existing

    item = {
        "incident": decision.get("incident"),
        "service": decision.get("service"),
        "action": decision.get("action"),
        "confidence": decision.get("confidence"),
        "created": datetime.now().isoformat(),
        "status": "pending"
    }

    queue.append(item)

    save(
        QUEUE_FILE,
        queue
    )

    return item



def get_pending():

    return [
        item
        for item in get_queue()
        if item.get("status") == "pending"
    ]
