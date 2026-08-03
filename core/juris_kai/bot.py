"""Juris Kai Bot - Legal Expert System

This module implements the core bot functionality for juris_kai, which provides
legal expertise and educational assistance. It enforces strict security
boundaries that prevent any access to operational capabilities like build
management, approvals, or deployments.

Security Constraints:
1. Must never import core.build_manager, core.approval, core.deployment_manager
2. Must never import operational modules
3. Authorized only for specific users via environment variable
4. Text-only responses, no executable code or tool use
5. Only text_task providers allowed for responses

This is a direct copy of the law_tutor bot security pattern with legal focus.
"""

import os
import re
from pathlib import Path

from core.juris_kai.commands import handle_command
from core.juris_kai.session import get_user_session
from core.juris_kai.prompt import build_prompt
from core.memory import save
from core.ai.ai_router import AllProvidersFailed

# The environment variable that controls authorization (same pattern as law_tutor)
JURIS_KAI_CHAT_ID = os.getenv("JURIS_KAI_CHAT_ID")

# Help text for user commands
HELP_TEXT = (
    "I am Juris Kai, a legal expert assistant. My purpose is to provide "
    "educational legal guidance and explanations.\n\n"
    "Available commands:\n"
    "/help - Show this help text\n"
    "/start - Show this help text\n"
    "/learn <topic> - Learn about a legal topic\n"
    "/case <name> - Analyze a legal case\n"
    "/research <query> - Research legal concepts\n"
    "/argument <topic> - Construct legal arguments\n"
    "/flashcards <topic> - Generate flashcards for study\n"
    "/progress - Show learning progress\n"
    "\nI only respond to authorized users. Please ensure your chat ID is "
    "configured in the environment."
)

def handle_message(update):
    """Process incoming Telegram messages with security checks."""
    
    # Security: Check if the sender is authorized (same as law_tutor)
    if JURIS_KAI_CHAT_ID is None:
        return "Legal assistant setup incomplete - no authorized chat ID configured."
    
    if str(update.get("chat_id", "")) != JURIS_KAI_CHAT_ID:
        return "This is a private legal assistant for authorized users only. You cannot interact with it."
    
    # Extract message text
    message_text = update.get("text", "").strip()
    if not message_text:
        return HELP_TEXT
    
    # Handle commands (starts with /)
    if message_text.startswith("/"):
        return handle_command(message_text, update)
    
    # Default: treat as a learning query
    return handle_command(f"/learn {message_text}", update)

def _handle_error(error):
    """Handle errors gracefully without exposing system details."""
    if isinstance(error, AllProvidersFailed):
        return "Legal research is currently unavailable. Please try again later."
    return "An error occurred while processing your request. Please try again."

# Ensure no operational imports (same security principle as law_tutor)
# This module and everything it imports (telegram_client, prompt, session, commands) 
# must never import core.build_manager, core.approval, core.deployment_manager, 
# or anything else that grants an operational capability.