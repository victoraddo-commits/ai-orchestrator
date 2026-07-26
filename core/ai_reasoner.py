from datetime import datetime


def analyze_incident(incident):

    severity = incident.get(
        "severity",
        "unknown"
    )

    occurrences = incident.get(
        "occurrences",
        1
    )

    recommendation = "monitor"

    confidence = 50

    risk = "medium"


    if severity == "critical" and occurrences >= 3:

        recommendation = "restart_container"
        confidence = 85
        risk = "low"


    return {

        "incident": incident.get("id"),

        "recommendation": recommendation,

        "confidence": confidence,

        "risk": risk,

        "reason":

            f"Severity={severity}, occurrences={occurrences}",

        "analyzed":

            datetime.now().isoformat()

    }


if __name__ == "__main__":

    print(
        analyze_incident(
            {
                "id":"test",
                "severity":"critical",
                "occurrences":5
            }
        )
    )
