"""Juris Kai Bot - Multi-Tenant Legal Expert System.

Handles Telegram messages for the paid, multi-tenant Juris Kai bot
(@Juriskai_bot). Each user gets their own isolated account.

Security: NO imports of core.build_manager, core.approval, or
core.deployment_manager. Only text_task AI providers are used.
"""

import os
import logging
from typing import Optional, Dict, Any

from core.juris_kai.accounts import (
    get_account_manager,
    DISCLAIMER_TEXT,
    SUBSCRIPTION_TIERS,
)
from core.juris_kai.commands import handle_command
from core.juris_kai.session import get_user_session

logger = logging.getLogger("juris_kai.bot")

# Bot token from environment
BOT_TOKEN = os.getenv("JURIS_KAI_BOT_TOKEN", "")

HELP_TEXT = (
    "⚖️ *Juris Kai — Legal Research Assistant*\n\n"
    "I provide educational legal guidance and document analysis.\n\n"
    "*Commands:*\n"
    "/help — Show this help\n"
    "/start — Welcome message + disclaimer\n"
    "/account — View your account status\n"
    "/subscribe — View subscription plans\n"
    "/learn \\<topic\\> — Learn about a legal topic\n"
    "/case \\<name\\> — Analyze a legal case\n"
    "/research \\<query\\> — Research legal concepts\n"
    "/argument \\<topic\\> — Construct legal arguments\n"
    "/flashcards \\<topic\\> — Generate study flashcards\n"
    "/profile — View/update your profile\n"
    "/document — Upload a document for analysis (paid feature)\n"
    "\n_Not a substitute for professional legal advice._"
)

WELCOME_TEXT = (
    "Welcome to Juris Kai! ⚖️\n\n"
    "I'm your AI-powered Ghanaian legal research assistant and tutor.\n\n"
    "⚠️ *Disclaimer*: I am not a lawyer. My responses are for educational "
    "and informational purposes only.\n\n"
    "Type /help to see what I can do, or ask me a legal question!"
)


def handle_message(update: Dict[str, Any]) -> str:
    """Process incoming Telegram messages with multi-tenant account handling.

    Flow:
    1. Get or create account for this Telegram user
    2. If new user, show disclaimer
    3. Check subscription/usage limits
    4. Route to command handler
    """
    telegram_id = str(update.get("chat_id", ""))
    if not telegram_id:
        return "Error: Cannot identify user."

    message_text = update.get("text", "").strip()

    # Get or create account
    mgr = get_account_manager()
    account = mgr.get_or_create(telegram_id, update.get("from_first_name", ""))

    # New users: show disclaimer first
    if account["is_new"]:
        return DISCLAIMER_TEXT + "\n\n" + WELCOME_TEXT

    # Check disclaimer acceptance
    if not account.get("disclaimer_accepted") and not message_text.startswith("/start"):
        return (
            "Before using Juris Kai, please acknowledge the disclaimer:\n\n"
            + DISCLAIMER_TEXT
            + "\n\nReply with *I understand* or type /start to continue."
        )

    # Check if account is active
    if not account.get("is_active"):
        return "Your account has been deactivated. Contact support for assistance."

    # Handle /start specially
    if message_text.startswith("/start"):
        if not account.get("disclaimer_accepted"):
            mgr.accept_disclaimer(account["account_id"])
            return "Thank you for acknowledging. " + WELCOME_TEXT
        return WELCOME_TEXT

    # Route to command handler with account context
    if message_text.startswith("/"):
        return handle_command(message_text, update, account)

    # Default: treat as a legal query
    # Check query limits
    limit_check = mgr.check_query_limit(account["account_id"])
    if not limit_check["allowed"]:
        return (
            f"⚠️ You've reached your daily query limit "
            f"({limit_check['limit']} queries/day).\n"
            "Upgrade your plan with /subscribe for more queries."
        )

    # Process the query
    from core.juris_kai.commands import handle_learn
    response = handle_learn(message_text, update, account)
    mgr.record_query(account["account_id"])
    return response


def _handle_error(error: Exception) -> str:
    """Handle errors gracefully without exposing system details."""
    from core.ai.ai_router import AllProvidersFailed
    if isinstance(error, AllProvidersFailed):
        return "Legal research is currently unavailable. Please try again later."
    logger.error(f"Juris Kai error: {error}")
    return "An error occurred. Please try again later."


# This module and all it imports must NEVER import:
#   core.build_manager, core.approval, core.deployment_manager,
#   or anything that grants operational capabilities.
