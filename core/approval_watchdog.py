"""Reminds the operator via Telegram when a build has been sitting in a
human-gated waiting state for too long.

Root-caused live 2026-08-01: 17A's architecture-approval request sat
untouched for several hours (correctly -- the scheduler never auto-approves
a human gate) while attention was on an unrelated long task, silently
stalling the whole roadmap since only one self-modifying build is in
flight at a time. The gate itself was working exactly as designed; there
was just no signal that it had gone stale. This module is that signal.
"""

from datetime import datetime

from core.build_manager import load_builds
from core.memory import load, update
from core.telegram_bridge import send_message as _default_send_message

STATE_FILE = "stale_approval_reminders.json"

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
            send_message(
                f"⏳ Build {build.get('name', build_id)} has been waiting "
                f"{_format_duration(elapsed)} for {waiting_on} -- nothing else "
                f"on the roadmap can proceed behind this until it's reviewed."
            )
            reminded_now.append(build_id)
            state[build_id] = {"status": build["status"], "last_reminded": now.isoformat()}

        return state

    update(STATE_FILE, mutate)

    return reminded_now
