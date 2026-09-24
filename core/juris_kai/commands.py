"""Command handlers for Juris Kai Multi-Tenant Legal Assistant.

Updated for multi-tenant: all handlers now accept an account dict with
subscription info, usage limits, and billing context.

Security: NO imports of core.build_manager, core.approval, or
core.deployment_manager.
"""

import re
from typing import Dict, Any

from core.juris_kai.accounts import (
    get_account_manager,
    SUBSCRIPTION_TIERS,
    DISCLAIMER_TEXT,
)
from core.juris_kai import entitlements
from core.juris_kai.session import get_user_session


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
            return handle_subscribe(account, args)
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
        elif command == "seat":
            return handle_seat(args, account)
        elif command == "org":
            return handle_org(args, account)
        elif command == "profile":
            return handle_profile(args, update, account)
        elif command == "document":
            return handle_document(args, update, account)
        elif command == "progress":
            return handle_progress(update, account)
        elif command == "forget":
            return handle_forget(account)
        # ---- Phase 6 practice + research tools ----
        elif command == "contract":
            return handle_contract(args, update, account)
        elif command == "matrix":
            return handle_matrix(args, update, account)
        elif command == "chronology":
            return handle_chronology(args, update, account)
        elif command in ("authorities", "authority"):
            return handle_authorities(args, update, account)
        elif command == "statute":
            return handle_statute(args, update, account)
        elif command == "caselaw":
            return handle_caselaw(args, update, account)
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

        meter = mgr.usage_meter(account["account_id"])
        deep = meter["deep_research"]
        lines.append(
            f"🔬 Deep Research today: {deep['used']}/{deep['limit']}"
        )
        lines.append(f"🔤 Tokens today: {meter['tokens_today']}")

    return "\n".join(lines)


def handle_subscribe(account: Dict[str, Any], args: str = "") -> str:
    """Show subscription plans, or start a checkout for a named tier.

    ``/subscribe``                 -> list plans (unchanged legacy behaviour)
    ``/subscribe <tier> [email]``  -> initialize a Paystack (or Hubtel) checkout
    """
    mgr = get_account_manager()
    sub = mgr.get_active_subscription(account["account_id"])

    tier_arg = (args or "").strip().split(" ", 1)
    tier_key = tier_arg[0].strip().lower() if tier_arg and tier_arg[0].strip() else ""
    email_arg = tier_arg[1].strip() if len(tier_arg) > 1 else ""

    if tier_key:
        return _start_checkout(account, tier_key, email_arg)

    lines = ["📦 *Subscription Plans*\n"]
    current_tier = sub["tier"] if sub else "free_trial"

    for key, tier in SUBSCRIPTION_TIERS.items():
        marker = " ✅ (current)" if key == current_tier else ""
        period = ("/year" if "annual" in key
                  else "/month" if "monthly" in key else "")
        lines.append(
            f"*{tier['name']}*{marker}\n"
            f"  💰 GH₵{tier['price_ghs']}{period}\n"
            f"  📄 {tier['max_documents_per_month']} documents/month\n"
            f"  🔍 {tier['max_queries_per_day']} queries/day\n"
        )

    lines.append(
        "\nTo upgrade, use:\n"
        "  /subscribe \\<tier\\> \\[email\\]\n"
        "Example: /subscribe monthly_basic you@example.com"
    )
    return "\n".join(lines)


def _start_checkout(account: Dict[str, Any], tier_key: str, email: str) -> str:
    """Initialize a subscription checkout using the configured provider."""
    if tier_key not in SUBSCRIPTION_TIERS:
        return (f"Unknown plan: {tier_key}\n"
                "Use /subscribe to see available plans.")
    tier = SUBSCRIPTION_TIERS[tier_key]
    if float(tier.get("price_ghs") or 0) <= 0:
        return f"{tier['name']} is free — no payment needed. Use /subscribe for other plans."

    try:
        from core.juris_kai import paystack_checkout as checkout
        result = checkout.create_checkout(account, tier_key, email=email or None)
    except ValueError as exc:
        return f"⚠️ {exc}"
    except Exception:
        return "Payment is temporarily unavailable. Please try again later."

    if not result.get("success"):
        return f"⚠️ Checkout failed: {result.get('error') or 'unknown error'}"

    url = result.get("authorization_url") or ""
    if not url:
        ref = result.get("reference", "")
        return (f"📦 {tier['name']} checkout created ({result.get('provider')}).\n"
                f"Reference: {ref}\nComplete payment on your phone to activate.")
    return (
        f"💳 *{tier['name']}* — GH₵{tier['price_ghs']}\n"
        f"Complete your payment here:\n{url}\n"
        f"Reference: {result.get('reference', '')}"
    )


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

