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
import core.telegram_bridge as telegram_bridge
from core.monitoring.budget_monitor import check_budgets
import core.gpu_lifecycle as gpu_lifecycle


def _safe_send(message_text):
    # 2026-08-02: returns the sent Telegram message_id on success (None on
    # failure) so the state-change loop can remember which build each
    # outbound message announced -- that link is what lets the operator
    # answer via Telegram's native reply-to instead of typing a build name.
    try:
        body = telegram_bridge.send_message(message_text)
    except Exception as error:
        info(f"telegram outbound failed: {type(error).__name__}")
        return None

    return (body.get("result") or {}).get("message_id")


def _safe_check_stale_approvals():
    try:
        check_stale_approvals()
    except Exception as error:
        info(f"stale approval check failed: {type(error).__name__}")

    try:
        check_stale_failures()
    except Exception as error:
        info(f"stale failure check failed: {type(error).__name__}")


def _safe_check_budget():
    try:
        check_budgets()
    except Exception as error:
        info(f"budget check failed: {type(error).__name__}")


def run_cycle():

    info("=== orchestrator cycle started ===")

    # V3: GPU lifecycle heartbeat — verify pod health and update activity
    try:
        gpu_lifecycle.heartbeat()
    except Exception as error:
        info(f"gpu heartbeat failed: {type(error).__name__}")


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

    # Spawn new builds FIRST (before the blocking advance_builds call).
    # advance_roadmap() now spawns up to MAX_CONCURRENT_BUILDS phases
    # per cycle; by running it before advance_builds, all spawned builds
    # are fed into the same ThreadPoolExecutor, so they all run their
    # opencode/vLLM subprocesses concurrently.
    roadmap_progress = advance_roadmap()

    # Single advance_builds() call processes all builds — existing GENERATING
    # builds AND the ones just spawned by advance_roadmap(). ThreadPoolExecutor
    # with max_workers=MAX_CONCURRENT_BUILDS runs them in parallel.
    builds = advance_builds()

    # After this cycle's builds have been advanced as far as they can go
    # without a human, flag any that are now stuck waiting -- this is the
    # only point where "stuck" is actually knowable (right after advancing).
    _safe_check_stale_approvals()

    # Check budget thresholds and send alerts if limits are exceeded
    # (alert-only: no automatic provider disabling)
    _safe_check_budget()


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


    for build_id, message_text in telegram_bridge.detect_state_changes_with_build_ids(
        builds_before, builds
    ):
        # 2026-08-06: for builds that just entered a WAITING_FOR_* state,
        # send an inline approval keyboard alongside the descriptive text.
        # The keyboard gives the operator one-tap approve/reject on their
        # phone — no typing required.
        build = next((b for b in builds if b.get("id") == build_id), None)
        pending_status = build.get("status", "") if build else ""

        keyboard_sent = False
        if pending_status == "WAITING_FOR_ARCHITECTURE_APPROVAL" and build:
            try:
                telegram_bridge.send_approval_keyboard(
                    telegram_bridge.ALLOWED_CHAT_ID,
                    build_id,
                    "architecture",
                )
                keyboard_sent = True
            except Exception as error:
                info(f"approval keyboard send failed: {type(error).__name__}")

        elif pending_status == "WAITING_FOR_DEPLOY_APPROVAL" and build:
            try:
                telegram_bridge.send_approval_keyboard(
                    telegram_bridge.ALLOWED_CHAT_ID,
                    build_id,
                    "deploy",
                )
                keyboard_sent = True
            except Exception as error:
                info(f"approval keyboard send failed: {type(error).__name__}")

        # The keyboard has the build name + action buttons; the descriptive
        # text has plan excerpts / security findings / question text — both
        # are needed for an informed decision from a phone.
        message_id = _safe_send(message_text)

        if message_id is not None and build_id:
            # Best-effort: a failed bookkeeping write must not take the
            # whole cycle down -- worst case the operator falls back to the
            # old "type which build you mean" flow for this one message.
            try:
                telegram_bridge.record_sent_build_message(message_id, build_id)
            except Exception as error:
                info(f"telegram message->build record failed: {type(error).__name__}")


    # V3: GPU lifecycle events
    gpu_events = []
    try:
        gpu_events = gpu_lifecycle.manage_gpu_lifecycle()
    except Exception as error:
        info(f"gpu lifecycle failed: {type(error).__name__}")

    # V3: GPU metrics for dashboard
    gpu_metrics = {}
    try:
        gpu_metrics = gpu_lifecycle.get_gpu_dashboard()
    except Exception as error:
        info(f"gpu metrics failed: {type(error).__name__}")


    result = {

        "state": state,

        "findings": findings,

        "incidents": incidents,

        "decisions": decisions,

        "builds": builds,

        "roadmap_progress": roadmap_progress,

        "remediation": remediation,

        "verification": verification,

        # V3 additions
        "gpu_events": gpu_events,
        "gpu_metrics": gpu_metrics,

    }


    info("=== orchestrator cycle completed ===")


    return result



if __name__ == "__main__":

    print(run_cycle())
