"""Dedicated, tight Telegram inbound loop -- decoupled from the 60s
orchestrator scheduler cycle.

Root-caused live 2026-08-01: inbound Telegram messages were only ever
processed from inside core.orchestrator_cycle.run_cycle(), which runs once
per 60s scheduler tick with a short-poll (timeout=0) getUpdates call. Worst
case, a reply took nearly a minute -- not remotely "fluid chat," and
unrelated to how fast Kai could actually generate a response.

This module owns Telegram's getUpdates offset exclusively at runtime: a
long-poll call (Telegram holds the connection open server-side until a
message arrives or the timeout elapses) in a tight loop, so a reply goes
out within roughly one round-trip of the message arriving, not up to 60s
later. Run as its own process (see ai-orchestrator-telegram.service) so a
hang or crash here can never stall builds, roadmap progress, or health
checks, and vice versa -- this is genuinely somebody else's problem to fix
if it goes down, not a reason the rest of Kai should stop.
"""

import random
import threading
import time

from core.logger import info
from core.telegram_bridge import (
    poll_updates,
    TelegramConflictError,
    delete_webhook,
    route_inbound_reply,
    route_callback_query,
    answer_callback_query,
    edit_message_reply_markup,
    send_message,
)
from core.approval_watcher import poll_once as poll_approval_queue
from core.approval_notifier import notify_new_pending, notify_status_change

# Telegram recommends staying comfortably under its own server-side cap;
# this is long enough to feel instant without holding the connection so
# long that a restart/deploy takes ages to converge.
POLL_TIMEOUT = 25

# Only used when poll_once() itself raises (network blip, bad token, 5xx) --
# a real message never waits this long, since a successful long-poll call
# returns (with or without messages) well under this window.
ERROR_BACKOFF_SECONDS = 5

# HTTP 409 means a second consumer is polling this bot token (Telegram allows
# only one getUpdates listener at a time). That will not clear on a 5s retry,
# so back off far longer instead of hammering ~11 requests/min against it.
CONFLICT_BACKOFF_SECONDS = 60

# Bounded retry around a single getUpdates call. Upstream 502s / ReadTimeouts
# on api.telegram.org are transient; dropping the poll (the old behaviour)
# lost the messages that arrived during the outage. Retry a few times with
# exponential backoff + jitter before letting run_forever apply its own
# longer backoff. The offset is only advanced inside poll_updates() on a
# successful fetch, so a failed attempt never skips updates.
MAX_POLL_ATTEMPTS = 5
POLL_RETRY_BASE_SECONDS = 1.0
POLL_RETRY_MAX_SECONDS = 30.0

# Repeated identical failures are logged at most this often (a flapping
# network must not spam the log every poll).
FAILURE_LOG_THROTTLE_SECONDS = 60.0

_consecutive_poll_failures = 0
_last_failure_log_at = 0.0


def _retry_delay(attempt: int) -> float:
    """Exponential backoff (1-based attempt) capped, with 50-100% jitter."""
    base = min(POLL_RETRY_BASE_SECONDS * (2 ** (attempt - 1)),
               POLL_RETRY_MAX_SECONDS)
    return base * (0.5 + random.random() * 0.5)


def _log_poll_failure(error, attempts: int) -> None:
    """Throttled, token-safe failure log (never raises)."""
    global _consecutive_poll_failures, _last_failure_log_at
    _consecutive_poll_failures += 1
    now = time.monotonic()
    if now - _last_failure_log_at >= FAILURE_LOG_THROTTLE_SECONDS:
        info(
            f"telegram_poller: getUpdates failed after {attempts} attempt(s) "
            f"({_consecutive_poll_failures} consecutive): "
            f"{type(error).__name__}: {error}"
        )
        _last_failure_log_at = now