def _firewall(answer: str) -> str:
    """Strip unverifiable citations from a grounded answer (fail-open)."""
    try:
        from core.juris_kai import citation_firewall
        return citation_firewall.apply_citation_firewall(answer)["text"]
    except Exception:  # noqa: BLE001 - never break a command reply
        return answer


def _grounded_command_text(topic: str, task_type: str,
                           failure_message: str) -> str:
    """Ground a slash-command answer, or return the shared refusal.

    Slash commands return plain text (they have no Telegram ``chat_id`` of
    their own), so this retrieves on the user's topic, gates on the verdict,
    and only then calls the model with ``build_grounded_prompt``. The
    deterministic Sources footer (and PARTIAL banner) are appended to the
    returned text. Out-of-scope and UNGROUNDED questions never reach a model.
    """
    from core.juris_kai import grounding

    plan = grounding.build_grounded_plan(topic, task_type)
    if plan["refusal"]:
        return plan["refusal"]
    try:
        from core.ai.ai_router import delegate
        result = delegate(plan["prompt"], task_type=task_type,
                          capability="text_task")
        answer = result.get("response") or ""
    except Exception:
        return failure_message
    return plan["banner"] + _firewall(answer) + plan["footer"]


def handle_learn(topic: str, update: Dict[str, Any], account: Dict[str, Any]) -> str:
    """Learn about a legal topic (strict grounding)."""
    if not topic.strip():
        return "Usage: /learn <legal topic>\nExample: /learn contract law"

    return _grounded_command_text(
        topic, "juris_legal_teaching",
        "Unable to provide legal teaching. Please try again later.")


def handle_case(case_name: str, update: Dict[str, Any], account: Dict[str, Any]) -> str:
    """Analyze a legal case (strict grounding)."""
    if not case_name.strip():
        return "Usage: /case <case name>\nExample: /case Donoghue v Stevenson"

    return _grounded_command_text(
        case_name, "juris_case_analysis",
        "Unable to analyze case. Please try again later.")


def handle_research(query: str, update: Dict[str, Any], account: Dict[str, Any]) -> str:
    """Research legal concepts (strict grounding).

    §37: Juris Kai requests the legal-research capability from KAI's unified
    workforce (teammate factory + mission engine + Model Fabric) instead of
    driving the model directly. The historical direct call is kept as a
    fallback so the command never regresses. Both paths are now gated by
    retrieval: the grounded prompt (with sources) is what the workforce/model
    receives, and the Sources footer is appended to whatever answers.
    """
    if not query.strip():
        return "Usage: /research <legal query>"

    from core.juris_kai import grounding

    plan = grounding.build_grounded_plan(query, "juris_research")
    if plan["refusal"]:
        return plan["refusal"]

    try:
        from core.integration.module_bridge import get_bridge
        result = get_bridge().request_capability(
            "juris-kai", "legal_research", objective=plan["prompt"],
            execute=True, skills=["legal_research"])
        mission = result.get("mission") or {}
        for task in mission.get("tasks") or []:
            if task.get("skill_id") == "legal_research" and task.get("output"):
                output = task["output"]
                if isinstance(output, dict):
                    output = (output.get("response") or output.get("text")
                              or str(output))
                return plan["banner"] + _firewall(str(output)) + plan["footer"]
    except Exception:
        pass

    try:
        from core.ai.ai_router import delegate
        result = delegate(plan["prompt"], task_type="juris_research",
                          capability="text_task")
        return plan["banner"] + _firewall(result.get("response") or "") + plan["footer"]
    except Exception:
        return "Unable to research. Please try again later."


def handle_argument(topic: str, update: Dict[str, Any], account: Dict[str, Any]) -> str:
    """Construct legal arguments (strict grounding)."""
    if not topic.strip():
        return "Usage: /argument <legal topic>\nExample: /argument self-defense"

    # Check if feature is available in subscription (entitlement-gated).
    mgr = get_account_manager()
    prompt = entitlements.check_feature(mgr, account["account_id"],
                                        "argument_construction")
    if prompt:
        return prompt

    return _grounded_command_text(
        topic, "juris_argument_construction",
        "Unable to construct argument. Please try again later.")


