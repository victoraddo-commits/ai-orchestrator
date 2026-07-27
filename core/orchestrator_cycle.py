from core.state_manager import refresh_state
from core.health import analyze
from core.incident_manager import create_incident
from core.decision_engine import evaluate_incidents
from core.remediation_runner import process
from core.verification import verify_service
from core.logger import info


def run_cycle():

    info("=== orchestrator cycle started ===")


    state = refresh_state()


    findings = analyze()


    incidents = []

    for finding in findings:

        incidents.append(
            create_incident(
                finding.get("service"),
                finding.get("issue"),
                finding.get("severity", "warning")
            )
        )


    decisions = evaluate_incidents()


    remediation = process()


    verification = []


    for item in remediation:

        verification.append(
            verify_service(
                item.get("service"),
                trace_id=item.get("trace_id")
            )
        )


    result = {

        "state": state,

        "findings": findings,

        "incidents": incidents,

        "decisions": decisions,

        "remediation": remediation,

        "verification": verification

    }


    info("=== orchestrator cycle completed ===")


    return result



if __name__ == "__main__":

    print(run_cycle())
