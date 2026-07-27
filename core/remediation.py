from core.memory import load, save
from core.lifecycle import new_object, transition


ALLOWED_TRANSITIONS = {
    "queued": ["executing"],
    "executing": ["completed", "failed"],
    "completed": ["rolled_back"],
    "failed": ["executing", "rolled_back"],
    "rolled_back": []
}


ROLLBACK_STRATEGIES = {}


def register_rollback(action, strategy):
    ROLLBACK_STRATEGIES[action] = strategy


def load_remediations():

    remediations = load("remediation.json")

    if not isinstance(remediations, list):
        remediations = []

    return remediations


def save_remediations(remediations):

    save("remediation.json", remediations)


def create_remediation(approval_id, trace_id, action, service):

    remediations = load_remediations()

    remediation = new_object(
        "queued",
        trace_id=trace_id,
        approval_id=approval_id,
        action=action,
        service=service,
        snapshot=None,
        result=None
    )

    remediations.append(remediation)

    save_remediations(remediations)

    return remediation


def _find(remediations, remediation_id):

    for remediation in remediations:

        if remediation.get("id") == remediation_id:

            return remediation

    return None


def start_remediation(remediation_id, snapshot=None):

    remediations = load_remediations()

    remediation = _find(remediations, remediation_id)

    if remediation is None:
        return None

    transition(remediation, "executing", ALLOWED_TRANSITIONS)

    remediation["snapshot"] = snapshot

    save_remediations(remediations)

    return remediation


def complete_remediation(remediation_id, result):

    remediations = load_remediations()

    remediation = _find(remediations, remediation_id)

    if remediation is None:
        return None

    new_status = "completed" if result.get("status") == "success" else "failed"

    transition(remediation, new_status, ALLOWED_TRANSITIONS)

    remediation["result"] = result

    save_remediations(remediations)

    return remediation


def attempt_rollback(remediation_id):

    remediations = load_remediations()

    remediation = _find(remediations, remediation_id)

    if remediation is None:
        return None

    strategy = ROLLBACK_STRATEGIES.get(remediation.get("action"))

    if strategy is None:

        remediation["rollback"] = {
            "attempted": True,
            "available": False,
            "reason": f"No rollback strategy registered for action {remediation.get('action')!r}"
        }

        save_remediations(remediations)

        return remediation

    result = strategy(remediation)

    transition(
        remediation,
        "rolled_back",
        ALLOWED_TRANSITIONS,
        note="automatic rollback after failed verification"
    )

    remediation["rollback"] = {
        "attempted": True,
        "available": True,
        "result": result
    }

    save_remediations(remediations)

    return remediation
