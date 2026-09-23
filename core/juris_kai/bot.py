"""Juris Kai Bot — Multi-Tenant Legal Expert System with Native Telegram Menus.

Handles Telegram messages for the paid, multi-tenant Juris Kai bot
(@Juriskai_bot). Each user gets their own isolated account.

Features:
  - Native Telegram reply keyboards for all menus
  - Admin menu gated by JURIS_KAI_ADMIN_IDS
  - Welcome onboarding flow with disclaimer
  - Rate limiting per user
  - Command auditing
  - Session-based conversation tracking
  - Document analysis (session-only, never auto-ingested)

Security: NO imports of core.build_manager, core.approval, or
core.deployment_manager. Only text_task AI providers are used.
"""

import concurrent.futures
import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import requests

try:
    from core.telegram import registry as _tg_registry, guard as _tg_guard
    _TG_BOT = _tg_registry.resolve_bot(token_env="JURIS_KAI_BOT_TOKEN")
except Exception:  # noqa: BLE001 - Telegram Module optional (compat layer)
    _tg_registry = _tg_guard = _TG_BOT = None

from core.juris_kai.accounts import (
    get_account_manager,
    DISCLAIMER_TEXT,
    SUBSCRIPTION_TIERS,
)
from core.juris_kai.commands import handle_command
from core.juris_kai import menus as _menus
from core.juris_kai import cache as _cache
from core.juris_kai import streaming as _streaming
from core.juris_kai import grounding as _grounding
# Convenience aliases for frequently-used menu functions
main_menu = _menus.main_menu
admin_main_menu = _menus.admin_main_menu
disclaimer_accept_keyboard = _menus.disclaimer_accept_keyboard
menu_for_text = _menus.menu_for_text
learn_menu = _menus.learn_menu
case_law_menu = _menus.case_law_menu
practice_menu = _menus.practice_menu
study_tools_menu = _menus.study_tools_menu
documents_menu = _menus.documents_menu
progress_menu = _menus.progress_menu
settings_menu = _menus.settings_menu
admin_bot_health_menu = _menus.admin_bot_health_menu
admin_ai_menu = _menus.admin_ai_menu
admin_knowledge_menu = _menus.admin_knowledge_menu
admin_security_menu = _menus.admin_security_menu
admin_main_menu = _menus.admin_main_menu
confirm_cancel_keyboard = _menus.confirm_cancel_keyboard
quiz_answer_keyboard = _menus.quiz_answer_keyboard

logger = logging.getLogger("juris_kai.bot")

# Prompt-injection guard (inbound + outbound). Optional: if the module is
# unavailable the bot behaves exactly as before, never fails closed on import.
try:
    from core.legal.injection import (
        guard_input as _guard_input,
        guard_output as _guard_output,
        guard_stream_prefix as _guard_stream_prefix,
    )
except Exception:  # noqa: BLE001 - guard is optional, never block the bot
    _guard_input = _guard_output = _guard_stream_prefix = None


def _guard_inbound_text(text: str, source: str = "juris_kai") -> str:
    """Neutralize instruction-like spans in inbound user text before the LLM."""
    if _guard_input is None or not text:
        return text
    try:
        verdict = _guard_input(text, source=source)
    except Exception as exc:  # noqa: BLE001 - guard must never break a reply
        logger.warning("injection guard failed: %s", exc)
        return text
    if verdict.get("suspected"):
        logger.warning(
            "inbound injection neutralized source=%s markers=%s normalizations=%s",
            source, verdict.get("markers"), verdict.get("normalizations"))
        return verdict.get("clean_text", text)
    return text


def _juris_protected_fragments() -> list:
    """System-prompt text that must never appear verbatim in a reply."""
    try:
        from core.juris_kai.prompt import (
            _PREAMBLE, _JURISDICTION_GATE, _DATABASE_FIRST, _GROUNDED_SCOPE,
        )
        return [_PREAMBLE, _JURISDICTION_GATE, _DATABASE_FIRST, _GROUNDED_SCOPE]
    except Exception:  # noqa: BLE001 - best effort
        return []


def _guard_outbound_text(text: str, source: str = "juris_kai") -> str:
    """Redact leaked secrets/system-prompt text; safe fallback if tripped."""
    if _guard_output is None or not text:
        return text
    try:
        verdict = _guard_output(
            text, source=source, protected=_juris_protected_fragments())
    except Exception as exc:  # noqa: BLE001
        logger.warning("output guard failed: %s", exc)
        return text
    if verdict.get("tripped"):
        logger.warning("outbound leak blocked source=%s markers=%s",
                       source, verdict.get("markers"))
        return verdict.get("text", text)
    return text


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

BOT_TOKEN: str = ""  # filled lazily on first use


def _get_bot_token() -> str:
    """Load JURIS_KAI_BOT_TOKEN from env (vault temporarily disabled).

    HOTFIX 2026-09-09: credential_vault.retrieve_api_key() returns encrypted
    token. Bypass vault until decryption is fixed.
    """
    global BOT_TOKEN
    if BOT_TOKEN:
        return BOT_TOKEN
    # Direct .env load (working)
    BOT_TOKEN = os.environ.get("JURIS_KAI_BOT_TOKEN", "")
    return BOT_TOKEN
ADMIN_IDS: set[int] = set()
_raw_admin = os.environ.get("JURIS_KAI_ADMIN_IDS", "")
if _raw_admin:
    for _id in _raw_admin.split(","):
        try:
            ADMIN_IDS.add(int(_id.strip()))
        except ValueError:
            pass

# Rate limiting
RATE_LIMIT_WINDOW = 10  # seconds
RATE_LIMIT_MAX = 5  # messages per window per user
_rate_buckets: dict[str, list[float]] = {}

# Polling
POLL_TIMEOUT = 25
ERROR_BACKOFF = 5

# Conversation state for multi-step flows
_conversation_state: dict[str, dict] = {}

# ---------------------------------------------------------------------------
# Welcome & Help text
# ---------------------------------------------------------------------------

WELCOME_TEXT = (
    "⚖️ *Welcome to Juris Kai!*\n\n"
    "Your AI-powered Ghanaian Legal Research Assistant and Study Tutor.\n\n"
    "I can help you:\n"
    "📚 *Learn* Ghanaian law concepts\n"
    "⚖️ *Analyze* legal cases and precedents\n"
    "📝 *Practice* with IRAC and exam questions\n"
    "🧠 *Study* with flashcards and quizzes\n"
    "📄 *Analyze* legal documents\n\n"
    "Use the menu below to get started!"
)

HELP_TEXT = (
    "⚖️ *Juris Kai — Help*\n\n"
    "*Menu Items:*\n"
    "📚 *Learn Law* — Study Ghanaian legal concepts, topic explanations\n"
    "⚖️ *Cases* — Browse case law, principles, precedents, analysis\n"
    "📝 *Practice* — Generate exam questions, IRAC practice, mock exams\n"
    "🧠 *Study Tools* — Flashcards, memory drills, quick quizzes\n"
    "📄 *Documents* — Upload and analyze legal documents (session-based)\n"
    "🎓 *Progress* — Track your learning history and weak areas\n"
    "⚙️ *Settings* — Language, learning level, notifications, account\n\n"
    "*Commands:*\n"
    "/menu — Show the main menu\n"
    "/help — Show this help\n"
    "/start — Welcome message\n"
    "/account — Your account status\n"
    "/subscribe — View subscription plans\n"
    "/subscribe <tier> [email] — Buy a plan (Paystack checkout)\n"
    "/forget — Delete your stored questions & answers\n\n"
    "_Not a substitute for professional legal advice._"
)


# ---------------------------------------------------------------------------
# Telegram API helpers
# ---------------------------------------------------------------------------

def telegram_api(method: str, data: dict, timeout: int = 35) -> dict:
    """Call the Telegram Bot API. Returns the decoded JSON response.

    Default timeout is 35s to accommodate getUpdates long-polling (POLL_TIMEOUT=25s
    + network buffer).
    """
    url = f"https://api.telegram.org/bot{_get_bot_token()}/{method}"
    try:
        resp = requests.post(url, json=data, timeout=timeout)
        return resp.json()
    except Exception as exc:
        logger.error(f"Telegram API call failed: {method} — {exc}")
        return {"ok": False, "description": str(exc)}


def send_message(
    chat_id: int | str,
    text: str,
    reply_markup: str | None = None,
    parse_mode: str = "Markdown",
) -> dict:
    """Send a Telegram message with optional reply keyboard.

    Automatically chunks long messages (>4000 chars) to respect
    Telegram's 4096-char limit.
    """
    if len(text) <= 4000:
        data = {"chat_id": chat_id, "text": text}
        if reply_markup:
            data["reply_markup"] = reply_markup
        if parse_mode:
            data["parse_mode"] = parse_mode
        return telegram_api("sendMessage", data)

    # Chunk long messages
    results = []
    for i in range(0, len(text), 4000):
        chunk = text[i:i + 4000]
        data = {"chat_id": chat_id, "text": chunk}
        if i == 0 and reply_markup:
            data["reply_markup"] = reply_markup
        if parse_mode and i == 0:
            data["parse_mode"] = parse_mode
        results.append(telegram_api("sendMessage", data))
    return results[-1] if results else {"ok": False}


