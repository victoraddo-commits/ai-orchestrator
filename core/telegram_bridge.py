"""Thin Telegram Bot HTTP API client following the llm_clients.py pattern.

The scheduler runs as a plain daemon without Claude Code MCP tool access,
so this module calls https://api.telegram.org directly via requests.

Two directions:
- OUTBOUND: send_message(text) posts a human-readable status update to the
  allowed chat. State-change gating is the CALLER's responsibility (see
  _detect_state_changes in this module) -- this function just sends.
- INBOUND: poll_updates() returns new messages since the last poll.
  Matching and routing to submit_answer/approve_architecture/approve_deploy
  is the CALLER's responsibility (see _route_inbound_reply).

Config: reads KAI_TELEGRAM_BOT_TOKEN from ai-orchestrator's own .env file
(/project/ai-orchestrator/.env) -- a bot dedicated to Kai
(@KaiEnzo_bot, 2026-08-01), never duplicated or hardcoded. Deliberately NOT
the Claude Code Telegram plugin's shared TELEGRAM_BOT_TOKEN
(/root/.claude/channels/telegram/.env): confirmed live 2026-08-01 that
reusing that token caused real 409 Conflict collisions between this
module's poller and the plugin's own always-on getUpdates consumer --
Telegram only allows one active listener per bot token, so each consumer
needs its own bot. Chat ID and dmPolicy=allowlist enforcement are applied
on every operation.
"""

import json
import os
from pathlib import Path

import requests
from dotenv import load_dotenv

import core.build_manager as _bm


AI_ORCHESTRATOR_ENV_PATH = Path("/project/ai-orchestrator/.env")
ALLOWED_CHAT_ID = os.environ.get("KAI_TELEGRAM_CHAT_ID") or "612786480"


def _load_token():
    load_dotenv(AI_ORCHESTRATOR_ENV_PATH)
    token = os.environ.get("KAI_TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError(
            "KAI_TELEGRAM_BOT_TOKEN is not set; expected in "
            f"{AI_ORCHESTRATOR_ENV_PATH} or the environment"
        )
    return token


def _api_url(method, token):
    return f"https://api.telegram.org/bot{token}/{method}"


# ---------------------------------------------------------------------------
# Outbound: send a message to the allowed chat
# ---------------------------------------------------------------------------


def send_message(text, token=None, chat_id=None):
    if token is None:
        token = _load_token()
    if chat_id is None:
        chat_id = ALLOWED_CHAT_ID

    try:
        response = requests.post(
            _api_url("sendMessage", token),
            json={"chat_id": chat_id, "text": text},
            timeout=15,
        )
        response.raise_for_status()
        body = response.json()
    except Exception as error:
        raise RuntimeError(
            f"Telegram sendMessage failed: {type(error).__name__}"
        ) from error

    if not body.get("ok"):
        raise RuntimeError(
            f"Telegram sendMessage returned not ok: {body.get('description', 'unknown')}"
        )

    return body


# ---------------------------------------------------------------------------
# Inbound: poll /getUpdates for new messages since the last offset
# ---------------------------------------------------------------------------

_last_update_id = None


def _load_last_offset():
    try:
        path = Path("memory") / "telegram_last_update_id.txt"
        if path.exists():
            return int(path.read_text().strip())
    except Exception:
        pass
    return None


def _save_last_offset(update_id):
    try:
        Path("memory").mkdir(parents=True, exist_ok=True)
        Path("memory/telegram_last_update_id.txt").write_text(str(update_id))
    except Exception:
        pass


def _resolve_offset():
    global _last_update_id

    if _last_update_id is not None:
        return _last_update_id + 1

    _last_update_id = _load_last_offset()
    if _last_update_id is not None:
        return _last_update_id + 1

    return None


