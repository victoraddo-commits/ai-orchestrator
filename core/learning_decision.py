from core.learning_engine import evaluate_action


def adjust_confidence(recommendation):

    action = recommendation.get(
        "recommendation"
    )

    if action == "monitor":
        return recommendation


    learning = evaluate_action(
        action
    )


    confidence = recommendation.get(
        "confidence",
        0
    )


    historical_confidence = learning.get(
        "confidence",
        0
    )


    adjusted = round(
        (confidence * 0.7) +
        (historical_confidence * 0.3)
    )


    recommendation["historical_confidence"] = historical_confidence

    recommendation["confidence"] = adjusted

    recommendation["learning_status"] = learning.get(
        "recommendation"
    )

    return recommendation


if __name__ == "__main__":

    sample = {
        "recommendation": "restart_container",
        "confidence": 85
    }

    print(
        adjust_confidence(
            sample
        )
    )
