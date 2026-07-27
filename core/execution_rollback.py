from datetime import datetime
from core.memory import load, save


ROLLBACK_FILE = "execution_rollback.json"


def record_failure(event):

    logs = load(ROLLBACK_FILE) or []

    logs.append(
        {
            **event,
            "timestamp": datetime.now().isoformat()
        }
    )

    save(
        ROLLBACK_FILE,
        logs
    )


def history():

    return load(ROLLBACK_FILE) or []
