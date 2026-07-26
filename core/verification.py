from datetime import datetime

from core.health import analyze
from core.memory import load, save


def verify_service(service):

    findings = analyze()


    remaining = [

        f for f in findings

        if f.get("service") == service

    ]


    if remaining:

        status = "unresolved"

    else:

        status = "resolved"


    result = {

        "timestamp": datetime.now().isoformat(),

        "service": service,

        "status": status,

        "remaining_findings": remaining

    }


    history = load(
        "verification_history.json"
    )


    if not history:

        history = []


    history.append(result)


    save(
        "verification_history.json",
        history
    )


    return result



if __name__ == "__main__":

    print(
        verify_service(
            "pulse"
        )
    )
