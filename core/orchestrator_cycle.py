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
# 2026-08-07: gpu_lifecycle removed — RunPod pods decommissioned.
import core.vpn_failover as vpn_failover
# 2026-08-09: Notification Service — Kai Mobile Command Node sub-project 3
import core.notifications as notifications


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


def _safe_check_rotation():
    """AI-7: Check for credential rotation needs and log (alert-only)."""
    try:
        from core.ai.credential_vault import check_rotation_needed
        due = check_rotation_needed()
        if due:
            providers = [d["provider"] for d in due]
            info(f"credential rotation due for: {', '.join(providers)}")
    except Exception as error:
        info(f"credential rotation check failed: {type(error).__name__}")


def _sync_workforce():
    """Register/sync all worker classes in the workforce registry. Guarded:
    registry problems must never break the cycle."""
    try:
        from core.workforce.bootstrap import run_full_sync
        from core.build_manager import MAX_CONCURRENT_BUILDS
        counts = run_full_sync(max_concurrent_builds=MAX_CONCURRENT_BUILDS)
        if any(counts.values()):
            info(f"workforce sync: {counts}")
    except Exception as error:
        info(f"workforce sync failed: {type(error).__name__}")


def run_cycle():

    info("=== orchestrator cycle started ===")

    # Workforce registry sync FIRST (2026-08-22 spec §1): identity records
    # must exist before any delegate gate / starvation check this cycle.
    _sync_workforce()

    # 2026-08-07: GPU lifecycle/heartbeat/auto-recovery removed —
    # RunPod pods decommissioned.

    # AI-7: Credential Vault rotation check
    _safe_check_rotation()

    # TK-176d6efe: VPN failover — check WireGuard tunnel to Proxmox B and
    # attempt recovery if the tunnel is down.
    vpn_events = []
    try:
        vpn_events = vpn_failover.attempt_recovery()
        for evt in vpn_events:
            if evt.get("severity") == "critical":
                info(f"VPN FAILOVER CRITICAL: {evt.get('message', '')}")
            elif evt.get("severity") == "warning":
                info(f"VPN FAILOVER WARNING: {evt.get('message', '')}")
            else:
                info(
                    f"vpn failover: {evt.get('type')} "
                    f"attempt={evt.get('attempt', '-')} "
                    f"success={evt.get('success', '-')}"
                )
    except Exception as error:
        info(f"vpn failover check failed: {type(error).__name__}")

    # 2026-08-09: Enqueue VPN events as notifications (safe — non-fatal)
    if vpn_events:
        try:
            vpn_count = notifications.enqueue_from_vpn_events(vpn_events)
            if vpn_count:
                info(f"notifications: {vpn_count} VPN event(s) enqueued")
        except Exception as error:
            info(f"notifications: VPN event enqueue failed: {type(error).__name__}")

    state = refresh_state()


    findings = analyze()


    incidents = []

    for finding in findings:
        # Skip info-only findings (risk_score=0, healthy status, etc.) —
        # they are observations, not actionable incidents.
        if finding.get("severity") == "info" or finding.get("risk_score", 0) == 0:
            continue

        incidents.append(
            create_incident(
                finding.get("service"),
                finding.get("issue"),
                finding.get("severity", "warning")
            )
        )

    # 2026-08-09: Enqueue health findings as notifications (safe — non-fatal)
    # Critical findings → Telegram push; warnings → heartbeat queue
    if findings:
        try:
            health_count = notifications.enqueue_from_findings(findings)
            if health_count:
                info(f"notifications: {health_count} health finding(s) enqueued")
        except Exception as error:
            info(f"notifications: health findings enqueue failed: {type(error).__name__}")


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
    #
    # V3.1: wrap with a hard timeout (900s / 15min) so a hung opencode
    # subprocess doesn't deadlock the entire cycle.  advance_roadmap() runs
    # BEFORE this call, so new phases are already spawned; the timeout just
    # means the next scheduler tick (300s later) gets a fresh cycle.
    import concurrent.futures as _cf
    with _cf.ThreadPoolExecutor(max_workers=1) as _timeout_pool:
        _future = _timeout_pool.submit(advance_builds)
        try:
            builds = _future.result(timeout=900)
        except _cf.TimeoutError:
            info("advance_builds timed out after 900s — cycle continues, builds still in flight")
            builds = load_builds()

    # Workforce starvation detection (2026-08-22 spec §2) — safe, alerting +
    # bounded concurrency boost only.
    try:
        from core.workforce.starvation import detect as _detect_starvation
        starve_events = _detect_starvation(builds or [])
        for evt in starve_events:
            info(f"workforce: {evt['action']} build={evt.get('name')}")
    except Exception as error:
        info(f"starvation detection failed: {type(error).__name__}")

    # After this cycle's builds have been advanced as far as they can go
    # without a human, flag any that are now stuck waiting -- this is the
    # only point where "stuck" is actually knowable (right after advancing).
    _safe_check_stale_approvals()

    # Check budget thresholds and send alerts if limits are exceeded
    # (alert-only: no automatic provider disabling)
    _safe_check_budget()

    # JARVIS P13: proactive/predictive engine — hourly, not every cycle.
    # Persists predictions to memory/kai_predictions.json; only warn/critical
    # findings reach notifications (dedupe + rate limiting upstream).
    try:
        import time as _p13time
        from core.memory import MEMORY_DIR
        stamp = MEMORY_DIR / ".proactive_last_run"
        now_s = _p13time.time()
        last = stamp.read_text().strip() if stamp.exists() else "0"
        if now_s - float(last or 0) >= 3600:
            from core.kai_proactive import run_cycle
            result = run_cycle(notify_threshold="warn")
            info(f"proactive cycle: {len(result['predictions'])} prediction(s), "
                 f"{result['actionable']} actionable")
            # world model refresh shares the same hourly gate (§27/§28 freshness)
            try:
                from core.world_model import build_snapshot
                build_snapshot()
                info("world model refreshed")
            except Exception as error:
                info(f"world model refresh failed: {type(error).__name__}")
            # recurring missions share the same hourly gate
            try:
                from core.kai_missions import run_due_schedules
                sched = run_due_schedules()
                if sched["ran"]:
                    info(f"scheduled missions ran: {sched['ran']}")
            except Exception as error:
                info(f"scheduled missions failed: {type(error).__name__}")
            stamp.write_text(str(now_s))
    except Exception as error:
        info(f"proactive cycle failed: {type(error).__name__}")


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

    # 2026-08-09: Enqueue build failure notifications (safe — non-fatal)
    # Detect builds that just transitioned to FAILED in this cycle
    try:
        builds_after_map = {b.get("id"): b for b in builds if b.get("id")}
        for b_before in builds_before:
            bid = b_before.get("id")
            if not bid:
                continue
            b_after = builds_after_map.get(bid)
            if b_after is None:
                continue
            if b_before.get("status") != "FAILED" and b_after.get("status") == "FAILED":
                name = b_after.get("name", bid)
                reason = b_after.get("failure_reason", "no reason recorded")
                notifications.enqueue_build_failure(name, bid, reason)
                info(f"notifications: build failure enqueued for {name}")
    except Exception as error:
        info(f"notifications: build failure enqueue failed: {type(error).__name__}")


    # 2026-08-07: GPU lifecycle/mgmt/dashboard removed — RunPod pods decommissioned.

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
        # 2026-08-07: gpu_events/gpu_metrics removed — RunPod pods decommissioned.

        # TK-176d6efe: VPN failover
        "vpn_events": vpn_events,

    }


    info("=== orchestrator cycle completed ===")


    return result



if __name__ == "__main__":

    print(run_cycle())
