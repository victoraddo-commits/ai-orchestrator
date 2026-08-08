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

from core.juris_kai.accounts import (
    get_account_manager,
    DISCLAIMER_TEXT,
    SUBSCRIPTION_TIERS,
)
from core.juris_kai.commands import handle_command
from core.juris_kai import menus as _menus
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

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

BOT_TOKEN = os.environ.get("JURIS_KAI_BOT_TOKEN", "")
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
    "/subscribe — View subscription plans\n\n"
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
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"
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


def _delegate_with_timeout(prompt: str, task_type: str, fallback_label: str) -> str:
    """Call ai_router.delegate() in a background thread with a hard timeout.

    Returns the response text on success, or a user-friendly error message
    on timeout or failure.  This prevents a stalled provider from blocking
    the bot's synchronous polling loop indefinitely.
    """
    try:
        from core.ai.ai_router import delegate
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(
                delegate, prompt,
                task_type=task_type, capability="text_task",
            )
            result = future.result(timeout=DELEGATE_TIMEOUT)
            return result["response"]
    except concurrent.futures.TimeoutError:
        logger.warning(f"Delegate timeout for {task_type} ({DELEGATE_TIMEOUT}s)")
        return f"⚠️ Query timed out. Please try a more specific question."
    except Exception as e:
        logger.error(f"Delegate failed for {task_type}: {e}")
        return f"⚠️ Unable to load {fallback_label}. Please try again later."


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
    admin = is_admin(telegram_id)

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
    """Generate a legal teaching response for a topic button."""
    from core.juris_kai.prompt import build_prompt

    topic_display = label.split(" ", 1)[1] if " " in label else label
    prompt = build_prompt("legal_teaching", f"Ghana {topic_display}")

    # Run delegate with a hard wall-clock timeout so one slow provider
    # doesn't block the bot's entire polling loop indefinitely.
    response_text = _delegate_with_timeout(prompt, "juris_legal_teaching", f"information about {topic_display}")

    mgr = get_account_manager()
    mgr.record_query(account["account_id"])

    return {
        "chat_id": chat_id,
        "text": response_text,
        "reply_markup": learn_menu(),
    }


def _handle_case_query(query_type: str, chat_id: int, account: dict) -> dict:
    """Handle case law queries."""
    from core.juris_kai.prompt import build_prompt

    prompt = build_prompt("legal_case_analysis", f"{query_type} in Ghana law")
    response_text = _delegate_with_timeout(prompt, "juris_case_analysis", query_type.lower())

    mgr = get_account_manager()
    mgr.record_query(account["account_id"])

    return {
        "chat_id": chat_id,
        "text": response_text,
        "reply_markup": case_law_menu(),
    }


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

    # Process as legal query
    from core.juris_kai.prompt import build_prompt
    prompt = build_prompt("legal_research", text)
    response_text = _delegate_with_timeout(prompt, "juris_research", "your query")

    mgr.record_query(account["account_id"])

    return {
        "chat_id": chat_id,
        "text": response_text,
        "reply_markup": main_menu() if not admin else admin_main_menu(),
    }


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
    prompt = build_prompt(prompt_type, text)
    response_text = _delegate_with_timeout(prompt, task_type, f"your {step.replace('_', ' ')} request")

    mgr.record_query(account["account_id"])
    del _conversation_state[state_key]

    # Route back to appropriate menu
    menu_fn_name = menu_routing.get(step)
    if menu_fn_name:
        from core.juris_kai import menus
        menu_fn = getattr(menus, menu_fn_name, main_menu)
        keyboard = menu_fn()
    else:
        keyboard = main_menu()

    return {"chat_id": chat_id, "text": response_text, "reply_markup": keyboard}


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
        )

        cmd_map = {
            "help": lambda: handle_help(),
            "account": lambda: handle_account(account),
            "subscribe": lambda: handle_subscribe(account),
            "learn": lambda: handle_learn(args, {}, account),
            "case": lambda: handle_case(args, {}, account),
            "research": lambda: handle_research(args, {}, account),
            "argument": lambda: handle_argument(args, {}, account),
            "flashcards": lambda: handle_flashcards(args, {}, account),
            "progress": lambda: handle_progress({}, account),
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

def poll_updates(offset: int | None = None) -> int | None:
    """Fetch and process new messages from Telegram. Returns next offset."""
    params: dict = {"timeout": POLL_TIMEOUT, "allowed_updates": ["message", "callback_query"]}
    if offset:
        params["offset"] = offset

    resp = telegram_api("getUpdates", params)
    if not resp.get("ok"):
        err = resp.get('description', '')
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
                    # Send reply if needed
                    if result.get("text"):
                        send_message(
                            result["chat_id"],
                            result["text"],
                            reply_markup=result.get("reply_markup"),
                            parse_mode=result.get("parse_mode", "Markdown"),
                        )
            except Exception as e:
                logger.error(f"Callback error: {e}")
            offset = update_id + 1
            continue

        # Handle regular messages
        if "message" in update:
            msg = update["message"]
            chat_id = msg["chat"]["id"]
            text = msg.get("text", "")
            from_user = msg.get("from", {})

            if not text:
                offset = update_id + 1
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
                send_message(
                    result["chat_id"],
                    result["text"],
                    reply_markup=result.get("reply_markup"),
                    parse_mode=result.get("parse_mode", "Markdown"),
                )

        offset = update_id + 1

    return offset


def run_forever():
    """Main entry point — poll forever. Run as a standalone process."""
    if not BOT_TOKEN:
        logger.error("JURIS_KAI_BOT_TOKEN not set. Bot cannot start.")
        print("ERROR: JURIS_KAI_BOT_TOKEN environment variable is not set.")
        return

    logger.info("Juris Kai bot starting...")
    print("⚖️ Juris Kai bot starting...")

    # Ensure DB is initialized
    mgr = get_account_manager()
    print(f"Database ready at: {mgr.db}")

    offset = None
    while True:
        try:
            offset = poll_updates(offset)
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