def handle_flashcards(topic: str, update: Dict[str, Any], account: Dict[str, Any]) -> str:
    """Generate legal flashcards (strict grounding; output shape preserved)."""
    if not topic.strip():
        return "Usage: /flashcards <legal topic>"

    mgr = get_account_manager()
    prompt = entitlements.check_feature(mgr, account["account_id"],
                                        "flashcards")
    if prompt:
        return prompt

    return _grounded_command_text(
        topic, "juris_flashcards",
        "Unable to generate flashcards. Please try again later.")


# ---- Phase 6 practice + research tools ----

def _render_tool(tool_fn, *args, **kwargs) -> str:
    """Run a practice/research tool and return its gated rendering.

    The tool itself routes its output through the AgentGuard legal output gate
    (citation firewall + injection guard); this only surfaces a safe message if
    the tool is unavailable.
    """
    try:
        return tool_fn(*args, **kwargs)["rendered"]
    except Exception:  # noqa: BLE001 - a command must never crash the bot
        return "⚠️ That tool is unavailable right now. Please try again later."


def handle_contract(args: str, update: Dict[str, Any], account: Dict[str, Any]) -> str:
    """Analyse a pasted contract in the zero-trust workspace (never the corpus)."""
    if not (args or "").strip():
        return ("Usage: /contract <paste the contract text>\n"
                "The text is analysed in the zero-trust workspace and is never "
                "added to the authoritative knowledge base.")
    from core.juris_kai import tools
    return _render_tool(tools.contract_analysis, args.strip(),
                        title="Your contract")


def handle_matrix(args: str, update: Dict[str, Any], account: Dict[str, Any]) -> str:
    """Render the legal issue matrix for an issue (retrieval + Deep reasoning)."""
    if not (args or "").strip():
        return "Usage: /matrix <legal issue> [facts]"
    from core.juris_kai import tools
    return _render_tool(tools.issue_matrix, args.strip())


def handle_chronology(args: str, update: Dict[str, Any], account: Dict[str, Any]) -> str:
    """Build a litigation-ready chronology from the supplied facts."""
    if not (args or "").strip():
        return "Usage: /chronology <facts containing dates>"
    from core.juris_kai import tools
    return _render_tool(tools.legal_chronology, args.strip())


def handle_authorities(args: str, update: Dict[str, Any], account: Dict[str, Any]) -> str:
    """List the retrieved authorities for an issue (retrieval only)."""
    if not (args or "").strip():
        return "Usage: /authorities <legal issue>"
    from core.juris_kai import tools
    return _render_tool(tools.authority_bundle, [args.strip()])


def handle_statute(args: str, update: Dict[str, Any], account: Dict[str, Any]) -> str:
    """Statute research mode (filtered to enactments/instruments)."""
    if not (args or "").strip():
        return "Usage: /statute <query>"
    from core.juris_kai import tools
    return _render_tool(tools.research, args.strip(), mode="statute")


def handle_caselaw(args: str, update: Dict[str, Any], account: Dict[str, Any]) -> str:
    """Case-law research mode (honest when there is no judgment corpus)."""
    if not (args or "").strip():
        return "Usage: /caselaw <query>"
    from core.juris_kai import tools
    return _render_tool(tools.research, args.strip(), mode="case_law")


# ---- Document Analysis ----

