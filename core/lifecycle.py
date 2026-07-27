from datetime import datetime

from core.id_generator import generate_id


class InvalidTransition(ValueError):
    """Raised when a status transition isn't in the object's allowed-transitions map."""


def new_object(status, trace_id=None, **fields):

    now = datetime.now().isoformat()
    object_id = generate_id()

    obj = {
        "id": object_id,
        "trace_id": trace_id or object_id,
        "status": status,
        "created": now,
        "updated": now,
        "history": [{"status": status, "timestamp": now}],
    }

    obj.update(fields)

    return obj


def transition(obj, new_status, allowed_transitions, note=None):

    current = obj.get("status")
    allowed = allowed_transitions.get(current, [])

    if new_status not in allowed:
        raise InvalidTransition(
            f"cannot transition from {current!r} to {new_status!r}"
        )

    now = datetime.now().isoformat()

    obj["status"] = new_status
    obj["updated"] = now

    entry = {"status": new_status, "timestamp": now}

    if note:
        entry["note"] = note

    obj.setdefault("history", []).append(entry)

    return obj
