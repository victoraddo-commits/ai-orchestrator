from core.state_manager import refresh_state
from core.health import analyze
from core.decision import decide
from core.incidents import create_incident
from core.logger import info


def run():

    info("AI orchestrator cycle started")


    scan_result = refresh_state()


    findings = analyze()


    decisions = decide()


    incidents = []


    for finding in findings:

        incident = create_incident(
            finding.get("severity", "info"),
            finding.get("service", "unknown"),
            finding.get("issue", "unknown")
        )

        incidents.append(
            incident
        )


    result = {

        "scan": scan_result,

        "findings": findings,

        "decisions": decisions,

        "incidents": incidents

    }


    info(
        f"cycle complete. findings={len(findings)}"
    )


    return result


if __name__ == "__main__":

    print(run())
