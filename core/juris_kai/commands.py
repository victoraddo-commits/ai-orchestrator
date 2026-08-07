"""Command handlers for Juris Kai Multi-Tenant Legal Assistant.

Updated for multi-tenant: all handlers now accept an account dict with
subscription info, usage limits, and billing context.

Security: NO imports of core.build_manager, core.approval, or
core.deployment_manager.
"""

import re
from typing import Dict, Any

from core.juris_kai.prompt import build_prompt
from core.juris_kai.accounts import (
    get_account_manager,
    SUBSCRIPTION_TIERS,
    DISCLAIMER_TEXT,
)


def handle_command(text: str, update: Dict[str, Any], account: Dict[str, Any]) -> str:
    """Process legal assistant commands with multi-tenant context."""

    try:
        parts = text.strip().split(" ", 1)
        command = parts[0].lstrip("/")
        args = parts[1] if len(parts) > 1 else ""

        if command in ("help", "start"):
            return handle_help()
        elif command == "account":
            return handle_account(account)
        elif command == "subscribe":
            return handle_subscribe(account)
        elif command == "learn":
            return handle_learn(args, update, account)
        elif command == "case":
            return handle_case(args, update, account)
        elif command == "research":
            return handle_research(args, update, account)
        elif command == "argument":
            return handle_argument(args, update, account)
        elif command == "flashcards":
            return handle_flashcards(args, update, account)
        elif command == "profile":
            return handle_profile(args, update, account)
        elif command == "document":
            return handle_document(args, update, account)
        elif command == "progress":
            return handle_progress(update, account)
        else:
            return f"Unknown command: /{command}. Type /help for available commands."

    except Exception as e:
        return f"Error processing command. Please try again."


def handle_help() -> str:
    from core.juris_kai.bot import HELP_TEXT
    return HELP_TEXT


# ---- Account & Subscription Commands ----

def handle_account(account: Dict[str, Any]) -> str:
    """Show account status and subscription details."""
    mgr = get_account_manager()
    sub = mgr.get_active_subscription(account["account_id"])

    lines = [f"👤 *Account*: {account.get('full_name', 'Not set')}"]
    lines.append(f"📧 Email: {account.get('email', 'Not set')}")
    lines.append(f"📱 Phone: {account.get('phone', 'Not set')}")

    if sub:
        status = "✅ Active" if sub["is_active"] else "❌ Expired"
        lines.append(f"\n📦 *Plan*: {sub['tier_name']} ({status})")
        if sub["end"]:
            lines.append(f"⏳ Expires: {sub['end'][:10]}")

        limits = sub["limits"]
        limit_check = mgr.check_query_limit(account["account_id"])
        lines.append(
            f"🔍 Queries today: {limits['max_queries_per_day'] - limit_check['remaining']}"
            f"/{limits['max_queries_per_day']}"
        )

        doc_check = mgr.check_document_limit(account["account_id"])
        lines.append(
            f"📄 Documents this month: {limits['max_documents_per_month'] - doc_check['remaining']}"
            f"/{limits['max_documents_per_month']}"
        )

    return "\n".join(lines)


def handle_subscribe(account: Dict[str, Any]) -> str:
    """Show available subscription plans."""
    mgr = get_account_manager()
    sub = mgr.get_active_subscription(account["account_id"])

    lines = ["📦 *Subscription Plans*\n"]
    current_tier = sub["tier"] if sub else "free_trial"

    for tier_key, tier in SUBSCRIPTION_TIERS.items():
        marker = " ✅ (current)" if tier_key == current_tier else ""
        lines.append(
            f"*{tier['name']}*{marker}\n"
            f"  💰 GH₵{tier['price_ghs']}"
            f"{'/month' if 'monthly' in tier_key else '/year' if 'annual' in tier_key else ''}\n"
            f"  📄 {tier['max_documents_per_month']} documents/month\n"
            f"  🔍 {tier['max_queries_per_day']} queries/day\n"
        )

    lines.append(
        "\nTo upgrade, use:\n"
        "  /subscribe \\<plan_name\\> \\<phone_number\\>\n"
        "Example: /subscribe monthly_basic 0244123456"
    )
    return "\n".join(lines)


def handle_profile(args: str, update: Dict[str, Any], account: Dict[str, Any]) -> str:
    """View or update profile."""
    mgr = get_account_manager()

    if not args.strip():
        return handle_account(account)

    # Parse: /profile name John Doe  or  /profile email john@example.com
    parts = args.strip().split(" ", 1)
    field = parts[0].lower()
    value = parts[1] if len(parts) > 1 else ""

    field_map = {"name": "full_name", "email": "email", "phone": "phone"}
    if field not in field_map:
        return "Usage: /profile name|email|phone <value>\nExample: /profile name John Doe"

    if not value:
        return f"Usage: /profile {field} <value>"

    mgr.update_profile(account["account_id"], **{field_map[field]: value})
    return f"✅ Profile updated: {field} = {value}"