# Telegram bot tokens look like "<bot_id>:<secret>". Never log one; the token
# appears in the getUpdates URL, so a raw network-error string would leak it.
_TOKEN_RE = re.compile(r"\d{5,}:[A-Za-z0-9_-]{20,}")


def _redact(text) -> str:
    """Strip anything token-shaped from text before it reaches a log."""
    if not text:
        return ""
    return _TOKEN_RE.sub("<redacted-token>", str(text))


def delete_webhook() -> bool:
    """Remove any active webhook so getUpdates can run.

    Live 2026-09-21: an unrelated third-party webhook was set on this bot
    token, so Telegram rejected every getUpdates with HTTP 409 and flooded the
    logs. Clearing it at startup makes polling self-healing; the poll loop also
    retries once on the same 409. Never raises.
    """
    try:
        resp = telegram_api("deleteWebhook", {"drop_pending_updates": False})
        return bool(resp.get("ok"))
    except Exception:
        return False


def send_typing(chat_id: int | str) -> None:
    """Send typing indicator."""
    telegram_api("sendChatAction", {"chat_id": chat_id, "action": "typing"})


def answer_callback(callback_id: str, text: str = "", show_alert: bool = False) -> dict:
    """Answer a callback query."""
    data = {"callback_query_id": callback_id}
    if text:
        data["text"] = text
        data["show_alert"] = show_alert
    return telegram_api("answerCallbackQuery", data)


def edit_reply_markup(chat_id: int | str, message_id: int) -> dict:
    """Remove inline keyboard after a button is pressed."""
    return telegram_api("editMessageReplyMarkup", {
        "chat_id": chat_id,
        "message_id": message_id,
    })


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------

def check_rate_limit(telegram_id: str) -> bool:
    """Return False if user is rate-limited."""
    now = time.time()
    bucket = _rate_buckets.get(telegram_id, [])
    # Purge old entries
    bucket = [t for t in bucket if now - t < RATE_LIMIT_WINDOW]
    _rate_buckets[telegram_id] = bucket

    if len(bucket) >= RATE_LIMIT_MAX:
        return False

    bucket.append(now)
    _rate_buckets[telegram_id] = bucket
    return True


# ---------------------------------------------------------------------------
# Admin check
# ---------------------------------------------------------------------------

def is_admin(telegram_id: str) -> bool:
    """Check if a user ID is in the admin list."""
    try:
        return int(telegram_id) in ADMIN_IDS
    except (ValueError, TypeError):
        return False


# ---------------------------------------------------------------------------
# Message handling
# ---------------------------------------------------------------------------

# Hard wall-clock timeout for AI delegate calls (seconds).
# With deepseek_native_flash as primary + max_tokens=2048, responses
# typically arrive in 15-30s. 45s gives headroom without blocking the
# polling loop for too long.
DELEGATE_TIMEOUT = 45

# ---------------------------------------------------------------------------
# Request isolation — no shared mutable state across users
# ---------------------------------------------------------------------------
# The bot processes messages from multiple users in a single process.
# Every AI response is generated fresh per request — nothing is cached.
# The send_response() gateway validates chat_id before every message send.
#
# ARCHITECTURE RULE: No module-level dict/list/set that stores per-user data.
# All per-request state lives in the immutable RequestContext, created fresh
# for every incoming message and never shared between users.
# ---------------------------------------------------------------------------

import uuid
from dataclasses import dataclass


@dataclass(frozen=True)
class RequestContext:
    """Immutable per-message context — created fresh for every incoming message.

    No handler ever mutates shared module-level state. Each message gets its
    own context object, and every response is bound to the requesting chat_id.
    """
    chat_id: int
    telegram_id: str
    account: dict
    request_id: str
    is_admin: bool


def _send_text_with_fallback(chat_id, text, reply_markup=None,
                             parse_mode="Markdown"):
    """Send normally, then retry as plain text if a Markdown send fails.

    Legal answers (and their Sources footer) routinely contain ``_``/``*`` in
    titles; a Markdown parse error would otherwise drop the whole message.
    Mirrors the retry ``_finalize_stream`` does for the streaming path.
    """
    resp = send_message(chat_id, text, reply_markup=reply_markup,
                        parse_mode=parse_mode)
    if parse_mode and isinstance(resp, dict) and not resp.get("ok"):
        resp = send_message(chat_id, text, reply_markup=reply_markup,
                            parse_mode=None)
    return resp


def _send_guarded(expected_chat_id, result: dict | None):
    """Send a response ONLY if it targets the expected chat_id.

    This is the single bottleneck for ALL outbound messages in the bot.
    Every send_message() call MUST go through here or send_response().
    """
    if not result or not result.get("text"):
        return
    result_chat_id = result.get("chat_id")
    if result_chat_id is not None and str(result_chat_id) != str(expected_chat_id):
        logger.error(
            f"CROSS-USER LEAK BLOCKED — response chat_id={result_chat_id} "
            f"!= expected chat_id={expected_chat_id}. Response DROPPED."
        )
        return
    _send_text_with_fallback(
        result["chat_id"],
        result["text"],
        reply_markup=result.get("reply_markup"),
        parse_mode=result.get("parse_mode", "Markdown"),
    )


def send_response(ctx: RequestContext, result: dict | None):
    """Send via RequestContext — validates chat_id match before sending."""
    _send_guarded(ctx.chat_id, result)


