"""Reminds the operator via Telegram when a build has been sitting in a
human-gated waiting state for too long, or when a failed phase has gone
unaddressed.

Root-caused live 2026-08-01: 17A's architecture-approval request sat
untouched for several hours (correctly -- the scheduler never auto-approves
a human gate) while attention was on an unrelated long task, silently
stalling the whole roadmap since only one self-modifying build is in
flight at a time. The gate itself was working exactly as designed; there
was just no signal that it had gone stale. This module is that signal.

Extended same day after an independent review (Fable) pointed out two
related gaps: FAILED phases got no reminder at all (same silent-stall
shape, different terminal state -- see check_stale_failures), and a
Telegram send failure here would itself fail silently (see
_send_with_incident_fallback).
"""

from datetime import datetime

from core.build_manager import load_builds, get_build
from core.roadmap_engine import load_roadmap
from core.memory import load, update
from core.telegram_bridge import send_message as _default_send_message
from core.incident_manager import create_incident

STATE_FILE = "stale_approval_reminders.json"
FAILURE_STATE_FILE = "stale_failure_reminders.json"

# A human gate this fresh doesn't need a nag yet -- give a normal review
# pass time to happen first.
STALE_THRESHOLD_SECONDS = 30 * 60

# Once flagged, don't re-nag every cycle (60s) -- only escalate again if it's
# still stuck after a real chunk of time has passed.
REMINDER_REPEAT_SECONDS = 2 * 60 * 60

WAITING_STATUSES = {
    "WAITING_FOR_ARCHITECTURE_APPROVAL": "architecture approval",
    "WAITING_FOR_DEPLOY_APPROVAL": "deploy approval",
    "WAITING_FOR_USER_INPUT": "an answer to its clarifying question",
}


def _parse_timestamp(value):
    return datetime.fromisoformat(value)


def _entered_current_status_at(build):
    history = build.get("history") or []
    status = build.get("status")

    for entry in reversed(history):
        if entry.get("status") == status:
            return _parse_timestamp(entry["timestamp"])

    return None


def _format_duration(seconds):
    hours, remainder = divmod(int(seconds), 3600)
    minutes = remainder // 60

    if hours:
        return f"{hours}h{minutes}m"

    return f"{minutes}m"


def _send_with_incident_fallback(send_message, text):
    """Send text; on failure, record a visible incident instead of just a
    log line, so a broken Telegram channel doesn't make this watchdog's own
    failures as silent as the problem it exists to catch. Returns whether
    the send actually succeeded -- callers must not mark something as
    "reminded" on a failed send, so it gets retried next cycle."""

    try:
        send_message(text)
        return True
    except Exception as error:
        create_incident(
            "telegram",
            f"Stale-approval/failure reminder could not be sent ({type(error).__name__}): {text}",
            severity="warning",
        )
        return False


def check_stale_approvals(now=None, send_message=None):
    """Scan builds for stale human-gated waits and send/refresh reminders.

    Returns the list of build ids reminded on this call (empty list is the
    normal, healthy case).
    """

    now = now or datetime.now()
    send_message = send_message or _default_send_message

    builds = load_builds()
    waiting = [b for b in builds if b.get("status") in WAITING_STATUSES]
    waiting_ids = {b["id"] for b in waiting}

    reminders = load(STATE_FILE)
    reminded_now = []

    def mutate(state):
        state = state if isinstance(state, dict) else {}

        # Drop reminders for builds that moved on -- keeps the file from
        # growing unbounded and lets a build get a fresh nag cycle if it
        # somehow ends up waiting again later.
        for build_id in list(state.keys()):
            if build_id not in waiting_ids:
                del state[build_id]

        for build in waiting:
            build_id = build["id"]
            entered_at = _entered_current_status_at(build)

            if entered_at is None:
                continue

            elapsed = (now - entered_at).total_seconds()

            if elapsed < STALE_THRESHOLD_SECONDS:
                continue

            record = state.get(build_id)
            already_reminded_for_this_status = (
                record is not None and record.get("status") == build["status"]
            )

            if already_reminded_for_this_status:
                last_reminded = _parse_timestamp(record["last_reminded"])

                if (now - last_reminded).total_seconds() < REMINDER_REPEAT_SECONDS:
                    continue

            waiting_on = WAITING_STATUSES[build["status"]]
            sent = _send_with_incident_fallback(
                send_message,
                f"⏳ Build {build.get('name', build_id)} has been waiting "
                f"{_format_duration(elapsed)} for {waiting_on} -- nothing else "
                f"on the roadmap can proceed behind this until it's reviewed.",
            )

            if not sent:
                continue

            reminded_now.append(build_id)
            state[build_id] = {"status": build["status"], "last_reminded": now.isoformat()}

        return state

    update(STATE_FILE, mutate)

    return reminded_now


def check_stale_failures(now=None, send_message=None):
    """Scan roadmap phases stuck in status="failed" and send/refresh
    reminders -- a failed phase is never auto-retried (by design, a human
    must look at it), but until today nothing signaled that one had gone
    unaddressed, same silent-stall shape as check_stale_approvals covers
    for human-gated waits.

    Returns the list of phase ids reminded on this call.
    """

    now = now or datetime.now()
    send_message = send_message or _default_send_message

    failed_phases = [p for p in load_roadmap()["phases"] if p.get("status") == "failed"]
    failed_ids = {p["id"] for p in failed_phases}

    reminded_now = []

    def mutate(state):
        state = state if isinstance(state, dict) else {}

        for phase_id in list(state.keys()):
            if phase_id not in failed_ids:
                del state[phase_id]

        for phase in failed_phases:
            phase_id = phase["id"]
            build = get_build(phase.get("build_id")) if phase.get("build_id") else None
            failed_at = _entered_current_status_at(build) if build else None

            if failed_at is None:
                continue

            elapsed = (now - failed_at).total_seconds()

            if elapsed < STALE_THRESHOLD_SECONDS:
                continue

            record = state.get(phase_id)

            if record is not None:
                last_reminded = _parse_timestamp(record["last_reminded"])

                if (now - last_reminded).total_seconds() < REMINDER_REPEAT_SECONDS:
                    continue

            reason = build.get("failure_reason") or "no reason recorded"
            sent = _send_with_incident_fallback(
                send_message,
                f"❌ Phase {phase_id} ({phase.get('name', phase_id)}) has been failed "
                f"and unaddressed for {_format_duration(elapsed)} -- {reason}. "
                f"It will not retry itself; needs a human decision (fix and requeue, "
                f"or leave failed).",
            )

            if not sent:
                continue

            reminded_now.append(phase_id)
            state[phase_id] = {"last_reminded": now.isoformat()}

        return state

    update(FAILURE_STATE_FILE, mutate)

    return reminded_now
