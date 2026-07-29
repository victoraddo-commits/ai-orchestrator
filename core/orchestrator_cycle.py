from core.state_manager import refresh_state
from core.health import analyze
from core.incident_manager import create_incident
from core.decision_engine import evaluate_incidents
from core.remediation_runner import process
from core.remediation import attempt_rollback
from core.verification import verify_service
from core.build_manager import advance_builds, load_builds
from core.roadmap_manager import advance_roadmap
from core.logger import info
from core.telegram_bridge import (
    detect_state_changes,
    poll_updates,
    route_inbound_reply,
    send_message,
)


def _safe_send(message_text):
    try:
        send_message(message_text)
    except Exception as error:
        info(f"telegram outbound failed: {type(error).__name__}")


def _safe_process_inbound():
    try:
        updates = poll_updates()
    except Exception as error:
        info(f"telegram inbound poll failed: {type(error).__name__}")
        return

    for msg in updates:
        try:
            result = route_inbound_reply(msg)
        except Exception as error:
            info(f"telegram inbound routing error: {type(error).__name__}")
            continue

        reply_text = result.get("reply")

        if reply_text:
            _safe_send(reply_text)


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


    # Poll and route any inbound Telegram replies before advancing builds,
    # so a human-answered question or approval is already applied when
    # advance_builds() decides what to do this cycle.
    _safe_process_inbound()


    builds_before = load_builds()


    advance_builds()


    roadmap_progress = advance_roadmap()


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


    for message_text in detect_state_changes(builds_before, builds):
        _safe_send(message_text)


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
