"""Telegram message formatters for approval events — merged from telegra-approval-responder."""
import json
from core.telegram_bridge import send_message, answer_callback_query, edit_message_reply_markup

TELEGRAM_CHAT_ID = "612786480"

def _build_summary(item: dict) -> str:
    kind = item.get("type", "unknown")
    trace = item.get("trace_id", "")[:8]
    try:
        desc = json.loads(item.get("description", "{}"))
        name = desc.get("name", item.get("build_id", trace))
    except Exception:
        name = item.get("build_id", trace)
    return f"*{kind.upper()}*\n`{name}`\n_{trace}_"

def _format_status_change(change: dict) -> str:
    old = change["old"]
    new = change["new"]
    item = change["item"]
    summary = _build_summary(item)
    emoji = "✅" if new == "approved" else "❌" if new == "rejected" else "ℹ️"
    return f"{emoji} Status: `{old}` → `{new}`\n{summary}"

def notify_new_pending(item: dict):
    """Send approval request notification to Telegram."""
    summary = _build_summary(item)
    text = (
        f"🛎 *New Approval Required*\n{summary}\n\n"
        f"_Reply with *approve* or *reject* to act._"
    )
    send_message(TELEGRAM_CHAT_ID, text)

def notify_status_change(change: dict):
    """Send approved/rejected notification to Telegram."""
    text = _format_status_change(change)
    send_message(TELEGRAM_CHAT_ID, text)

def notify_status_change_reply(change: dict, message_id: int, callback_query_id: str = None):
    """Send approved/rejected notification as a reply to the original message."""
    text = _format_status_change(change)
    send_message(TELEGRAM_CHAT_ID, text, reply_to_message_id=message_id)
    if callback_query_id:
        answer_callback_query(callback_query_id, text="Notified ✓")
        edit_message_reply_markup(TELEGRAM_CHAT_ID, message_id)