def _estimate_tokens(text: str) -> int:
    """Rough token count for English text (~4 chars/token)."""
    return max(1, len(text or "") // 4)


def _delegate_with_timeout(prompt: str, task_type: str, fallback_label: str, account_id: str = "") -> tuple[str, str]:
    """Call ai_router.delegate() in a background thread with a hard timeout.

    Returns (response_text, model_name) on success, or (error_message, "") on failure.
    This prevents a stalled provider from blocking the bot's synchronous polling
    loop indefinitely.

    Detects empty (blank) AI responses and retries once with a simplified
    prompt before giving up, because some providers (e.g. deepseek_native_flash)
    occasionally return empty strings for valid queries.
    """
    def _call(p: str) -> tuple[str, str]:
        from core.ai.ai_router import delegate
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(
                delegate, p,
                task_type=task_type, capability="text_task",
            )
            res = future.result(timeout=DELEGATE_TIMEOUT)
            model = (res.get("provider") or "").strip() if isinstance(res, dict) else ""
            return ((res.get("response") or "").strip(), model)

    try:
        text, model = _call(prompt)
        # If the AI returned nothing, retry once with a simpler bare prompt
        # (no jurisdiction gate — the AI already saw the gate on the first attempt).
        if not text:
            logger.warning(f"Empty AI response for {task_type}, retrying with simple prompt")
            text, model = _call(f"Answer this Ghana law question concisely: {fallback_label}")
        if not text:
            logger.error(f"Empty AI response after retry for {task_type}")
            return (
                "⚠️ I couldn't generate a response for that query. "
                "The AI provider returned an empty reply — this can happen with "
                "certain legal article references. Please try rephrasing your question "
                "or ask about a specific legal topic."
            ), ""
        return text, model
    except concurrent.futures.TimeoutError:
        logger.warning(f"Delegate timeout for {task_type} ({DELEGATE_TIMEOUT}s)")
        return "⚠️ Query timed out. Please try a more specific question.", ""
    except Exception as e:
        logger.error(f"Delegate failed for {task_type}: {e}")
        return f"⚠️ Unable to load {fallback_label}. Please try again later.", ""


# ---------------------------------------------------------------------------
# Streaming generation (local-only) + TTL cache
# ---------------------------------------------------------------------------
# The local model supports token streaming, so instead of waiting ~19s for a
# full answer we send a placeholder and edit the Telegram message as tokens
# arrive (throttled to respect Telegram's edit rate limits). Any failure in
# this path falls back to the original blocking _delegate_with_timeout().
STREAM_EDIT_INTERVAL = float(os.environ.get("JURIS_KAI_STREAM_EDIT_INTERVAL", "1.0"))
STREAM_MIN_CHARS = int(os.environ.get("JURIS_KAI_STREAM_MIN_CHARS", "24"))
STREAM_MAX_MESSAGE = 3900
STREAM_PLACEHOLDER = "⚖️ _Searching Ghana law…_"

# Strict grounding (owner directive): no legal substance without a retrieved
# source. An UNGROUNDED question is refused honestly and NEVER reaches the
# model; GROUNDED/PARTIAL answers carry a deterministic Sources footer built
# from retrieval (plus a banner for PARTIAL). The texts live in
# ``grounding`` so every legal-answer surface shares them; aliased here for
# backwards compatibility.
UNGROUNDED_REPLY = _grounding.UNGROUNDED_REPLY
PARTIAL_BANNER = _grounding.PARTIAL_BANNER
JURISDICTION_REFUSAL = _grounding.JURISDICTION_REFUSAL
LEGAL_GROUNDING_TASK = "juris_research"


def _stream_enabled() -> bool:
    val = os.environ.get("JURIS_KAI_STREAM", "1").strip().lower()
    return val not in ("0", "false", "no", "off")


def _edit_message_text(chat_id, message_id, text, reply_markup=None,
                       parse_mode="Markdown") -> dict:
    data = {"chat_id": chat_id, "message_id": message_id, "text": text}
    if reply_markup:
        data["reply_markup"] = reply_markup
    if parse_mode:
        data["parse_mode"] = parse_mode
    return telegram_api("editMessageText", data)


def _send_placeholder(chat_id) -> int | None:
    resp = telegram_api("sendMessage", {
        "chat_id": chat_id, "text": STREAM_PLACEHOLDER, "parse_mode": "Markdown",
    })
    if resp.get("ok") and resp.get("result"):
        return resp["result"].get("message_id")
    return None


# Telegram rejects an edit that changes nothing with this phrase. It is a
# benign no-op (the text is already on screen), NOT a failure -- treating it
# as one made _finalize_stream resend the whole answer and double-post it.
_BENIGN_EDIT_MARKERS = ("message is not modified",)


def _edit_is_benign_noop(resp: dict) -> bool:
    """True when an edit failed only because the message already matched."""
    if not resp or resp.get("ok"):
        return False
    desc = str(resp.get("description", "")).lower()
    return any(marker in desc for marker in _BENIGN_EDIT_MARKERS)


def _finalize_stream(chat_id, message_id, text, reply_markup=None) -> None:
    """Final edit of a streamed message; keeps the reply keyboard.

    Legal answers routinely contain characters that break Telegram Markdown,
    so a failed Markdown edit is retried as plain text rather than lost.

    A "message is not modified" response is success, not failure: the
    placeholder already shows this exact text, so resending would duplicate
    the answer in the chat.
    """
    text = _guard_outbound_text(text, source="juris_kai")
    final = text if len(text) <= STREAM_MAX_MESSAGE else text[:STREAM_MAX_MESSAGE]
    resp = _edit_message_text(chat_id, message_id, final,
                              reply_markup=reply_markup, parse_mode="Markdown")
    if not resp.get("ok") and not _edit_is_benign_noop(resp):
        resp = _edit_message_text(chat_id, message_id, final,
                                  reply_markup=reply_markup, parse_mode=None)
    if not resp.get("ok") and not _edit_is_benign_noop(resp):
        send_message(chat_id, final, reply_markup=reply_markup, parse_mode=None)
    if len(text) > STREAM_MAX_MESSAGE:
        send_message(chat_id, text[STREAM_MAX_MESSAGE:], parse_mode=None)


def _stream_to_telegram(prompt, task_type, chat_id, reply_markup=None,
                        prefix="", suffix=""):
    """Stream a local generation into Telegram, editing as tokens arrive.

    ``prefix`` (e.g. a PARTIAL banner) and ``suffix`` (e.g. the deterministic
    Sources footer) are applied only to the FINAL edit, so they land in the
    finished message; the streamed pieces themselves stay verbatim and the
    returned text excludes both (the caller owns them and the cache stores the
    raw answer only).

    Returns (text, model, delivered):
      * delivered=True  — the full text is already in the chat; the caller
        must not send it again.
      * delivered=False — streaming was unusable; text is None and the caller
        should use the blocking path.
    """
    message_id = _send_placeholder(chat_id)
    if not message_id:
        return None, "", False

    acc = ""
    last_edit = 0.0
    last_len = 0
    model = _streaming.DEFAULT_MODEL
    stream = _streaming.stream_chat(prompt, task_type=task_type)
    try:
        for piece in stream:
            acc += piece
            # The incremental injection guard now lives inside the shared
            # streaming primitive (core.juris_kai.streaming.stream_chat), which
            # raises StreamGuardAbort before any suspicious span is yielded --
            # so this loop only ever sees verified text.
            now = time.time()
            if (len(acc) - last_len >= STREAM_MIN_CHARS
                    and now - last_edit >= STREAM_EDIT_INTERVAL):
                _edit_message_text(chat_id, message_id, acc[:STREAM_MAX_MESSAGE],
                                   parse_mode=None)
                last_edit = now
                last_len = len(acc)
        if not acc.strip():
            _edit_message_text(chat_id, message_id,
                               "⚠️ The model returned an empty reply.",
                               reply_markup=reply_markup, parse_mode=None)
            return None, "", False
        _finalize_stream(chat_id, message_id, prefix + acc + suffix, reply_markup)
        return acc, model, True
    except _streaming.StreamGuardAbort as exc:
        # The primitive's incremental guard stopped before the suspicious span
        # was rendered. Drop the partial message; the caller falls back to the
        # fully-guarded blocking path.
        logger.warning("stream aborted by injection guard (task=%s markers=%s)",
                       task_type, exc.markers)
        try:
            telegram_api("deleteMessage",
                         {"chat_id": chat_id, "message_id": message_id})
        except Exception:  # noqa: BLE001
            try:
                _edit_message_text(
                    chat_id, message_id,
                    "⚠️ I couldn't safely complete that answer.",
                    reply_markup=reply_markup, parse_mode=None)
            except Exception:
                pass
        return None, model, False
    except Exception as exc:  # noqa: BLE001 - any stream failure must fall back
        logger.warning(f"Telegram streaming failed for {task_type}: {exc}")
        if acc.strip():
            # Partial output is already visible; finish delivering it.
            try:
                _finalize_stream(chat_id, message_id, prefix + acc + suffix,
                                 reply_markup)
            except Exception:
                send_message(chat_id, prefix + acc + suffix,
                             reply_markup=reply_markup, parse_mode=None)
            return acc, model, True
        telegram_api("deleteMessage", {"chat_id": chat_id, "message_id": message_id})
        return None, "", False
    finally:
        close = getattr(stream, "close", None)
        if close is not None:
            try:
                close()
            except Exception:  # noqa: BLE001
                pass


def _generate_reply(prompt, task_type, query, fallback_label, account_id="",
                    chat_id=None, reply_markup=None, context="", prefix="",
                    suffix="", source_key=""):
    """Generate a legal answer: FAQ cache → TTL cache → stream → blocking.

    Returns (text, model, streamed, cached). ``streamed=True`` means the text
    was already delivered to Telegram by this function and must not be sent
    again by the caller. ``cached=True`` means it came from the FAQ or TTL
    cache without a model call.

    The FAQ fast path is only used for standalone questions (no follow-up
    context), so a context-dependent answer is never wrongly replayed.

    ``prefix``/``suffix`` (e.g. a PARTIAL banner and the Sources footer) are
    applied to the *delivered* message on the streaming path (final edit) but
    are deliberately excluded from the returned/cached text, so a cache replay
    cannot duplicate them — the caller owns both and applies them to
    non-streamed answers.

    ``source_key`` binds cache reuse to the retrieved source set: a cached
    answer only counts as a hit when retrieval returns the same sources, so a
    stale answer can never be replayed under a fresh Sources footer.
    """
    # 1) FAQ / repeat cache — a normalized repeat served instantly from the
    #    local juris_qa_log (survives restarts) + in-process FAQ layer.
    if not context and account_id:
        try:
            from core.juris_kai.accounts import get_account_manager
            faq = get_account_manager().lookup_qa(
                account_id, task_type, query, source_key=source_key)
        except Exception as exc:  # noqa: BLE001 - never break generation
            logger.warning("juris FAQ lookup failed: %s", exc)
            faq = None
        if faq and faq.get("answer"):
            logger.info("juris FAQ cache hit task=%s account=%s", task_type, account_id)
            return (_guard_outbound_text(faq["answer"], source="juris_kai"),
                    faq.get("model", ""), False, True)

    # 2) In-process generation TTL cache. Context and source set are folded
    #    into the key so a follow-up (or a different source set) is not replayed.
    corpus_ver = _cache.corpus_version()
    ctx_key = _cache.context_fingerprint(context)
    key = _cache.generation_key(task_type, query, corpus_ver, ctx_key, source_key)
    hit = _cache.GENERATION_CACHE.get(key)
    if hit and hit.get("text"):
        return (_guard_outbound_text(hit["text"], source="juris_kai"),
                hit.get("model", ""), False, True)

    if chat_id and _stream_enabled():
        text, model, delivered = _stream_to_telegram(
            prompt, task_type, chat_id, reply_markup, prefix=prefix, suffix=suffix)
        if delivered and text:
            _cache.GENERATION_CACHE.set(
                key, {"text": text, "model": model, "corpus_version": corpus_ver})
            return text, model, True, False

    text, model = _delegate_with_timeout(prompt, task_type, fallback_label, account_id)
    text = _guard_outbound_text(text, source="juris_kai")
    if text:
        _cache.GENERATION_CACHE.set(
            key, {"text": text, "model": model, "corpus_version": corpus_ver})
    return text, model, False, False


def _followup_context(chat_id, question: str) -> str:
    """Bounded prior-turn context for an apparent follow-up, else ""."""
    try:
        from core.juris_kai.session import get_followup_context
        return get_followup_context(chat_id, question)
    except Exception as exc:  # noqa: BLE001 - context is best-effort
        logger.warning("juris follow-up context failed: %s", exc)
        return ""


def _record_turn(account_id: str, chat_id, task_type: str, question: str,
                 answer: str, model: str, latency_ms: int,
                 cache_hit: bool, source_key: str = "") -> None:
    """Persist the Q&A for the learning loop and update the session history.

    ``source_key`` binds the stored answer to its retrieved source set so the
    FAQ layer only replays it for the same sources.
    """
    try:
        get_account_manager().record_qa(
            account_id, question=question, answer=answer, chat_id=str(chat_id),
            task_type=task_type, model=model, latency_ms=int(latency_ms),
            cache_hit=bool(cache_hit), source_key=source_key)
    except Exception as exc:  # noqa: BLE001 - logging must never break a reply
        logger.error("juris record_qa failed: %s", exc)
    try:
        from core.juris_kai.session import record_conversation_turn
        record_conversation_turn(chat_id, question, answer or "")
    except Exception as exc:  # noqa: BLE001
        logger.warning("juris session record failed: %s", exc)


def handle_message(update: dict) -> dict | None:
    """Process a single incoming Telegram message.

    Accepts two formats:
      1. Full Telegram API format: {"message": {"chat": {"id"}, "from": {...}, "text": "..."}}
      2. Legacy flat format:       {"chat_id": "123", "text": "...", "from_first_name": "..."}

    Returns a dict with keys:
      - chat_id: the Telegram chat ID
      - text: the reply text (may be None if no text reply needed)
      - reply_markup: JSON-serialized keyboard (may be None)
      - parse_mode: "Markdown" or None
    """
    # Support both Telegram API format and legacy flat format
    msg = update.get("message")
    if msg:
        chat_id = msg["chat"]["id"]
        from_user = msg.get("from", {})
        telegram_id = str(from_user.get("id", ""))
        message_text = (msg.get("text") or "").strip()
        first_name = from_user.get("first_name", "")
    else:
        # Legacy flat format (used by tests and direct callers)
        chat_id = update.get("chat_id", "")
        telegram_id = str(chat_id)
        message_text = (update.get("text") or "").strip()
        first_name = update.get("from_first_name", "")
        from_user = {"id": telegram_id, "first_name": first_name}

    if not telegram_id or not message_text:
        return None

    # Prompt-injection guard: scan EVERY inbound message before it can reach
    # the LLM. A message carrying instruction-like spans is never a menu item,
    # so neutralizing the spans in place still routes it to free text.
    message_text = _guard_inbound_text(message_text, source="juris_kai")

    # KAI Telegram Module authorization (directive §20-§22): this bot may only
    # act within its registered capabilities — deny-by-default. Juris is
    # registered for legal.* only, so it can never reach admin capabilities.
    if _tg_guard is not None and _TG_BOT is not None:
        verdict = _tg_guard.require(_TG_BOT, "legal.query",
                                    chat_id=chat_id, user_id=telegram_id)
        if not verdict["allowed"]:
            logger.warning("telegram module denied legal.query: %s",
                           verdict["reason"])
            return {
                "chat_id": chat_id,
                "text": "This request is not authorized by the Telegram Module.",
            }

    # Rate limit
    if not check_rate_limit(telegram_id):
        get_account_manager().log_rate_limit_hit(telegram_id)
        return {
            "chat_id": chat_id,
            "text": "⚠️ You're sending messages too quickly. Please wait a moment.",
            "reply_markup": main_menu() if not is_admin(telegram_id) else admin_main_menu(),
        }

    # Get or create account
    mgr = get_account_manager()
    account = mgr.get_or_create(
        telegram_id,
        from_user.get("first_name", ""),
    )

    # Create immutable per-request context — every response is bound to this chat_id
    admin = is_admin(telegram_id)
    ctx = RequestContext(
        chat_id=chat_id,
        telegram_id=telegram_id,
        account=account,
        request_id=str(uuid.uuid4()),
        is_admin=admin,
    )

    # New user onboarding
    if account.get("is_new"):
        return {
            "chat_id": chat_id,
            "text": DISCLAIMER_TEXT + "\n\n" + WELCOME_TEXT,
            "reply_markup": disclaimer_accept_keyboard(),
            "parse_mode": "Markdown",
        }

    # Disclaimer not accepted
    if not account.get("disclaimer_accepted") and not message_text.startswith("/start"):
        return {
            "chat_id": chat_id,
            "text": (
                "Before using Juris Kai, please acknowledge the disclaimer:\n\n"
                + DISCLAIMER_TEXT
                + "\n\nTap the button below to continue."
            ),
            "reply_markup": disclaimer_accept_keyboard(),
            "parse_mode": "Markdown",
        }

    # Deactivated account
    if not account.get("is_active"):
        return {
            "chat_id": chat_id,
            "text": "Your account has been deactivated. Contact support for assistance.",
            "reply_markup": None,
        }

    # Route based on message content

    # /start
    if message_text.startswith("/start"):
        if not account.get("disclaimer_accepted"):
            mgr.accept_disclaimer(account["account_id"])
        return {
            "chat_id": chat_id,
            "text": WELCOME_TEXT,
            "reply_markup": main_menu(),
            "parse_mode": "Markdown",
        }

    # /menu
    if message_text.startswith("/menu"):
        return {
            "chat_id": chat_id,
            "text": "What would you like to do?" if not admin else "Admin Menu:",
            "reply_markup": main_menu() if not admin else admin_main_menu(),
        }

    # /admin — toggle admin menu
    if message_text.startswith("/admin"):
        if not admin:
            mgr.log_admin_denied(telegram_id, "/admin command")
            return {"chat_id": chat_id, "text": "Unauthorized.", "reply_markup": main_menu()}
        return {
            "chat_id": chat_id,
            "text": "🔧 Admin Menu",
            "reply_markup": admin_main_menu(),
        }

    # Admin menu item (gate check)
    admin_menu_items = {
        "🔧 Bot Health", "👥 User Activity", "📚 Knowledge Mgmt", "🤖 AI Mgmt",
        "🔐 Security", "📊 Stats Dashboard", "🔙 Admin Menu",
        "📊 System Status", "⚠️ Error Logs", "🔌 API Status", "🤖 Provider Status",
        "💰 Cost Monitor", "✅ Approved Sources", "➕ Add Document", "🔍 Verify Source",
        "📚 Update Database", "📋 Source Versions", "🔄 Model Routing", "📈 Token Usage",
        "⚡ Performance", "⚠️ Failover Control", "👥 Permissions", "📊 Sessions",
        "🚨 Suspicious Activity", "📋 Access Logs",
    }
    if message_text in admin_menu_items and not admin:
        get_account_manager().log_admin_denied(telegram_id, message_text)
        return {"chat_id": chat_id, "text": "Main Menu", "reply_markup": main_menu()}

    # Admin menu item: "🔙 User Menu"
    if message_text == "🔙 User Menu":
        return {"chat_id": chat_id, "text": "Main Menu", "reply_markup": main_menu()}

    # Check for menu navigation
    keyboard = menu_for_text(message_text, is_admin=admin)
    if keyboard is not None:
        return {
            "chat_id": chat_id,
            "text": message_text,
            "reply_markup": keyboard,
        }

    # Handle menu items that trigger actions (not navigation)
    result = _handle_menu_action(message_text, chat_id, account, admin, update)
    if result is not None:
        return result

    # Commands with / prefix
    if message_text.startswith("/"):
        return _handle_legacy_command(message_text, chat_id, account, admin)

    # Default: treat as free-text legal query
    return _handle_free_text(message_text, chat_id, account, admin)


# ---------------------------------------------------------------------------
# Menu action handlers
# ---------------------------------------------------------------------------

def _handle_menu_action(
    text: str, chat_id: int, account: dict, admin: bool, update: dict,
) -> dict | None:
    """Handle menu button presses that trigger actions rather than navigation."""
    mgr = get_account_manager()

    # ---- Learn Law sub-items ----
    legal_topics = {
        "🇬🇭 Ghana Constitution": "ghana_constitution",
        "⚖️ Criminal Law": "criminal_law",
        "🏛️ Civil Law": "civil_law",
        "📋 Contract Law": "contract_law",
        "🏠 Property Law": "property_law",
        "👨‍👩‍👧 Family Law": "family_law",
        "💼 Business Law": "business_law",
    }
    if text in legal_topics:
        return _handle_learn_topic(legal_topics[text], text, chat_id, account)

    if text == "🔍 Search Topic":
        _conversation_state[str(chat_id)] = {"step": "search_topic", "data": {}}
        return {
            "chat_id": chat_id,
            "text": "What legal topic would you like to search for?\n\nType your topic below:",
            "reply_markup": '{"remove_keyboard": true}',
        }

    # ---- Case Law sub-items ----
    if text in ("📋 Case Summaries", "⚡ Legal Principles", "📜 Precedents",
                 "🔎 Case Analysis", "📂 Source References"):
        return _handle_case_query(text.replace("📋 ", "").replace("⚡ ", "").replace("📜 ", "").replace("🔎 ", "").replace("📂 ", ""), chat_id, account)

    if text == "🔍 Search Case":
        _conversation_state[str(chat_id)] = {"step": "search_case", "data": {}}
        return {
            "chat_id": chat_id,
            "text": "What case would you like to find?\n\nType the case name below:",
            "reply_markup": '{"remove_keyboard": true}',
        }

    # ---- Practice sub-items ----
    if text == "📝 Generate Questions":
        return _handle_generate_questions(chat_id, account)
    if text == "⚖️ IRAC Practice":
        return _handle_irac_practice(chat_id, account)
    if text == "✍️ Essay Practice":
        return _handle_essay_practice(chat_id, account)
    if text == "📋 Mock Exams":
        return _handle_mock_exam(chat_id, account)
    if text == "✅ Answer Evaluation":
        _conversation_state[str(chat_id)] = {"step": "answer_eval", "data": {}}
        return {
            "chat_id": chat_id,
            "text": (
                "Send me your answer to evaluate.\n\n"
                "Include the question and your answer, and I'll provide feedback "
                "using IRAC methodology.\n\nType /menu to go back."
            ),
            "reply_markup": '{"remove_keyboard": true}',
        }

    # ---- Study Tools sub-items ----
    if text == "🃏 Flashcards":
        return _handle_flashcards_action(chat_id, account)
    if text == "🧠 Memory Drills":
        return _handle_memory_drills(chat_id, account)
    if text == "⏱️ Quick Quiz":
        return _handle_quick_quiz(chat_id, account)
    if text == "📝 Revision Notes":
        return _handle_revision_notes(chat_id, account)

    # ---- Documents ----
    if text == "📤 Upload Document":
        return {
            "chat_id": chat_id,
            "text": (
                "📄 *Upload a Document*\n\n"
                "Send me a legal document (PDF, text, or DOCX) and I'll analyze it.\n\n"
                "⚠️ *Important*: Uploaded documents are *session-based only*.\n"
                "They will NOT be added to the permanent knowledge base.\n\n"
                "Your document stays private to your account."
            ),
            "reply_markup": documents_menu(),
            "parse_mode": "Markdown",
        }
    if text == "📋 Summarize":
        _conversation_state[str(chat_id)] = {"step": "summarize", "data": {}}
        return {
            "chat_id": chat_id,
            "text": "Send me the text or document you'd like summarized:",
            "reply_markup": '{"remove_keyboard": true}',
        }
    if text in ("⚖️ Legal Concepts from Doc", "📌 Key Points", "📂 Recent Documents"):
        return {
            "chat_id": chat_id,
            "text": (
                f"📄 *{text}*\n\n"
                "To use this feature, first upload a document using "
                "*📤 Upload Document*, then come back here."
            ),
            "reply_markup": documents_menu(),
            "parse_mode": "Markdown",
        }

    # ---- Progress ----
    if text in ("📊 Learning History", "✅ Completed Topics", "🎯 Weak Areas",
                 "🗺️ Study Path", "📈 Stats"):
        return _handle_progress_action(text, chat_id, account)

    # ---- Settings ----
    if text == "👤 Account Info":
        return _handle_account_info(chat_id, account)
    if text == "💳 Subscription":
        return _handle_subscription_info(chat_id, account)
    if text in ("🌐 Language", "📊 Learning Level", "🔔 Notifications"):
        return {
            "chat_id": chat_id,
            "text": f"⚙️ *{text}* — This feature will be available in the next update.",
            "reply_markup": settings_menu(account),
            "parse_mode": "Markdown",
        }

    # ---- Help ----
    if text == "❓ Help":
        return {"chat_id": chat_id, "text": HELP_TEXT, "reply_markup": main_menu(), "parse_mode": "Markdown"}

    # ---- Admin sub-items ----
    if admin and text in ("📊 System Status", "📊 Stats Dashboard"):
        return _handle_admin_stats(chat_id)
    if admin and text == "⚠️ Error Logs":
        return _handle_admin_error_logs(chat_id)
    if admin and text == "👥 User Activity":
        return _handle_admin_user_activity(chat_id)
    if admin and text in ("🔌 API Status", "🤖 Provider Status", "🔄 Model Routing",
                           "📈 Token Usage", "⚡ Performance", "⚠️ Failover Control"):
        return _handle_admin_ai_status(text, chat_id)
    if admin and text in ("✅ Approved Sources", "➕ Add Document", "🔍 Verify Source",
                           "📚 Update Database", "📋 Source Versions"):
        return _handle_admin_knowledge(text, chat_id)
    if admin and text == "💰 Cost Monitor":
        return _handle_admin_cost(chat_id)
    if admin and text in ("👥 Permissions", "📊 Sessions", "🚨 Suspicious Activity",
                           "📋 Access Logs"):
        return _handle_admin_security(text, chat_id)

    return None


# ---------------------------------------------------------------------------
# Learn / Case action implementations
# ---------------------------------------------------------------------------

def _handle_learn_topic(topic_key: str, label: str, chat_id: int, account: dict) -> dict:
    """Generate a grounded legal teaching response for a topic button."""
    topic_display = label.split(" ", 1)[1] if " " in label else label
    question = f"Ghana {topic_display}"

    # Strict grounding: teaching answers from retrieved Ghana sources only.
    return _build_legal_reply(
        question, chat_id, account, reply_markup=learn_menu(),
        task_type="juris_legal_teaching")


def _handle_case_query(query_type: str, chat_id: int, account: dict) -> dict:
    """Handle grounded case law queries."""
    question = f"{query_type} in Ghana law"
    retrieval_query = f"{query_type} Ghana law"

    # Strict grounding: case analysis answers from retrieved Ghana sources only.
    return _build_legal_reply(
        question, chat_id, account, reply_markup=case_law_menu(),
        task_type="juris_case_analysis", query=retrieval_query)


# ---------------------------------------------------------------------------
# Practice action implementations
# ---------------------------------------------------------------------------

def _handle_generate_questions(chat_id: int, account: dict) -> dict:
    """Generate exam questions on a legal topic."""
    _conversation_state[str(chat_id)] = {"step": "gen_questions", "data": {}}
    return {
        "chat_id": chat_id,
        "text": (
            "📝 *Generate Exam Questions*\n\n"
            "What topic should the questions cover?\n"
            "Examples:\n"
            "• Constitutional Law — Fundamental Human Rights\n"
            "• Criminal Law — Defenses\n"
            "• Contract Law — Offer and Acceptance\n\n"
            "Type a topic below, or /menu to go back."
        ),
        "reply_markup": '{"remove_keyboard": true}',
        "parse_mode": "Markdown",
    }


def _handle_irac_practice(chat_id: int, account: dict) -> dict:
    _conversation_state[str(chat_id)] = {"step": "irac", "data": {}}
    return {
        "chat_id": chat_id,
        "text": (
            "⚖️ *IRAC Practice*\n\n"
            "I'll give you a legal scenario, and you'll apply IRAC:\n"
            "• **I**ssue — Identify the legal issue\n"
            "• **R**ule — State the relevant legal rule\n"
            "• **A**pplication — Apply the rule to the facts\n"
            "• **C**onclusion — Reach a conclusion\n\n"
            "What topic area? (e.g., constitutional, criminal, contract)\n"
            "Type a topic or /menu to go back."
        ),
        "reply_markup": '{"remove_keyboard": true}',
        "parse_mode": "Markdown",
    }


def _handle_essay_practice(chat_id: int, account: dict) -> dict:
    _conversation_state[str(chat_id)] = {"step": "essay", "data": {}}
    return {
        "chat_id": chat_id,
        "text": (
            "✍️ *Essay Practice*\n\n"
            "I'll give you an essay question and then evaluate your answer.\n\n"
            "What topic area? (e.g., property law, human rights, business law)\n"
            "Type a topic or /menu to go back."
        ),
        "reply_markup": '{"remove_keyboard": true}',
        "parse_mode": "Markdown",
    }


def _handle_mock_exam(chat_id: int, account: dict) -> dict:
    _conversation_state[str(chat_id)] = {"step": "mock_exam", "data": {}}
    return {
        "chat_id": chat_id,
        "text": (
            "📋 *Mock Exam*\n\n"
            "I'll generate a timed mock exam with multiple question types.\n\n"
            "What subject? (e.g., Ghana Constitutional Law, Criminal Procedure)\n"
            "Type a subject or /menu to go back."
        ),
        "reply_markup": '{"remove_keyboard": true}',
        "parse_mode": "Markdown",
    }


# ---------------------------------------------------------------------------
# Study Tools action implementations
# ---------------------------------------------------------------------------

def _handle_flashcards_action(chat_id: int, account: dict) -> dict:
    _conversation_state[str(chat_id)] = {"step": "flashcards", "data": {}}
    return {
        "chat_id": chat_id,
        "text": (
            "🃏 *Flashcards*\n\n"
            "I'll generate study flashcards on any legal topic.\n\n"
            "What topic?\n"
            "Type a topic or /menu to go back."
        ),
        "reply_markup": '{"remove_keyboard": true}',
        "parse_mode": "Markdown",
    }


def _handle_memory_drills(chat_id: int, account: dict) -> dict:
    _conversation_state[str(chat_id)] = {"step": "memory", "data": {}}
    return {
        "chat_id": chat_id,
        "text": (
            "🧠 *Memory Drills*\n\n"
            "I'll quiz you on key legal principles and track your retention.\n\n"
            "What topic area?\n"
            "Type a topic or /menu to go back."
        ),
        "reply_markup": '{"remove_keyboard": true}',
        "parse_mode": "Markdown",
    }


def _handle_quick_quiz(chat_id: int, account: dict) -> dict:
    _conversation_state[str(chat_id)] = {"step": "quiz", "data": {}}
    return {
        "chat_id": chat_id,
        "text": (
            "⏱️ *Quick Quiz*\n\n"
            "5 multiple-choice questions on Ghana law. Ready?\n\n"
            "What topic?\n"
            "Type a topic or /menu to go back."
        ),
        "reply_markup": '{"remove_keyboard": true}',
        "parse_mode": "Markdown",
    }


def _handle_revision_notes(chat_id: int, account: dict) -> dict:
    _conversation_state[str(chat_id)] = {"step": "revision", "data": {}}
    return {
        "chat_id": chat_id,
        "text": (
            "📝 *Revision Notes*\n\n"
            "I'll create concise revision notes on any legal topic.\n\n"
            "What topic?\n"
            "Type a topic or /menu to go back."
        ),
        "reply_markup": '{"remove_keyboard": true}',
        "parse_mode": "Markdown",
    }


# ---------------------------------------------------------------------------
# Progress & Settings action implementations
# ---------------------------------------------------------------------------

def _handle_progress_action(label: str, chat_id: int, account: dict) -> dict:
    mgr = get_account_manager()
    sub = mgr.get_active_subscription(account["account_id"])
    limit_check = mgr.check_query_limit(account["account_id"])

    from core.juris_kai.menus import progress_menu
    return {
        "chat_id": chat_id,
        "text": (
            f"📊 *{label}*\n\n"
            f"📚 Queries today: {sub['limits']['max_queries_per_day'] - limit_check['remaining']}"
            f"/{sub['limits']['max_queries_per_day']}\n"
            f"📄 Documents this month: Check /account for details.\n\n"
            "_More detailed analytics coming soon._"
        ),
        "reply_markup": progress_menu(),
        "parse_mode": "Markdown",
    }


def _handle_account_info(chat_id: int, account: dict) -> dict:
    mgr = get_account_manager()
    sub = mgr.get_active_subscription(account["account_id"])
    tier_info = SUBSCRIPTION_TIERS.get(sub["tier"], SUBSCRIPTION_TIERS["free_trial"])

    from core.juris_kai.menus import settings_menu
    return {
        "chat_id": chat_id,
        "text": (
            f"👤 *Account Info*\n\n"
            f"Account ID: `{account['account_id']}`\n"
            f"Name: {account.get('full_name', 'Not set')}\n"
            f"Plan: {tier_info['name']}\n"
            f"Status: {'✅ Active' if sub['is_active'] else '❌ Expired'}\n"
        ),
        "reply_markup": settings_menu(account),
        "parse_mode": "Markdown",
    }


def _handle_subscription_info(chat_id: int, account: dict) -> dict:
    mgr = get_account_manager()
    sub = mgr.get_active_subscription(account["account_id"])
    current_tier = sub["tier"]

    lines = ["💳 *Subscription Plans*\n"]
    for tier_key, tier in SUBSCRIPTION_TIERS.items():
        marker = " ✅ (current)" if tier_key == current_tier else ""
        lines.append(
            f"*{tier['name']}*{marker}\n"
            f"  💰 GH₵{tier['price_ghs']}\n"
            f"  📄 {tier['max_documents_per_month']} docs/month\n"
            f"  🔍 {tier['max_queries_per_day']} queries/day\n"
        )

    from core.juris_kai.menus import settings_menu
    return {
        "chat_id": chat_id,
        "text": "\n".join(lines),
        "reply_markup": settings_menu(account),
        "parse_mode": "Markdown",
    }


# ---------------------------------------------------------------------------
# Admin action handlers
# ---------------------------------------------------------------------------

def _handle_admin_stats(chat_id: int) -> dict:
    try:
        from core.juris_kai.dashboard import get_dashboard_stats
        stats = get_dashboard_stats()
        juris = stats.get("juris_kai", {})
        text = (
            "📊 *Juris Kai Stats*\n\n"
            f"👥 Total accounts: {juris.get('total_accounts', 0)}\n"
            f"✅ Active: {juris.get('active_accounts', 0)}\n"
            f"💰 Revenue: GH₵{juris.get('total_revenue_ghs', 0):.2f}\n"
            f"🔍 Total queries: {juris.get('total_queries', 0)}\n"
        )
    except Exception as e:
        text = f"Error loading stats: {e}"

    return {"chat_id": chat_id, "text": text, "reply_markup": admin_main_menu(), "parse_mode": "Markdown"}


def _handle_admin_error_logs(chat_id: int) -> dict:
    try:
        log_path = Path(__file__).parent.parent.parent / "logs" / "juris_kai.log"
        if log_path.exists():
            tail = log_path.read_text().split("\n")[-20:]
            text = "⚠️ *Recent Logs (last 20 lines)*:\n\n" + "\n".join(tail)
        else:
            text = "No log file found."
    except Exception:
        text = "Unable to read logs."

    return {"chat_id": chat_id, "text": text[:4000], "reply_markup": admin_main_menu()}


def _handle_admin_user_activity(chat_id: int) -> dict:
    try:
        from core.juris_kai.dashboard import list_accounts
        result = list_accounts(page=1, per_page=10)
        lines = ["👥 *Recent Users*:\n"]
        for a in result["accounts"]:
            lines.append(
                f"• `{a['account_id'][:8]}` — {a.get('full_name', 'Unknown')} "
                f"[{a.get('subscription_tier', 'unknown')}]"
            )
        text = "\n".join(lines)
    except Exception as e:
        text = f"Error: {e}"

    return {"chat_id": chat_id, "text": text, "reply_markup": admin_main_menu(), "parse_mode": "Markdown"}


def _handle_admin_ai_status(label: str, chat_id: int) -> dict:
    try:
        from core.ai_provider import list_providers
        providers = list_providers()
        lines = [f"🤖 *{label}*\n"]
        for name, info in sorted(providers.items()):
            status = "✅" if info.get("available") else "❌"
            lines.append(f"{status} {name}: {info.get('capabilities', [])}")
        text = "\n".join(lines)
    except Exception as e:
        text = f"Error: {e}"

    return {"chat_id": chat_id, "text": text, "reply_markup": admin_ai_menu(), "parse_mode": "Markdown"}


def _handle_admin_knowledge(label: str, chat_id: int) -> dict:
    from core.juris_kai.menus import admin_knowledge_menu
    return {
        "chat_id": chat_id,
        "text": (
            f"📚 *{label}*\n\n"
            "Knowledge base management is available through the Kai Dashboard "
            "web interface. Open the Juris Kai admin tab for full document "
            "management capabilities."
        ),
        "reply_markup": admin_knowledge_menu(),
        "parse_mode": "Markdown",
    }


def _handle_admin_cost(chat_id: int) -> dict:
    try:
        from core.juris_kai.dashboard import get_dashboard_stats
        stats = get_dashboard_stats()
        juris = stats.get("juris_kai", {})
        text = (
            "💰 *Cost Monitor*\n\n"
            f"Total revenue: GH₵{juris.get('total_revenue_ghs', 0):.2f}\n"
            f"Active accounts: {juris.get('active_accounts', 0)}\n"
            f"Total queries: {juris.get('total_queries', 0)}\n"
        )
    except Exception as e:
        text = f"Error: {e}"

    return {"chat_id": chat_id, "text": text, "reply_markup": admin_main_menu(), "parse_mode": "Markdown"}


def _handle_admin_security(label: str, chat_id: int) -> dict:
    from core.juris_kai.menus import admin_security_menu
    return {
        "chat_id": chat_id,
        "text": (
            f"🔐 *{label}*\n\n"
            "Security management is available through the Kai Dashboard "
            "web interface. Open the Juris Kai admin tab for full security "
            "monitoring and permission management."
        ),
        "reply_markup": admin_security_menu(),
        "parse_mode": "Markdown",
    }


# ---------------------------------------------------------------------------
# Free-text handling
# ---------------------------------------------------------------------------

def _build_legal_reply(text: str, chat_id, account: dict,
                       reply_markup=None, task_type: str = LEGAL_GROUNDING_TASK,
                       query: str = None) -> dict:
    """Answer a legal question under strict grounding.

    This is the single grounded-answer path shared by free text and the
    Telegram menu handlers (Learn Law, Cases, practice/study flows). It calls
    ``grounding.build_grounded_plan`` so the SAME rules apply everywhere:

      * OUT-OF-SCOPE — a clearly non-Ghana question is refused before
        retrieval or any model call.
      * UNGROUNDED — no retrieved source grounds the question, so we return an
        honest refusal and NEVER call the model. The turn is still recorded so
        the learning loop sees the miss.
      * GROUNDED  — the model answers from ``build_grounded_prompt`` sources and
        the deterministic Sources footer is appended.
      * PARTIAL   — as GROUNDED, with a "limited sources" banner prefixed.

    ``task_type`` selects the generation task/budget (defaults to the free-text
    ``juris_research`` task, keeping that path byte-for-byte identical).
    ``text`` is the content shown to the model; ``query`` optionally overrides
    the retrieval query when the two differ.

    The banner/footer are applied by this function for non-streamed answers and
    passed as ``prefix``/``suffix`` for streamed ones (so they land in the
    final edited message rather than being lost).

    Retrieval failure fails closed: any exception from ``grounding.retrieve``
    is treated as UNGROUNDED (honest refusal, no model call), because an
    unreachable source is not a source.

    TODO(grounding-followup): ``grounding.retrieve(text)`` ignores the
    follow-up context, so an anaphoric follow-up ("and the penalty?") can
    ground on unrelated sources. Retrieval should incorporate ``followup_ctx``;
    tracked as a separate task.
    """
    mgr = get_account_manager()
    _t0 = time.time()

    retrieval_query = query if query is not None else text
    followup_ctx = _followup_context(chat_id, text)
    plan = _grounding.build_grounded_plan(retrieval_query, task_type,
                                          context=followup_ctx)

    # Refused (out-of-scope or UNGROUNDED): no model call, but still record the
    # miss for the learning loop. Refusals are recorded for learning but are
    # never cacheable (see cache.answer_is_cacheable / UNGROUNDED_MARKER), so
    # they can never be replayed once the same question becomes groundable.
    if plan["refusal"]:
        response_text = plan["refusal"]
        _latency_ms = int((time.time() - _t0) * 1000)
        mgr.record_query(account["account_id"],
                         input_tokens=_estimate_tokens(text),
                         output_tokens=_estimate_tokens(response_text),
                         model="")
        _record_turn(account["account_id"], chat_id, task_type,
                     text, response_text, "", _latency_ms, False)
        if plan["out_of_scope"]:
            logger.info("juris grounding: out-of-scope jurisdiction "
                        "(no model call) chat=%s", chat_id)
        else:
            logger.info("juris grounding: UNGROUNDED (no model call) chat=%s",
                        chat_id)
        return {
            "chat_id": chat_id,
            "text": response_text,
            "reply_markup": reply_markup,
            "parse_mode": "Markdown",
        }

    prompt = plan["prompt"]
    banner = plan["banner"]
    footer = plan["footer"]
    source_key = plan["source_key"]

    response_text, model, streamed, _cache_hit = _generate_reply(
        prompt, task_type, text, text, account["account_id"],
        chat_id=chat_id, reply_markup=reply_markup, context=followup_ctx,
        prefix=banner, suffix=footer, source_key=source_key)
    _latency_ms = int((time.time() - _t0) * 1000)

    have_answer = bool(response_text and response_text.strip())
    if not have_answer:
        logger.error(f"Empty response for legal query '{text[:80]}' from chat {chat_id}")
        response_text = "⚠️ I couldn't process that query. Please try rephrasing or use /menu for options."

    # The streamed message already carries the banner/footer; only un-streamed
    # real answers still need them applied here (never on an error/empty reply).
    # The raw answer is recorded for the learning loop (footer/banner are
    # presentation, not substance).
    if streamed:
        delivered_text = response_text
    elif have_answer:
        delivered_text = banner + response_text + footer
    else:
        delivered_text = response_text

    mgr.record_query(account["account_id"],
                     input_tokens=_estimate_tokens(prompt),
                     output_tokens=_estimate_tokens(response_text),
                     model=model)
    _record_turn(account["account_id"], chat_id, task_type, text,
                 response_text, model, _latency_ms, _cache_hit,
                 source_key=source_key)

    return {
        "chat_id": chat_id,
        "text": None if streamed else delivered_text,
        "reply_markup": reply_markup,
        "parse_mode": None if streamed else "Markdown",
    }


def _handle_free_text(text: str, chat_id: int, account: dict, admin: bool) -> dict:
    """Handle free-text legal queries with conversation state awareness."""
    state_key = str(chat_id)

    # Check if in conversation flow
    if state_key in _conversation_state:
        return _handle_conversation_flow(text, chat_id, account)

    # Check query limits
    mgr = get_account_manager()
    limit_check = mgr.check_query_limit(account["account_id"])
    if not limit_check["allowed"]:
        return {
            "chat_id": chat_id,
            "text": (
                f"⚠️ You've reached your daily query limit "
                f"({limit_check['limit']} queries/day).\n"
                "Upgrade your plan with /subscribe for more queries."
            ),
            "reply_markup": main_menu(),
        }

    reply_markup = main_menu() if not admin else admin_main_menu()
    return _build_legal_reply(text, chat_id, account, reply_markup=reply_markup)


# ---------------------------------------------------------------------------
# Conversation flow handler
# ---------------------------------------------------------------------------

def _handle_conversation_flow(text: str, chat_id: int, account: dict) -> dict:
    """Handle multi-step conversation flows (practice, study tools, etc.)."""
    state_key = str(chat_id)
    state = _conversation_state.get(state_key)
    if not state:
        return _handle_free_text(text, chat_id, account, False)

    step = state["step"]
    mgr = get_account_manager()
    limit_check = mgr.check_query_limit(account["account_id"])
    if not limit_check["allowed"]:
        del _conversation_state[state_key]
        return {
            "chat_id": chat_id,
            "text": "⚠️ Daily query limit reached. Try again tomorrow or upgrade your plan.",
            "reply_markup": main_menu(),
        }

    from core.juris_kai.prompt import build_prompt
    from core.juris_kai.legal_context import query_knowledge_base, build_context_preamble

    # Map conversation steps to prompt types
    step_prompt_map = {
        "search_topic": ("legal_teaching", "juris_legal_teaching", "Learn Law"),
        "search_case": ("legal_case_analysis", "juris_case_analysis", "Cases"),
        "gen_questions": ("legal_teaching", "juris_legal_teaching", "Practice"),
        "irac": ("legal_argument", "juris_argument_construction", "Practice"),
        "essay": ("legal_research", "juris_research", "Practice"),
        "mock_exam": ("legal_teaching", "juris_legal_teaching", "Practice"),
        "answer_eval": ("legal_argument", "juris_argument_construction", "Practice"),
        "flashcards": ("legal_flashcards", "juris_flashcards", "Study Tools"),
        "memory": ("legal_flashcards", "juris_flashcards", "Study Tools"),
        "quiz": ("legal_teaching", "juris_legal_teaching", "Study Tools"),
        "revision": ("legal_teaching", "juris_legal_teaching", "Study Tools"),
        "summarize": ("legal_research", "juris_research", "Documents"),
    }

    # Menu routing back
    menu_routing = {
        "search_topic": "learn_menu",
        "search_case": "case_law_menu",
        "gen_questions": "practice_menu",
        "irac": "practice_menu",
        "essay": "practice_menu",
        "mock_exam": "practice_menu",
        "answer_eval": "practice_menu",
        "flashcards": "study_tools_menu",
        "memory": "study_tools_menu",
        "quiz": "study_tools_menu",
        "revision": "study_tools_menu",
        "summarize": "documents_menu",
    }

    if step not in step_prompt_map:
        del _conversation_state[state_key]
        return _handle_free_text(text, chat_id, account, False)

    prompt_type, task_type, return_menu = step_prompt_map[step]

    # Resolve the return keyboard before streaming so the final edited message
    # carries it.
    menu_fn_name = menu_routing.get(step)
    if menu_fn_name:
        from core.juris_kai import menus
        menu_fn = getattr(menus, menu_fn_name, main_menu)
        keyboard = menu_fn()
    else:
        keyboard = main_menu()

    # Document summaries are not Ghana-law answers -- the user's own text is
    # being summarized -- so they deliberately stay on the ungrounded
    # build_prompt path. Every other step is a legal-answer surface and routes
    # through the shared strict-grounding path (_build_legal_reply), so it is
    # retrieval-gated and carries the Sources footer.
    if step == "summarize":
        legal_docs = query_knowledge_base(text)
        context_preamble = build_context_preamble(legal_docs)
        followup_ctx = _followup_context(chat_id, text)
        prompt = (build_prompt(prompt_type, text, context=followup_ctx)
                  + context_preamble)
        _t0 = time.time()
        response_text, model, streamed, _cache_hit = _generate_reply(
            prompt, task_type, text, f"your {step.replace('_', ' ')} request",
            account["account_id"], chat_id=chat_id, reply_markup=keyboard,
            context=followup_ctx)
        _latency_ms = int((time.time() - _t0) * 1000)

        mgr.record_query(account["account_id"],
                         input_tokens=_estimate_tokens(prompt),
                         output_tokens=_estimate_tokens(response_text),
                         model=model)
        _record_turn(account["account_id"], chat_id, task_type, text,
                     response_text, model, _latency_ms, _cache_hit)
        del _conversation_state[state_key]

        return {
            "chat_id": chat_id,
            "text": None if streamed else response_text,
            "reply_markup": keyboard,
            "parse_mode": None if streamed else "Markdown",
        }

    del _conversation_state[state_key]
    return _build_legal_reply(text, chat_id, account, reply_markup=keyboard,
                              task_type=task_type)


# ---------------------------------------------------------------------------
# Legacy command handling (delegates to commands.py)
# ---------------------------------------------------------------------------

def _handle_legacy_command(text: str, chat_id: int, account: dict, admin: bool) -> dict:
    """Handle slash-commands using the existing commands.py module."""
    try:
        parts = text.strip().split(" ", 1)
        command = parts[0].lstrip("/")
        args = parts[1] if len(parts) > 1 else ""

        from core.juris_kai.commands import (
            handle_help, handle_account, handle_subscribe,
            handle_learn, handle_case, handle_research,
            handle_argument, handle_flashcards, handle_progress,
            handle_group, handle_forget,
        )

        cmd_map = {
            "help": lambda: handle_help(),
            "account": lambda: handle_account(account),
            "subscribe": lambda: handle_subscribe(account, args),
            "learn": lambda: handle_learn(args, {}, account),
            "case": lambda: handle_case(args, {}, account),
            "research": lambda: handle_research(args, {}, account),
            "argument": lambda: handle_argument(args, {}, account),
            "flashcards": lambda: handle_flashcards(args, {}, account),
            "progress": lambda: handle_progress({}, account),
            "forget": lambda: handle_forget(account),
            "group": lambda: handle_group(args, account, admin),
        }

        if command in cmd_map:
            reply = cmd_map[command]()
        else:
            reply = f"Unknown command: /{command}. Type /help for available commands."

        return {
            "chat_id": chat_id,
            "text": reply,
            "reply_markup": main_menu() if not admin else admin_main_menu(),
            "parse_mode": "Markdown",
        }
    except Exception as e:
        logger.error(f"Command handler error: {e}")
        return {
            "chat_id": chat_id,
            "text": f"Error processing command. Please try again.",
            "reply_markup": main_menu(),
        }


# ---------------------------------------------------------------------------
# Callback query handling (inline keyboard buttons)
# ---------------------------------------------------------------------------

def handle_callback(callback_query: dict) -> dict | None:
    """Process inline keyboard button presses."""
    cb = callback_query
    callback_id = cb.get("id", "")
    data = cb.get("data", "")
    msg = cb.get("message", {})
    chat_id = msg.get("chat", {}).get("id", 0)
    from_user = cb.get("from", {})
    telegram_id = str(from_user.get("id", ""))

    if not chat_id or not data:
        return None

    # Disclaimer acceptance
    if data == "disclaimer_accept":
        mgr = get_account_manager()
        account = mgr.get_by_telegram(telegram_id)
        if account:
            mgr.accept_disclaimer(account["account_id"])

        answer_callback(callback_id, "Disclaimer accepted! ✅")
        return {
            "chat_id": chat_id,
            "text": "Thank you for acknowledging. " + WELCOME_TEXT,
            "reply_markup": main_menu(),
            "parse_mode": "Markdown",
        }

    # Confirm/Cancel actions
    if data.endswith("_confirm"):
        answer_callback(callback_id, "Processing...")
        return {
            "chat_id": chat_id,
            "text": "✅ Action confirmed.",
            "reply_markup": main_menu(),
        }
    if data.endswith("_cancel"):
        answer_callback(callback_id, "Cancelled.")
        return {
            "chat_id": chat_id,
            "text": "❌ Cancelled.",
            "reply_markup": main_menu(),
        }

    # Quiz answers
    if data.startswith("quiz_"):
        choice = data.replace("quiz_", "")
        answer_callback(callback_id, f"You selected {choice}")
        return {
            "chat_id": chat_id,
            "text": f"You selected option *{choice}*. ✅\n\n_Feedback: Good effort! Review the material and try again for mastery._",
            "reply_markup": study_tools_menu(),
            "parse_mode": "Markdown",
        }

    answer_callback(callback_id, "Action received.")
    return None


# ---------------------------------------------------------------------------
# Polling loop
# ---------------------------------------------------------------------------

_OFFSET_FILE_DEFAULT = "/project/ai-orchestrator/memory/juris-kai-telegram-offset"


def _offset_file() -> Path:
    return Path(os.environ.get("JURIS_KAI_TELEGRAM_OFFSET_FILE", _OFFSET_FILE_DEFAULT))


def _load_offset() -> int | None:
    """Return the last confirmed getUpdates offset (update_id + 1), or None.

    Persisted across restarts so a crash/restart does not reprocess (and
    re-run side effects for) the last update Telegram already delivered.
    """
    try:
        raw = _offset_file().read_text().strip()
        return int(raw) if raw else None
    except Exception:
        return None


def _save_offset(offset: int) -> None:
    try:
        path = _offset_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(offset))
    except Exception:
        pass