def handle_document(args: str, update: Dict[str, Any], account: Dict[str, Any]) -> str:
    """Handle document upload and analysis (paid feature)."""
    mgr = get_account_manager()

    if not args.strip():
        from core.juris_kai import accounts as _accounts
        rate = _accounts.PER_DOCUMENT_PAGE_RATE_GHS
        return (
            "📄 *Document Analysis*\n\n"
            "Send me a legal document (PDF or text) for AI analysis.\n\n"
            f"💰 Cost: GH₵{rate:.2f} per page\n"
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


# ---- Privacy / data deletion ----

def handle_forget(account: Dict[str, Any]) -> str:
    """Delete every stored question/answer record for this account.

    The Q&A log is local-only (CT111), but the user can still ask for it to be
    erased. This is irreversible.
    """
    try:
        res = get_account_manager().forget_qa(account["account_id"])
    except Exception:
        return "⚠️ Could not clear your stored data right now. Please try again."
    return (
        f"🧹 Deleted {res.get('deleted', 0)} stored question/answer record(s) "
        "for your account.\nYour conversation history in this chat is also "
        "reset on your next question. Nothing was ever sent externally."
    )


# ---- Learning Progress ----

def handle_progress(update: Dict[str, Any], account: Dict[str, Any]) -> str:
    """Show learning progress."""
    session = get_user_session(update.get("chat_id", ""))
    topics_studied = session.get("topics_studied", [])

    if not topics_studied:
        return "No topics studied yet. Start learning with /learn <topic>"

    return f"*Topics studied*:\n" + "\n".join(f"• {t}" for t in topics_studied)


# ---- Legal groups (Telegram) ----

def handle_group(args: str, account: Dict[str, Any], is_admin: bool = False) -> str:
    """Create/join/leave groups; list a user's groups; admin member management.

    ``/group``                    list your groups
    ``/group create <name>``      create a new group (you become owner)
    ``/group join <invite_code>`` join a group
    ``/group leave <group_id>``   leave a group
    ``/group members <group_id>`` list a group's members
    ``/group audit <group_id>``   audit the group's documents
    admin-only:
    ``/group archive <group_id>``
    ``/group add <group_id> <account_id> [role]``
    ``/group remove <group_id> <account_id>``
    ``/group role <group_id> <account_id> <role>``
    """
    from core.juris_kai import groups as group_api

    parts = (args or "").strip().split()
    action = parts[0].lower() if parts else "list"
    rest = parts[1:]
    account_id = account.get("account_id", "")

    if action in ("", "list"):
        items = group_api.groups_for_account(account_id)
        if not items:
            return ("You are not in any legal groups yet.\n"
                    "Create one with /group create <name>, or join with "
                    "/group join <invite_code>.")
        lines = ["👥 *Your legal groups*"]
        for g in items:
            lines.append(f"• *{g['name']}* (`{g['group_id']}`) — "
                         f"{g.get('member_count', 0)} member(s), "
                         f"role lookup in /group members")
        return "\n".join(lines)

    if action == "create":
        name = " ".join(rest).strip()
        if not name:
            return "Usage: /group create <name>"
        res = group_api.create_group(name, account_id, kind="user",
                                     actor=account_id)
        if not res.get("success"):
            return f"⚠️ {res.get('error', 'could not create group')}"
        g = res["group"]
        return (f"✅ Created *{g['name']}*\n"
                f"Group ID: `{g['group_id']}`\n"
                f"Invite code: `{g['invite_code']}`\n"
                "Share the invite code so others can /group join it.")

    if action == "join":
        if not rest:
            return "Usage: /group join <invite_code>"
        res = group_api.join_group(rest[0], account_id, actor=account_id)
        if not res.get("success"):
            return f"⚠️ {res.get('error', 'could not join group')}"
        g = res.get("group") or {}
        if res.get("added"):
            return f"✅ Joined *{g.get('name', rest[0])}*."
        return f"You are already a member of *{g.get('name', rest[0])}*."

    if action == "leave":
        if not rest:
            return "Usage: /group leave <group_id>"
        res = group_api.leave_group(rest[0], account_id, actor=account_id)
        if not res.get("success"):
            return f"⚠️ {res.get('error', 'could not leave group')}"
        return "✅ Left the group." if res.get("removed") else "You were not a member."

    if action == "members":
        if not rest:
            return "Usage: /group members <group_id>"
        group = group_api.find_group(rest[0])
        if not group:
            return "⚠️ Group not found."
        members = get_account_manager().list_members(group["group_id"])
        lines = [f"👥 *{group['name']}* — {len(members)} member(s)"]
        for m in members:
            lines.append(f"• {m.get('full_name') or m['account_id']} "
                         f"(`{m['account_id']}`) — {m['role']}")
        return "\n".join(lines)

    if action == "audit":
        if not rest:
            return "Usage: /group audit <group_id>"
        group = group_api.find_group(rest[0])
        if not group:
            return "⚠️ Group not found."
        members = get_account_manager().list_members(group["group_id"])
        roles = {m["account_id"]: m["role"] for m in members}
        if not is_admin and roles.get(account_id) not in ("owner", "admin"):
            return "⚠️ Only a group owner or admin can request a document audit."
        res = group_api.audit_group_documents(group["group_id"],
                                              requested_by=account_id)
        if not res.get("success"):
            return f"⚠️ {res.get('error', 'audit failed')}"
        r = res.get("report") or {}
        return (f"📋 *Audit report* for {group['name']}\n"
                f"Documents: {r.get('document_count', 0)}\n"
                f"Verified: {r.get('verified_count', 0)} · "
                f"Flagged: {r.get('flagged_count', 0)}\n"
                f"Report ID: `{r.get('report_id')}`")

    # ---- admin-only management ----
    if not is_admin:
        return ("Unknown group action. Try /group, /group create <name>, "
                "/group join <code>, /group leave <group_id>, "
                "/group members <group_id>, /group audit <group_id>.")

    if action == "archive" and rest:
        res = get_account_manager().archive_group(rest[0], actor=account_id)
        return "✅ Archived." if res.get("success") else f"⚠️ {res.get('error')}"

    if action in ("add", "remove", "role") and len(rest) >= 2:
        mgr = get_account_manager()
        gid, target = rest[0], rest[1]
        if action == "add":
            role = rest[2] if len(rest) > 2 else "member"
            res = mgr.add_member(gid, target, role=role, actor=account_id)
            return ("✅ Added." if res.get("added")
                    else f"⚠️ {res.get('error') or 'already a member'}")
        if action == "remove":
            res = mgr.remove_member(gid, target, actor=account_id)
            return "✅ Removed." if res.get("removed") else "⚠️ Not a member."
        role = rest[2] if len(rest) > 2 else "member"
        res = mgr.set_member_role(gid, target, role, actor=account_id)
        return "✅ Role updated." if res.get("success") else f"⚠️ {res.get('error')}"

    return ("Usage: /group add <group_id> <account_id> [role] | "
            "/group remove <group_id> <account_id> | "
            "/group role <group_id> <account_id> <role> | "
            "/group archive <group_id>")


# ---- Institutional seats (Phase 7, Task 5) ----

def handle_seat(args: str, account: Dict[str, Any]) -> str:
    """Redeem a seat code (``/seat <code>``) or show the user's memberships."""
    mgr = get_account_manager()
    code = (args or "").strip()
    if code:
        res = mgr.redeem_seat_code(code, account["account_id"])
        if not res.get("success"):
            return (f"⚠️ {res.get('error', 'could not redeem that seat code')}.\n"
                    "Ask your institution for a valid seat code.")
        if res.get("already_member"):
            return "You're already on that institution's plan. ✅"
        return (f"✅ Seat redeemed — you're now on the *{res.get('tier')}* "
                "plan.\nYour institution's quota applies. Use /account to see "
                "your usage.")
    orgs = mgr.orgs_for_account(account["account_id"])
    if not orgs:
        return ("🎟️ *Institutional seats*\n\nYou're not on an institution "
                "plan.\nIf your school or firm gave you a seat code, redeem it "
                "with:\n  /seat <code>")
    lines = ["🎟️ *Your institution plans*"]
    for o in orgs:
        lines.append(f"• org `{o['org_account_id']}` — "
                     f"role: {o.get('role', 'member')}")
    return "\n".join(lines)


def handle_org(args: str, account: Dict[str, Any]) -> str:
    """Show an org admin their seat inventory + per-member usage."""
    mgr = get_account_manager()
    parts = (args or "").strip().split()
    action = parts[0].lower() if parts else "usage"
    org_account_id = account["account_id"]
    codes = mgr.list_seat_codes(org_account_id)
    if not codes:
        orgs = mgr.orgs_for_account(account["account_id"])
        if orgs:
            lines = ["🏛️ *Your institutions*"]
            for o in orgs:
                lines.append(f"• `{o['org_account_id']}` "
                             f"({o.get('role', 'member')})")
            return "\n".join(lines)
        return ("🏛️ *Organisation*\n\nYou don't administer an organisation.\n"
                "Institutions buy seats; users redeem them with /seat <code>.")
    if action not in ("", "usage", "seats"):
        return "Usage: /org usage | /org seats"
    usage = mgr.org_usage(org_account_id)
    lines = [f"🏛️ *Org usage* (`{org_account_id}`)",
             f"Seats: {usage['seats_used']}/{usage['seats_total']} used",
             f"Queries today (all members): {usage['usage']['queries_today']}"]
    for m in usage["members"]:
        lines.append(f"• {m.get('name') or m['account_id']} — "
                     f"{m['queries_today']} queries, "
                     f"{m['deep_research_today']} deep")
    if action == "seats":
        lines.append("")
        lines.append("*Seat codes*")
        for c in codes:
            lines.append(f"• `{c['code']}` — {c['used_count']}/{c['max_uses']} "
                         f"used ({c['status']})")
    return "\n".join(lines)


# This module must NEVER import:
#   core.build_manager, core.approval, core.deployment_manager