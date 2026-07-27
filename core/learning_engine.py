from core.remediation_memory import get_history


def get_action_statistics(action):

    history = get_history()

    attempts = 0
    successes = 0
    failures = 0


    for entry in history:

        if entry.get("action") != action:
            continue

        attempts += 1

        if entry.get("result") == "success":
            successes += 1
        else:
            failures += 1


    if attempts == 0:
        success_rate = 0
    else:
        success_rate = round(
            (successes / attempts) * 100,
            2
        )


    confidence = min(
        95,
        int(success_rate)
    )


    return {
        "action": action,
        "attempts": attempts,
        "successes": successes,
        "failures": failures,
        "success_rate": success_rate,
        "confidence": confidence
    }



def evaluate_action(action):

    stats = get_action_statistics(
        action
    )

    if stats["attempts"] == 0:

        stats["recommendation"] = "insufficient_history"

    elif stats["success_rate"] >= 80:

        stats["recommendation"] = "trusted"

    elif stats["success_rate"] >= 50:

        stats["recommendation"] = "observe"

    else:

        stats["recommendation"] = "avoid"


    return stats



if __name__ == "__main__":

    print(
        evaluate_action(
            "restart_container"
        )
    )