def poll_updates(offset: int | None = None) -> int | None:
    """Fetch and process new messages from Telegram. Returns next offset."""
    params: dict = {"timeout": POLL_TIMEOUT, "allowed_updates": ["message", "callback_query"]}
    if offset:
        params["offset"] = offset

    resp = telegram_api("getUpdates", params)
    if not resp.get("ok") and "webhook" in str(resp.get("description", "")).lower():
        # An active webhook blocks getUpdates entirely. Delete it once and
        # retry, so a stray webhook can never permanently break polling.
        logger.warning("getUpdates blocked by an active webhook -- deleting it")
        if delete_webhook():
            resp = telegram_api("getUpdates", params)

    if not resp.get("ok"):
        err = _redact(resp.get('description', ''))
        logger.error(f"getUpdates failed: {err}")
        # If another getUpdates listener conflicts (409), back off to avoid
        # a tight retry loop that would hammer Telegram.
        backoff = ERROR_BACKOFF * 2 if '409' in str(err) else ERROR_BACKOFF
        time.sleep(backoff)
        return offset

    for update in resp.get("result", []):
        update_id = update["update_id"]

        # Handle callback queries (inline button presses)
        if "callback_query" in update:
            cb = update["callback_query"]
            try:
                result = handle_callback(cb)
                if result:
                    # Remove inline keyboard from original message
                    msg = cb.get("message", {})
                    original_chat_id = msg.get("chat", {}).get("id")
                    original_msg_id = msg.get("message_id")
                    if original_chat_id and original_msg_id:
                        edit_reply_markup(original_chat_id, original_msg_id)
                    # Send reply if needed (guarded against cross-user leaks)
                    _send_guarded(original_chat_id, result)
            except Exception as e:
                logger.error(f"Callback error: {e}")
            offset = update_id + 1
            _save_offset(offset)
            continue

        # Handle regular messages
        if "message" in update:
            msg = update["message"]
            chat_id = msg["chat"]["id"]
            text = msg.get("text", "")
            from_user = msg.get("from", {})

            if not text:
                offset = update_id + 1
                _save_offset(offset)
                continue

            # Send typing indicator
            send_typing(chat_id)

            try:
                result = handle_message(update)
            except Exception as e:
                logger.error(f"Message handling error: {e}")
                result = {
                    "chat_id": chat_id,
                    "text": "An error occurred. Please try again.",
                    "reply_markup": main_menu(),
                }

            if result and result.get("text"):
                # All sends go through the guarded gateway
                _send_guarded(chat_id, result)

        offset = update_id + 1
        _save_offset(offset)

    return offset