# ---- Legal Research Commands ----

def handle_learn(topic: str, update: Dict[str, Any], account: Dict[str, Any]) -> str:
    """Learn about a legal topic."""
    if not topic.strip():
        return "Usage: /learn <legal topic>\nExample: /learn contract law"

    prompt = build_prompt("legal_teaching", topic)
    try:
        from core.ai.ai_router import delegate
        result = delegate(prompt, task_type="juris_legal_teaching", capability="text_task")
        return result["response"]
    except Exception as e:
        return f"Unable to provide legal teaching. Please try again later."


def handle_case(case_name: str, update: Dict[str, Any], account: Dict[str, Any]) -> str:
    """Analyze a legal case."""
    if not case_name.strip():
        return "Usage: /case <case name>\nExample: /case Donoghue v Stevenson"

    prompt = build_prompt("legal_case_analysis", case_name)
    try:
        from core.ai.ai_router import delegate
        result = delegate(prompt, task_type="juris_case_analysis", capability="text_task")
        return result["response"]
    except Exception as e:
        return f"Unable to analyze case. Please try again later."


def handle_research(query: str, update: Dict[str, Any], account: Dict[str, Any]) -> str:
    """Research legal concepts."""
    if not query.strip():
        return "Usage: /research <legal query>"

    prompt = build_prompt("legal_research", query)
    try:
        from core.ai.ai_router import delegate
        result = delegate(prompt, task_type="juris_research", capability="text_task")
        return result["response"]
    except Exception as e:
        return f"Unable to research. Please try again later."


def handle_argument(topic: str, update: Dict[str, Any], account: Dict[str, Any]) -> str:
    """Construct legal arguments."""
    if not topic.strip():
        return "Usage: /argument <legal topic>\nExample: /argument self-defense"

    # Check if feature is available in subscription
    mgr = get_account_manager()
    sub = mgr.get_active_subscription(account["account_id"])
    if sub and "argument_construction" not in sub.get("features", []):
        return "⚠️ Legal argument construction requires a Basic or Professional plan.\nUpgrade with /subscribe"

    prompt = build_prompt("legal_argument", topic)
    try:
        from core.ai.ai_router import delegate
        result = delegate(prompt, task_type="juris_argument_construction", capability="text_task")
        return result["response"]
    except Exception as e:
        return f"Unable to construct argument. Please try again later."


def handle_flashcards(topic: str, update: Dict[str, Any], account: Dict[str, Any]) -> str:
    """Generate legal flashcards."""
    if not topic.strip():
        return "Usage: /flashcards <legal topic>"

    mgr = get_account_manager()
    sub = mgr.get_active_subscription(account["account_id"])
    if sub and "flashcards" not in sub.get("features", []):
        return "⚠️ Flashcards require a Professional plan.\nUpgrade with /subscribe"

    prompt = build_prompt("legal_flashcards", topic)
    try:
        from core.ai.ai_router import delegate
        result = delegate(prompt, task_type="juris_flashcards", capability="text_task")
        return result["response"]
    except Exception as e:
        return f"Unable to generate flashcards. Please try again later."


# ---- Document Analysis ----

def handle_document(args: str, update: Dict[str, Any], account: Dict[str, Any]) -> str:
    """Handle document upload and analysis (paid feature)."""
    mgr = get_account_manager()

    if not args.strip():
        return (
            "📄 *Document Analysis*\n\n"
            "Send me a legal document (PDF or text) for AI analysis.\n\n"
            f"💰 Cost: GH₵2.00 per page\n"
            "Features: summary, key legal principles, citation extraction.\n\n"
            "To upload, simply send the document file in this chat."
        )

    # Check document limits
    doc_check = mgr.check_document_limit(account["account_id"])
    if not doc_check["allowed"]:
        return (
            f"⚠️ You've reached your monthly document limit "
            f"({doc_check['limit']} documents/month).\n"
            "Upgrade your plan with /subscribe for more."
        )

    # Bill for document analysis
    billing = mgr.bill_document_analysis(
        account["account_id"], args.strip(), page_count=1
    )

    return (
        f"📄 Document queued for analysis:\n"
        f"  Name: {billing['document_name']}\n"
        f"  Estimated cost: GH₵{billing['cost_ghs']:.2f}\n"
        f"  Reference: {billing['analysis_id']}\n\n"
        "Your document will be analyzed shortly. I'll send the results here."
    )


# ---- Learning Progress ----

def handle_progress(update: Dict[str, Any], account: Dict[str, Any]) -> str:
    """Show learning progress."""
    session = get_user_session(update.get("chat_id", ""))
    topics_studied = session.get("topics_studied", [])

    if not topics_studied:
        return "No topics studied yet. Start learning with /learn <topic>"

    return f"*Topics studied*:\n" + "\n".join(f"• {t}" for t in topics_studied)


# This module must NEVER import:
#   core.build_manager, core.approval, core.deployment_manager
