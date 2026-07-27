from core.health import analyze
from core.memory import load, save
from core.lifecycle import new_object, transition


VERIFICATION_TRANSITIONS = {
    "verifying": ["resolved", "unresolved"],
    "resolved": [],
    "unresolved": []
}


def load_verification_history():

    history = load("verification_history.json")

    if not isinstance(history, list):
        history = []

    return history


def save_verification_history(history):

    save("verification_history.json", history)


def verify_service(service, trace_id=None):

    findings = analyze()

    remaining = [f for f in findings if f.get("service") == service]

    record = new_object("verifying", trace_id=trace_id, service=service)

    final_status = "unresolved" if remaining else "resolved"

    transition(record, final_status, VERIFICATION_TRANSITIONS)

    record["remaining_findings"] = remaining

    history = load_verification_history()
    history.append(record)
    save_verification_history(history)

    return record


if __name__ == "__main__":

    print(
        verify_service(
            "pulse"
        )
    )
