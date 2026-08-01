from core.state_manager import refresh_state
from core.health import analyze
from core.incident_manager import create_incident
from core.decision_engine import evaluate_incidents
from core.remediation_runner import process
from core.remediation import attempt_rollback
from core.verification import verify_service
from core.build_manager import advance_builds, load_builds
from core.roadmap_manager import advance_roadmap
from core.approval_watchdog import check_stale_approvals, check_stale_failures
from core.logger import info
from core.telegram_bridge import detect_state_changes, send_message


def _safe_send(message_text):
    try:
        send_message(message_text)
    except Exception as error:
        info(f"telegram outbound failed: {type(error).__name__}")


def _safe_check_stale_approvals():
    try:
        check_stale_approvals()
    except Exception as error:
        info(f"stale approval check failed: {type(error).__name__}")

    try:
        check_stale_failures()
    except Exception as error:
        info(f"stale failure check failed: {type(error).__name__}")


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


    # Inbound Telegram messages are handled entirely by the dedicated
    # core.telegram_poller process (ai-orchestrator-telegram.service), which
    # now owns its own bot (@KaiEnzo_bot) with no other consumer -- see that
    # module's docstring. A build's answer/approval may already be applied
    # here as a result of a message that arrived seconds ago via that path.
    builds_before = load_builds()


    advance_builds()


    roadmap_progress = advance_roadmap()


    builds = advance_builds()


    # After this cycle's builds have been advanced as far as they can go
    # without a human, flag any that are now stuck waiting -- this is the
    # only point where "stuck" is actually knowable (right after advancing).
    _safe_check_stale_approvals()


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