def _poll_updates_with_retry():
    """getUpdates with bounded retry + exponential backoff and jitter.

    A TelegramConflictError (409) is not transient -- re-raise immediately so
    run_forever applies its long conflict backoff. Any other failure (502,
    ReadTimeout, connection reset) is retried up to MAX_POLL_ATTEMPTS; after
    exhaustion the last error is re-raised (run_forever catches it and keeps
    the loop alive). A successful call resets the consecutive-failure count.
    """
    global _consecutive_poll_failures

    last_error = None
    for attempt in range(1, MAX_POLL_ATTEMPTS + 1):
        try:
            messages = poll_updates(poll_timeout=POLL_TIMEOUT)
            _consecutive_poll_failures = 0
            return messages
        except TelegramConflictError:
            raise
        except Exception as error:  # noqa: BLE001 - transient network failure
            last_error = error
            if attempt < MAX_POLL_ATTEMPTS:
                time.sleep(_retry_delay(attempt))

    _log_poll_failure(last_error, MAX_POLL_ATTEMPTS)
    raise last_error


def _safe_send(text):
    # send_message() defaults to the single allowed operator chat; kept as
    # a thin wrapper (rather than calling send_message directly) so a
    # broken send can never take the whole poll loop down with it.
    try:
        send_message(text)
    except Exception as error:
        info(f"telegram_poller: reply send failed: {type(error).__name__}")


def poll_once():
    """One long-poll cycle: fetch new messages, route each, reply if there
    is one. Returns the number of messages processed (0 is normal/healthy
    -- it just means nothing arrived during this poll window)."""

    messages = _poll_updates_with_retry()

    for message in messages:
        try:
            # 2026-08-06: route callback queries (inline keyboard buttons) separately
            if message.get("callback_query"):
                result = route_callback_query(message)
                # Answer the callback query to dismiss the loading spinner
                callback_id = message.get("callback_id")
                if callback_id:
                    answer_callback_query(callback_id, text=result.get("reply"))
                # Remove the inline keyboard to prevent double-action
                chat_id = message.get("chat_id")
                msg_id = message.get("message_id")
                if chat_id and msg_id:
                    edit_message_reply_markup(chat_id, msg_id)
            else:
                result = route_inbound_reply(message)
        except Exception as error:
            info(f"telegram_poller: routing error: {type(error).__name__}")
            continue

        reply_text = result.get("reply")
        audio_bytes = result.get("audio_bytes")

        if audio_bytes:
            try:
                from core.telegram_bridge import send_voice
                send_voice(audio_bytes, chat_id=result.get("chat_id"))
            except Exception as error:
                info(f"telegram_poller: voice send failed: {type(error).__name__}")
                # Fall back to text if voice fails
                if reply_text:
                    _safe_send(reply_text)
        elif reply_text:
            _safe_send(reply_text)

    return len(messages)


def _approval_watcher_loop():
    """Background loop: check approval queue every 30 seconds."""
    while True:
        try:
            result = poll_approval_queue()
            for item in result["new_pending"]:
                notify_new_pending(item)
            for change in result["status_changes"]:
                if change["new"] in ("approved", "rejected"):
                    notify_status_change(change)
        except Exception:
            pass  # never crash the poller
        time.sleep(30)

def ensure_no_webhook():
    """Best-effort: clear any active Telegram webhook before polling.

    Telegram refuses getUpdates while a webhook is set, so a stray webhook
    (e.g. one set on the token by a third party) would otherwise flood the
    logs with 409 conflicts. Startup cleanup makes the poller self-healing;
    failures are non-fatal because poll_updates also recovers on the 409.
    """
    try:
        delete_webhook()
    except Exception as error:
        info(f"telegram_poller: webhook cleanup skipped: {type(error).__name__}")


def run_forever():
    info("telegram_poller started")
    ensure_no_webhook()

    # Start approval watcher as daemon thread
    _watcher_thread = threading.Thread(target=_approval_watcher_loop, daemon=True)
    _watcher_thread.start()

    while True:
        try:
            poll_once()
        except TelegramConflictError as error:
            info(
                "telegram_poller: duplicate getUpdates consumer for this bot "
                f"token (409) -- backing off {CONFLICT_BACKOFF_SECONDS}s: {error}"
            )
            time.sleep(CONFLICT_BACKOFF_SECONDS)
        except Exception as error:
            info(f"telegram_poller: poll failed: {type(error).__name__}: {error}")
            time.sleep(ERROR_BACKOFF_SECONDS)


if __name__ == "__main__":
    run_forever()
