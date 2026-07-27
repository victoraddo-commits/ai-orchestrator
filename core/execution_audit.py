from datetime import datetime
from core.memory import load, save


AUDIT_FILE = "execution_audit.json"


def record(event):

    logs = load(AUDIT_FILE) or []

    logs.append(
        {
            **event,
            "timestamp": datetime.now().isoformat()
        }
    )

    save(
        AUDIT_FILE,
        logs
    )


def history():

    return load(AUDIT_FILE) or []
