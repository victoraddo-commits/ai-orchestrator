from core.remediation_memory import get_history
from core.learning_engine import evaluate_action


def summarize():

    actions = {entry.get("action") for entry in get_history() if entry.get("action")}

    return {action: evaluate_action(action) for action in actions}