def run_forever():
    """Main entry point — poll forever. Run as a standalone process."""
    if not _get_bot_token():
        logger.error("JURIS_KAI_BOT_TOKEN not set. Bot cannot start.")
        print("ERROR: JURIS_KAI_BOT_TOKEN environment variable is not set.")
        return

    # Clear any active webhook before long-polling getUpdates (Telegram forbids
    # both at once). Best-effort: never block startup on it.
    delete_webhook()

    logger.info("Juris Kai bot starting...")
    print("⚖️ Juris Kai bot starting...")

    # KAI Telegram Module: confirm this bot's registered identity/owner/caps.
    if _TG_BOT is not None:
        logger.info("telegram module: bot=%s owner=%s caps=%s",
                    _TG_BOT.bot_id, _TG_BOT.owner_module,
                    sorted(_TG_BOT.capabilities))
        if not _tg_registry.is_enabled(_TG_BOT):
            logger.error("telegram module: bot %s is DISABLED in the registry",
                         _TG_BOT.bot_id)

    # Ensure DB is initialized
    mgr = get_account_manager()
    print(f"Database ready at: {mgr.db}")

    # Health check file — touched after each successful poll cycle.
    # Uses /project/ memory dir (NOT /tmp — PrivateTmp=yes isolates /tmp).
    # Monitor with: find /project/ai-orchestrator/memory/juris-kai-health -mmin +10
    HEALTH_FILE = Path("/project/ai-orchestrator/memory/juris-kai-health")
    HEALTH_FILE.touch()

    offset = _load_offset()
    while True:
        try:
            offset = poll_updates(offset)
            HEALTH_FILE.touch()  # Successful cycle — bot is alive
        except Exception as e:
            logger.error(f"Poll error: {e}")
            time.sleep(ERROR_BACKOFF)


if __name__ == "__main__":
    run_forever()


# ---------------------------------------------------------------------------
# Module doc: This module must NEVER import:
#   core.build_manager, core.approval, core.deployment_manager,
#   or anything that grants operational capabilities.
# ---------------------------------------------------------------------------
