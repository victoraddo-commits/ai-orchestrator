from core.state_manager import refresh_state
from core.health import analyze
from core.incident_manager import create_incident
from core.decision_engine import evaluate_incidents
from core.remediation_runner import process
from core.remediation import attempt_rollback
from core.verification import verify_service
from core.build_manager import advance_builds
from core.roadmap_manager import advance_roadmap
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


    advance_builds()


    roadmap_progress = advance_roadmap()


    # advance_roadmap() may have just created a build -- process it the same
    # cycle it's created rather than leaving it at REQUESTED for a full
    # extra INTERVAL until the next scheduled cycle picks it up. Safe to
    # call twice: advance_builds() only acts on builds in an immediately
    # actionable status (REQUESTED/PLANNING/GENERATING/DEPLOYING), so this
    # is a no-op for anything the first call already carried past that.
    builds = advance_builds()


    remediation = process()


    verification = []


    for item in remediation:

        result = verify_service(
            item.get("service"),
            trace_id=item.get("trace_id")
        )

        verification.append(result)

        if result.get("status") == "unresolved":
            attempt_rollback(item.get("remediation_id"))


    result = {

        "state": state,

        "findings": findings,

        "incidents": incidents,

        "decisions": decisions,

        "builds": builds,

        "roadmap_progress": roadmap_progress,

        "remediation": remediation,

        "verification": verification

    }


    info("=== orchestrator cycle completed ===")


    return result



if __name__ == "__main__":

    print(run_cycle())
