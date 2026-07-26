from core.health import analyze
from core.policy import check_action
from core.memory import save
from datetime import datetime


def decide():

    findings = analyze()

    decisions = []


    for finding in findings:

        severity = finding.get(
            "severity",
            "info"
        )

        service = finding.get(
            "service",
            "unknown"
        )

        issue = finding.get(
            "issue",
            "unknown issue"
        )


        action = "none"


        if "missing" in issue.lower():

            action = "investigate_service"


        elif "unhealthy" in issue.lower():

            action = "restart_service"


        policy = check_action(
            action,
            severity
        )


        decision = {

            "time": datetime.now().isoformat(),

            "service": service,

            "issue": issue,

            "recommended_action": action,

            "policy": policy

        }


        decisions.append(decision)


    save(
        "decisions.json",
        decisions
    )


    return decisions



if __name__ == "__main__":

    print(decide())