def poll_updates(token=None, chat_id=None, poll_timeout=0):
    # poll_timeout=0 (the default) is a short poll -- Telegram returns
    # immediately whether or not anything is waiting, which is what the
    # once-per-60s orchestrator cycle wants (it must not block its other
    # duties). core.telegram_poller passes a real long-poll value instead:
    # Telegram holds the connection open server-side until a message
    # arrives or the timeout elapses, so a dedicated tight loop gets replies
    # out in near-real-time instead of waiting for the next 60s tick.
    global _last_update_id

    if token is None:
        token = _load_token()
    if chat_id is None:
        chat_id = ALLOWED_CHAT_ID

    try:
        response = requests.get(
            _api_url("getUpdates", token),
            params={
                "offset": _resolve_offset(),
                "timeout": poll_timeout,
                "allowed_updates": json.dumps(["message"]),
            },
            # The HTTP client timeout must comfortably exceed Telegram's own
            # server-side long-poll window, or requests aborts the
            # connection right as a reply would have arrived.
            timeout=poll_timeout + 15,
        )
        response.raise_for_status()
        body = response.json()
    except Exception as error:
        raise RuntimeError(
            f"Telegram getUpdates failed: {type(error).__name__}"
        ) from error

    if not body.get("ok"):
        raise RuntimeError(
            f"Telegram getUpdates returned not ok: {body.get('description', 'unknown')}"
        )

    messages = []

    for update in body.get("result", []):
        update_id = update.get("update_id")
        msg = update.get("message") or {}

        _last_update_id = update_id

        msg_chat_id = str((msg.get("chat") or {}).get("id", ""))

        if msg_chat_id != chat_id:
            continue

        text = (msg.get("text") or "").strip()

        if not text:
            continue

        messages.append(
            {
                "update_id": update_id,
                "chat_id": msg_chat_id,
                "text": text,
                "from": {
                    "id": str((msg.get("from") or {}).get("id", "")),
                    "username": (msg.get("from") or {}).get("username", ""),
                    "first_name": (msg.get("from") or {}).get("first_name", ""),
                },
            }
        )

    if _last_update_id is not None:
        _save_last_offset(_last_update_id)

    return messages


def reset_offset():
    global _last_update_id

    _last_update_id = None
    try:
        path = Path("memory") / "telegram_last_update_id.txt"
        if path.exists():
            path.unlink()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Message formatting: human-readable, not raw JSON / log dumps
# ---------------------------------------------------------------------------

_WAITING_FOR_LABEL = {
    "WAITING_FOR_USER_INPUT": "Waiting for User Input",
    "WAITING_FOR_ARCHITECTURE_APPROVAL": "Waiting for Architecture Approval",
    "WAITING_FOR_DEPLOY_APPROVAL": "Waiting for Deploy Approval",
}

_STATE_LABEL = {
    "REQUESTED": "Requested",
    "PLANNING": "Planning",
    "WAITING_FOR_USER_INPUT": "Waiting for User Input",
    "WAITING_FOR_ARCHITECTURE_APPROVAL": "Waiting for Architecture Approval",
    "ARCHITECTURE_APPROVED": "Architecture Approved",
    "GENERATING": "Generating",
    "CODE_REVIEW": "Code Review",
    "TESTING": "Testing",
    "SECURITY_REVIEW": "Security Review",
    "WAITING_FOR_DEPLOY_APPROVAL": "Waiting for Deploy Approval",
    "DEPLOYING": "Deploying",
    "VERIFIED": "Verified",
    "COMPLETED": "Completed",
    "FAILED": "Failed",
    "ROLLED_BACK": "Rolled Back",
}


