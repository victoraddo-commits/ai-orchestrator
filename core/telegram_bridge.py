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
import core.memory as _memory

# Imported lazily inside route_inbound_reply to avoid circular imports at
# module load time (core.api imports core.telegram_bridge indirectly through
# the orchestrator cycle; deferring the import breaks the cycle).
_handle_kai_chat = None
_KaiChatAllProvidersFailed = None


def _import_kai_chat():
    global _handle_kai_chat, _KaiChatAllProvidersFailed
    if _handle_kai_chat is None:
        from core.api import handle_kai_chat, KaiChatAllProvidersFailed  # noqa: PLC0415
        _handle_kai_chat = handle_kai_chat
        _KaiChatAllProvidersFailed = KaiChatAllProvidersFailed


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


def send_message(text, token=None, chat_id=None, reply_markup=None):
    if token is None:
        token = _load_token()
    if chat_id is None:
        chat_id = ALLOWED_CHAT_ID

    payload = {"chat_id": chat_id, "text": text}
    if reply_markup:
        payload["reply_markup"] = reply_markup

    try:
        response = requests.post(
            _api_url("sendMessage", token),
            json=payload,
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


def send_typing(chat_id=None, token=None):
    """17W: Send 'typing...' indicator via Telegram.  Non-blocking — best-effort.
    Telegram auto-clears the indicator after 5 seconds or on next message."""
    if token is None:
        token = _load_token()
    if chat_id is None:
        chat_id = ALLOWED_CHAT_ID

    try:
        requests.post(
            _api_url("sendChatAction", token),
            json={"chat_id": chat_id, "action": "typing"},
            timeout=5,
        )
    except Exception:
        pass  # typing indicator is a nice-to-have, never block on it


def send_approval_keyboard(chat_id, build_id, approval_type, token=None):
    """17W: Send a message with inline Approve/Reject buttons for a build
    approval.  Returns the sent message response for tracking."""
    if token is None:
        token = _load_token()

    keyboard = {
        "inline_keyboard": [[
            {"text": f"✅ Approve {approval_type}", "callback_data": f"approve:{build_id}:{approval_type}"},
            {"text": f"❌ Reject {approval_type}", "callback_data": f"reject:{build_id}:{approval_type}"},
        ]]
    }

    return send_message(
        f"*Approval needed* — {approval_type} for build `{build_id}`",
        token=token,
        chat_id=chat_id,
        reply_markup=keyboard,
    )


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
                # 2026-08-02: present only when the operator used Telegram's
                # native reply-to (long-press/swipe to quote a message).
                # route_inbound_reply uses it to resolve exactly which build
                # a reply targets when several are pending at once.
                "reply_to_message_id": (msg.get("reply_to_message") or {}).get(
                    "message_id"
                ),
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
# Sent-message -> build correlation: which build did Kai's message announce?
# ---------------------------------------------------------------------------

# 2026-08-02: typing out which build a plain "approve" refers to is painful
# on a phone, so instead the operator can long-press/swipe-reply to Kai's
# original approval/question message. That only works if we remember which
# build each outbound message announced -- this map (message_id -> build_id)
# is that memory. Persisted so a poller/scheduler restart between the
# notification going out and the operator replying doesn't lose the link.
_MESSAGE_BUILD_MAP_FILE = "telegram_message_builds.json"

# Telegram message_ids grow forever; without a cap this file would too.
# 200 comfortably covers any realistic backlog of still-answerable
# notifications -- a reply to something older just falls back to the
# ordinary pending-build matching, same as before this feature existed.
_MESSAGE_BUILD_MAP_LIMIT = 200


def record_sent_build_message(message_id, build_id):
    def mutate(state):
        state = state if isinstance(state, dict) else {}
        # JSON object keys are always strings, so normalize the int
        # message_id Telegram gives us here and in _build_id_for_message.
        state[str(message_id)] = build_id
        # dicts preserve insertion order, and memory_manager round-trips
        # through json which keeps it -- so the front of the map is always
        # the oldest entry.
        while len(state) > _MESSAGE_BUILD_MAP_LIMIT:
            state.pop(next(iter(state)))
        return state

    # Same flock critical section as ai_router._rotate_candidates (13R):
    # the scheduler and the dedicated poller are separate processes, so a
    # plain load+save could drop one side's writes.
    _memory.update(_MESSAGE_BUILD_MAP_FILE, mutate)


def _build_id_for_message(message_id):
    mapping = _memory.load(_MESSAGE_BUILD_MAP_FILE)
    if not isinstance(mapping, dict):
        return None
    return mapping.get(str(message_id))


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


# Telegram's hard message cap is 4096 chars -- this leaves plenty of room
# for the rest of the message (name/status/reply instructions) around a
# truncated plan excerpt. Full untruncated plan is always available via
# `python -m core.approval_cli list` / the dashboard -- this is a preview,
# not the system of record.
_PLAN_EXCERPT_CHARS = 1200

# Telegram-reply vocabulary a pending approval actually accepts (see
# _APPROVAL_PATTERNS / _REJECT_PATTERNS above) -- spelled out here so the
# operator doesn't have to go find approval_cli.py's syntax to act on a
# notification.
_APPROVAL_REPLY_HINT = "Reply \"yes\" to approve or \"no\" to reject."


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

    if status == "WAITING_FOR_ARCHITECTURE_APPROVAL":
        plan = (build.get("plan") or "").strip()
        if plan:
            excerpt = plan[:_PLAN_EXCERPT_CHARS]
            if len(plan) > _PLAN_EXCERPT_CHARS:
                excerpt += f"... ({len(plan) - _PLAN_EXCERPT_CHARS} more chars, see approval_cli/dashboard)"
            lines.append(f"\nProposed plan:\n{excerpt}")
        lines.append(f"\n{_APPROVAL_REPLY_HINT}")

    if status == "WAITING_FOR_DEPLOY_APPROVAL":
        security_report = build.get("security_report") or {}
        findings = security_report.get("total_findings")
        if findings is not None:
            severity = security_report.get("highest_severity")
            severity_note = f", highest severity: {severity}" if severity else ""
            lines.append(f"\nSecurity review: {findings} finding(s){severity_note}.")
        code_review = build.get("code_review") or {}
        if code_review.get("skipped"):
            lines.append(f"Code review skipped: {code_review.get('reason')}.")
        elif code_review.get("findings"):
            lines.append(f"Code review ({code_review.get('reviewer', 'advisory')}): {code_review.get('findings')}")
        lines.append(f"\n{_APPROVAL_REPLY_HINT}")

    failure = build.get("failure_reason")
    if failure and status == "FAILED":
        lines.append(f"Reason: {failure}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# State-change detection: compare old and new build snapshots
# ---------------------------------------------------------------------------


def _build_state_key(build):
    return build.get("id", "")


def _iter_state_changes(builds_before, builds_after):
    before_map = {}
    for b in builds_before:
        bid = _build_state_key(b)
        if bid:
            before_map[bid] = b.get("status")

    for b in builds_after:
        bid = _build_state_key(b)
        if not bid:
            continue

        new_status = b.get("status")
        old_status = before_map.get(bid)

        if old_status is None:
            # New build.
            yield b, format_state_change(b)
        elif new_status != old_status:
            yield b, format_state_change(b, previous_status=old_status)


def detect_state_changes(builds_before, builds_after):
    return [text for _, text in _iter_state_changes(builds_before, builds_after)]


def detect_state_changes_with_build_ids(builds_before, builds_after):
    # 2026-08-02: same detection as detect_state_changes, but the caller
    # (orchestrator cycle) also needs to know WHICH build each message is
    # about, so it can remember the sent Telegram message_id -> build_id
    # link for native reply-to disambiguation (record_sent_build_message).
    return [
        (build.get("id", ""), text)
        for build, text in _iter_state_changes(builds_before, builds_after)
    ]


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


_PENDING_STATUSES = {
    "WAITING_FOR_USER_INPUT",
    "WAITING_FOR_ARCHITECTURE_APPROVAL",
    "WAITING_FOR_DEPLOY_APPROVAL",
}


def _find_pending_build():
    builds = _bm.load_builds()
    pending = [b for b in builds if b.get("status") in _PENDING_STATUSES]

    return pending


def _get_build_list_with_numbers(pending_builds):
    """Generate a list of builds with numbered prefixes for disambiguation."""
    build_list = []
    for i, build in enumerate(pending_builds, 1):
        name = build.get("name", "unknown")
        build_id = build.get("id", "")
        project_path = build.get("project_path", "")
        
        # Create a distinguishable representation that includes build ID and project path
        if project_path:
            display_name = f"{name} ({project_path})"
        else:
            display_name = name
            
        build_list.append({
            "number": i,
            "name": name,
            "display_name": display_name,
            "build_id": build_id,
            "build": build
        })
    
    return build_list


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


def _build_from_reply_to(message):
    # 2026-08-02: when the operator used Telegram's native reply-to feature
    # to quote one of Kai's build notifications, that quote pins down
    # exactly which build they mean -- no need to type the build name on a
    # phone, and no ambiguity even with several builds pending at once.
    reply_to_message_id = message.get("reply_to_message_id")
    if reply_to_message_id is None:
        return None

    build_id = _build_id_for_message(reply_to_message_id)
    if not build_id:
        return None

    build = _bm.get_build(build_id)
    if build is None:
        return None

    # A reply to an old notification for a build that has since moved on
    # (answered/approved via another surface) must not hijack routing --
    # fall back to the normal pending-build matching instead of erroring.
    if build.get("status") not in _PENDING_STATUSES:
        return None

    return build


def route_inbound_reply(message, pending_builds=None):
    # 17W: Send typing indicator so the operator sees Kai is working
    chat_id = str((message.get("chat") or {}).get("id", ALLOWED_CHAT_ID))
    send_typing(chat_id=chat_id)

    if pending_builds is None:
        replied_build = _build_from_reply_to(message)
        if replied_build is not None:
            # Bypasses _find_pending_build() and the "Multiple builds are
            # awaiting input" branch entirely -- the whole point is that a
            # native reply routes correctly even when OTHER builds are also
            # pending.
            pending_builds = [replied_build]
        else:
            pending_builds = _find_pending_build()

    if not pending_builds:
        # No build is awaiting input -- fall through to the full Kai chat
        # experience rather than returning a dead-end static error. This is
        # the 17K path: Telegram becomes a genuine equal-access surface for
        # open-ended chat, commands, and build requests (same handler as
        # POST /kai/chat).
        from_info = message.get("from", {})
        operator = _operator_name(from_info)
        text = (message.get("text") or "").strip()
        if not text:
            return {"routed": False, "reply": "Empty message."}

        _import_kai_chat()
        try:
            reply = _handle_kai_chat(text, operator)
        except Exception as exc:
            return {"routed": False, "reply": f"Chat error: {exc}"}

        # Extract the human-readable response string from the reply dict.
        if reply.get("response") is not None:
            reply_text = str(reply["response"])
        elif reply.get("result") is not None:
            result = reply["result"]
            reply_text = result if isinstance(result, str) else json.dumps(result, indent=2, default=str)
        elif reply.get("error"):
            reply_text = str(reply["error"])
        else:
            reply_text = str(reply)

        return {
            "routed": True,
            "action": "kai_chat",
            "operator": operator,
            "reply": reply_text,
            "chat_reply": reply,
        }

    if len(pending_builds) > 1:
        # Check if the message contains a number or name for build selection
        text = (message.get("text") or "").strip()
        build_list = _get_build_list_with_numbers(pending_builds)
        
        # Parse build selection and intent from the text
        selected_build = None
        remaining_text = text
        
        # Check if the message starts with a number that identifies a build
        if text.isdigit():
            # Handle case where user sends just a number (like "3")
            number = int(text)
            for build_info in build_list:
                if build_info["number"] == number:
                    selected_build = build_info["build"]
                    # Consume the number, leaving the rest for intent checking
                    remaining_text = ""
                    break
        elif text.startswith("1 ") or text.startswith("2 ") or text.startswith("3 ") or text.startswith("4 ") or text.startswith("5 ") or text.startswith("6 ") or text.startswith("7 ") or text.startswith("8 ") or text.startswith("9 "):
            # Handle case like "1 build-name" - extract number and potential name match
            parts = text.split()
            if len(parts) >= 2 and parts[0].isdigit():
                number = int(parts[0])
                # Look for a build that matches the number
                for build_info in build_list:
                    if build_info["number"] == number:
                        selected_build = build_info["build"]
                        # Consume the number and build name from text
                        # Remove the number part and the build name part
                        # First, try to consume the build name if it exists
                        build_name_parts = []
                        # Extract the build name parts
                        for i in range(1, len(parts)):
                            part = parts[i]
                            # Stop if we hit a pattern like approve, reject, etc.
                            if part in _APPROVAL_PATTERNS or part in _REJECT_PATTERNS:
                                break
                            build_name_parts.append(part)
                        
                        # If we found the build name, remove it
                        if build_name_parts:
                            # Reconstruct the remaining text after removing matched parts
                            consumed = parts[0]  # The number part
                            consumed += " " + " ".join(build_name_parts) # The build name part
                            remaining_text = text[len(consumed):].strip()
                        else:
                            # No build name matched, so just remove the number
                            remaining_text = " ".join(parts[1:])
                        break
        else:
            # Try to match by build name (more flexible pattern matching)
            # Check if the text contains a build name
            for build_info in build_list:
                # Check if message contains build name (exact or as substring)
                if build_info["name"].lower() in text.lower():
                    selected_build = build_info["build"]
                    # Consume the name, leaving the rest for intent checking
                    # Make sure we don't accidentally remove too much
                    remaining_text = text.replace(build_info["name"], "", 1).strip()
                    # Remove extra spaces
                    if remaining_text:
                        remaining_text = " ".join(remaining_text.split())
                    break
        
        # If we have a selected build, process it
        if selected_build:
            # Continue with the processing for a single build
            # But use the remaining text for intent resolution
            build = selected_build
            status = build.get("status")
            from_info = message.get("from", {})
            operator = _operator_name(from_info)
            # Use the remaining text for approval/intent checking
            intent_text = remaining_text
            intent = _resolve_approval_intent(intent_text)
            
            # Return appropriate action based on the intent
            if status == "WAITING_FOR_USER_INPUT":
                updated = _bm.submit_answer(build["id"], intent_text)
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

            # For approval/rejection paths, we only proceed if there's intent
            if intent == "approve":
                if status == "WAITING_FOR_ARCHITECTURE_APPROVAL":
                    updated = _bm.approve_architecture(build["id"], operator=operator)
                    return {
                        "routed": True,
                        "action": "approve_architecture",
                        "build_id": build["id"],
                        "operator": operator,
                        "reply": f"Architecture approved for build {build.get('name')}.",
                        "build": updated,
                    }
                elif status == "WAITING_FOR_DEPLOY_APPROVAL":
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
                if status == "WAITING_FOR_ARCHITECTURE_APPROVAL":
                    updated = _bm.reject_architecture(build["id"], operator=operator)
                    return {
                        "routed": True,
                        "action": "reject_architecture",
                        "build_id": build["id"],
                        "operator": operator,
                        "reply": f"Architecture rejected for build {build.get('name')}.",
                        "build": updated,
                    }
                elif status == "WAITING_FOR_DEPLOY_APPROVAL":
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
                # No intent - return the normal prompt for approval/rejection
                if status == "WAITING_FOR_ARCHITECTURE_APPROVAL":
                    return {
                        "routed": True,
                        "build_id": build["id"],
                        "reply": (
                            f"Build {build.get('name')} is waiting for architecture "
                            "approval. Reply 'approve' or 'reject'."
                        ),
                    }
                elif status == "WAITING_FOR_DEPLOY_APPROVAL":
                    return {
                        "routed": True,
                        "build_id": build["id"],
                        "reply": (
                            f"Build {build.get('name')} is waiting for deploy "
                            "approval. Reply 'approve' or 'reject'."
                        ),
                    }
                else:
                    # Fsother statuses
                    return {
                        "routed": True,
                        "build_id": build["id"],
                        "reply": (
                            f"Build {build.get('name')} is in state {status} -- no action taken."
                        ),
                    }
        else:
            # Show disambiguation message with numbered list
            build_display_lines = []
            for build_info in build_list:
                build_display_lines.append(
                    f"{build_info['number']}. {build_info['display_name']} "
                    f"(ID: {build_info['build_id'][:8]})"  # Shortened ID for readability
                )
            
            return {
                "routed": False,
                "reply": (
                    "Multiple builds are awaiting input. Please respond with either:\n"
                    "- The number of the build (e.g., '1', '2')\n"
                    "- The name of the build\n"
                    "- Both number and name (e.g., '1 build-name')\n\n"
                    "Available builds:\n" + "\n".join(build_display_lines)
                ),
            }

    # Process as a single build case - this is the existing logic for single builds
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
