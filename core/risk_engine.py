from datetime import datetime


SAFE_ACTIONS = [
    "restart_container",
    "restart_service"
]


def evaluate_risk(incident, recommendation):

    severity = incident.get(
        "severity",
        "unknown"
    )

    occurrences = incident.get(
        "occurrences",
        1
    )

    confidence = recommendation.get(
        "confidence",
        0
    )

    action = recommendation.get(
        "recommendation",
        "unknown"
    )


    risk_score = 50
    risk_level = "medium"
    auto_execute = False
    approval_required = True


    if action not in SAFE_ACTIONS:
        return {
            "risk_score": 100,
            "risk_level": "high",
            "auto_execute": False,
            "approval_required": True,
            "reason": "Unknown remediation action",
            "evaluated": datetime.now().isoformat()
        }


    if (
        severity == "critical"
        and confidence >= 85
        and occurrences < 10
    ):

        risk_score = 20
        risk_level = "low"
        auto_execute = True
        approval_required = False


    elif confidence >= 70:

        risk_score = 45
        risk_level = "medium"


    else:

        risk_score = 80
        risk_level = "high"


    return {
        "risk_score": risk_score,
        "risk_level": risk_level,
        "auto_execute": auto_execute,
        "approval_required": approval_required,
        "reason": (
            f"severity={severity}, "
            f"confidence={confidence}, "
            f"action={action}"
        ),
        "evaluated": datetime.now().isoformat()
    }


if __name__ == "__main__":

    test_incident = {
        "id": "5",
        "severity": "critical",
        "occurrences": 3
    }

    test_recommendation = {
        "recommendation": "restart_container",
        "confidence": 85
    }

    print(
        evaluate_risk(
            test_incident,
            test_recommendation
        )
    )