def format_state_change(build, previous_status=None):
    name = build.get("name", "unknown")
    status = build.get("status", "unknown")
    label = _STATE_LABEL.get(status, status)

    lines = [
        "\U0001f4e2 Kai Orchestrator Update",
        f"Build: {name}",
        f"Status: {label}",
    ]

    if previous_status is not None:
        prev_label = _STATE_LABEL.get(previous_status, previous_status)
        lines.append(f"Previous: {prev_label}")

    if status in _WAITING_FOR_LABEL:
        lines.append(f"Action needed: {_WAITING_FOR_LABEL[status]}")

    question = build.get("pending_question")
    if question and status == "WAITING_FOR_USER_INPUT":
        lines.append(f"Question: {question}")

    failure = build.get("failure_reason")
    if failure and status == "FAILED":
        lines.append(f"Reason: {failure}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# State-change detection: compare old and new build snapshots
# ---------------------------------------------------------------------------


def _build_state_key(build):
    return build.get("id", "")


def detect_state_changes(builds_before, builds_after):
    before_map = {}
    for b in builds_before:
        bid = _build_state_key(b)
        if bid:
            before_map[bid] = b.get("status")

    messages_to_send = []

    for b in builds_after:
        bid = _build_state_key(b)
        if not bid:
            continue

        new_status = b.get("status")
        old_status = before_map.get(bid)

        if old_status is None:
            # New build.
            messages_to_send.append(format_state_change(b))
        elif new_status != old_status:
            messages_to_send.append(format_state_change(b, previous_status=old_status))

    return messages_to_send


# ---------------------------------------------------------------------------
# Inbound reply routing: match an incoming Telegram message to the build
# that is currently awaiting human input, and call the correct
# submit_answer / approve_architecture / approve_deploy backend function.
# ---------------------------------------------------------------------------


_APPROVAL_PATTERNS = {"approve", "yes", "accept", "ok", "proceed", "go ahead", "y"}
_REJECT_PATTERNS = {"reject", "no", "deny", "cancel", "stop", "n"}


def _word_matches_any(text, word_set):
    lowered = text.strip().lower()
    return lowered in word_set


def _find_pending_build():
    builds = _bm.load_builds()
    pending = [
        b
        for b in builds
        if b.get("status")
        in {
            "WAITING_FOR_USER_INPUT",
            "WAITING_FOR_ARCHITECTURE_APPROVAL",
            "WAITING_FOR_DEPLOY_APPROVAL",
        }
    ]

    return pending


def _resolve_approval_intent(text):
    if _word_matches_any(text, _APPROVAL_PATTERNS):
        return "approve"
    if _word_matches_any(text, _REJECT_PATTERNS):
        return "reject"
    return None


def _operator_name(from_info):
    parts = []
    if from_info.get("first_name"):
        parts.append(from_info["first_name"])

    username = from_info.get("username")
    if username:
        parts.append(f"(@{username})")

    tg_id = from_info.get("id")
    if parts:
        parts.append(f"tg:{tg_id}")
    else:
        parts.append(f"tg:{tg_id}")

    return " ".join(parts)


def route_inbound_reply(message, pending_builds=None):
    if pending_builds is None:
        pending_builds = _find_pending_build()

    if not pending_builds:
        return {
            "routed": False,
            "reply": "No build is currently awaiting input.",
        }

    if len(pending_builds) > 1:
        names = [b.get("name", "?") for b in pending_builds]
        return {
            "routed": False,
            "reply": (
                f"Multiple builds are awaiting input: {', '.join(names)}. "
                "Please specify which build you are responding to."
            ),
        }

    build = pending_builds[0]
    status = build.get("status")
    from_info = message.get("from", {})
    operator = _operator_name(from_info)
    text = message.get("text", "")

    if status == "WAITING_FOR_USER_INPUT":
        updated = _bm.submit_answer(build["id"], text)
        return {
            "routed": True,
            "action": "submit_answer",
            "build_id": build["id"],
            "operator": operator,
            "reply": (
                f"Answer recorded for build {build.get('name')}. "
                "Resuming planning."
            ),
            "build": updated,
        }

    intent = _resolve_approval_intent(text)

    if status == "WAITING_FOR_ARCHITECTURE_APPROVAL":
        if intent == "approve":
            updated = _bm.approve_architecture(build["id"], operator=operator)
            return {
                "routed": True,
                "action": "approve_architecture",
                "build_id": build["id"],
                "operator": operator,
                "reply": f"Architecture approved for build {build.get('name')}.",
                "build": updated,
            }
        elif intent == "reject":
            updated = _bm.reject_architecture(build["id"], operator=operator)
            return {
                "routed": True,
                "action": "reject_architecture",
                "build_id": build["id"],
                "operator": operator,
                "reply": f"Architecture rejected for build {build.get('name')}.",
                "build": updated,
            }
        else:
            return {
                "routed": True,
                "build_id": build["id"],
                "reply": (
                    f"Build {build.get('name')} is waiting for architecture "
                    "approval. Reply 'approve' or 'reject'."
                ),
            }

    if status == "WAITING_FOR_DEPLOY_APPROVAL":
        if intent == "approve":
            updated = _bm.approve_deploy(build["id"], operator=operator)
            return {
                "routed": True,
                "action": "approve_deploy",
                "build_id": build["id"],
                "operator": operator,
                "reply": f"Deploy approved for build {build.get('name')}.",
                "build": updated,
            }
        elif intent == "reject":
            updated = _bm.reject_deploy(build["id"], operator=operator)
            return {
                "routed": True,
                "action": "reject_deploy",
                "build_id": build["id"],
                "operator": operator,
                "reply": f"Deploy rejected for build {build.get('name')}.",
                "build": updated,
            }
        else:
            return {
                "routed": True,
                "build_id": build["id"],
                "reply": (
                    f"Build {build.get('name')} is waiting for deploy "
                    "approval. Reply 'approve' or 'reject'."
                ),
            }

    return {
        "routed": False,
        "reply": f"Build {build.get('name')} is in state {status} -- no action taken.",
    }
