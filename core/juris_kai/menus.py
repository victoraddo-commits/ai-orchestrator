"""Telegram menu definitions for Juris Kai.

Provides reply keyboards and inline keyboards for main menu, sub-menus,
admin menu, and settings.  All menus are isolated from operational Kai code.
"""

import json


# ---------------------------------------------------------------------------
# Reply Keyboards (native Telegram menus — replace text input bar)
# ---------------------------------------------------------------------------

def _keyboard(rows: list[list[str]], resize: bool = True) -> str:
    """Build a JSON-serialized ReplyKeyboardMarkup."""
    return json.dumps({
        "keyboard": [[{"text": btn} for btn in row] for row in rows],
        "resize_keyboard": resize,
    })


def _remove_keyboard() -> str:
    """Remove the reply keyboard (for free-text input)."""
    return json.dumps({"remove_keyboard": True})


# ---------------------------------------------------------------------------
# Main Menu
# ---------------------------------------------------------------------------

def main_menu() -> str:
    """Primary navigation menu shown after /start or menu command."""
    return _keyboard([
        ["📚 Learn Law", "⚖️ Cases"],
        ["📝 Practice", "🧠 Study Tools"],
        ["📄 Documents", "🎓 Progress"],
        ["⚙️ Settings", "❓ Help"],
    ])


# ---------------------------------------------------------------------------
# Learn Law Sub-menu
# ---------------------------------------------------------------------------

def learn_menu() -> str:
    """Sub-menu for legal learning topics."""
    return _keyboard([
        ["🇬🇭 Ghana Constitution", "⚖️ Criminal Law"],
        ["🏛️ Civil Law", "📋 Contract Law"],
        ["🏠 Property Law", "👨‍👩‍👧 Family Law"],
        ["💼 Business Law", "🔍 Search Topic"],
        ["🔙 Back to Menu"],
    ])


# ---------------------------------------------------------------------------
# Case Law Sub-menu
# ---------------------------------------------------------------------------

def case_law_menu() -> str:
    """Sub-menu for case law library."""
    return _keyboard([
        ["📋 Case Summaries", "⚡ Legal Principles"],
        ["📜 Precedents", "🔎 Case Analysis"],
        ["📂 Source References", "🔍 Search Case"],
        ["🔙 Back to Menu"],
    ])


# ---------------------------------------------------------------------------
# Practice & Exams Sub-menu
# ---------------------------------------------------------------------------

def practice_menu() -> str:
    """Sub-menu for exam practice and IRAC."""
    return _keyboard([
        ["📝 Generate Questions", "⚖️ IRAC Practice"],
        ["✍️ Essay Practice", "📋 Mock Exams"],
        ["✅ Answer Evaluation", "🔙 Back to Menu"],
    ])


# ---------------------------------------------------------------------------
# Study Tools Sub-menu
# ---------------------------------------------------------------------------

def study_tools_menu() -> str:
    """Sub-menu for flashcards, quizzes, and study aids."""
    return _keyboard([
        ["🃏 Flashcards", "🧠 Memory Drills"],
        ["⏱️ Quick Quiz", "📝 Revision Notes"],
        ["🔙 Back to Menu"],
    ])


# ---------------------------------------------------------------------------
# Documents Sub-menu
# ---------------------------------------------------------------------------

def documents_menu() -> str:
    """Sub-menu for document analysis."""
    return _keyboard([
        ["📤 Upload Document", "📋 Summarize"],
        ["⚖️ Legal Concepts from Doc", "📌 Key Points"],
        ["📂 Recent Documents", "🔙 Back to Menu"],
    ])


# ---------------------------------------------------------------------------
# Progress Sub-menu
# ---------------------------------------------------------------------------

def progress_menu() -> str:
    """Sub-menu for study progress and history."""
    return _keyboard([
        ["📊 Learning History", "✅ Completed Topics"],
        ["🎯 Weak Areas", "🗺️ Study Path"],
        ["📈 Stats", "🔙 Back to Menu"],
    ])


# ---------------------------------------------------------------------------
# Settings Sub-menu
# ---------------------------------------------------------------------------

def settings_menu(account: dict = None) -> str:
    """Sub-menu for user settings and account info."""
    return _keyboard([
        ["🌐 Language", "📊 Learning Level"],
        ["🔔 Notifications", "👤 Account Info"],
        ["💳 Subscription", "🔙 Back to Menu"],
    ])


# ---------------------------------------------------------------------------
# Admin Menu (gated by JURIS_KAI_ADMIN_IDS)
# ---------------------------------------------------------------------------

def admin_main_menu() -> str:
    """Admin-only main menu."""
    return _keyboard([
        ["🔧 Bot Health", "👥 User Activity"],
        ["📚 Knowledge Mgmt", "🤖 AI Mgmt"],
        ["🔐 Security", "📊 Stats Dashboard"],
        ["🔙 User Menu"],
    ])


def admin_bot_health_menu() -> str:
    return _keyboard([
        ["📊 System Status", "⚠️ Error Logs"],
        ["🔌 API Status", "🤖 Provider Status"],
        ["💰 Cost Monitor", "🔙 Admin Menu"],
    ])


def admin_knowledge_menu() -> str:
    return _keyboard([
        ["✅ Approved Sources", "➕ Add Document"],
        ["🔍 Verify Source", "📚 Update Database"],
        ["📋 Source Versions", "🔙 Admin Menu"],
    ])


def admin_ai_menu() -> str:
    return _keyboard([
        ["🔄 Model Routing", "📊 Provider Status"],
        ["📈 Token Usage", "⚡ Performance"],
        ["⚠️ Failover Control", "🔙 Admin Menu"],
    ])


def admin_security_menu() -> str:
    return _keyboard([
        ["👥 Permissions", "📊 Sessions"],
        ["🚨 Suspicious Activity", "📋 Access Logs"],
        ["🔙 Admin Menu"],
    ])


# ---------------------------------------------------------------------------
# Inline Keyboards (for interaction within messages)
# ---------------------------------------------------------------------------

def inline_button(text: str, callback_data: str) -> dict:
    """Single inline keyboard button."""
    return {"text": text, "callback_data": callback_data}


def inline_row(*buttons: tuple[str, str]) -> list[dict]:
    """Row of inline buttons from (text, callback_data) pairs."""
    return [inline_button(t, cb) for t, cb in buttons]


def disclaimer_accept_keyboard() -> str:
    """Inline keyboard for disclaimer acceptance."""
    return json.dumps({
        "inline_keyboard": [
            inline_row(("✅ I Understand", "disclaimer_accept")),
        ]
    })


def confirm_cancel_keyboard(action: str = "confirm") -> str:
    """Generic confirm/cancel inline keyboard."""
    return json.dumps({
        "inline_keyboard": [
            inline_row(
                ("✅ Confirm", f"{action}_confirm"),
                ("❌ Cancel", f"{action}_cancel"),
            )
        ]
    })


def quiz_answer_keyboard(options: list[str]) -> str:
    """Inline keyboard for quiz answers (A/B/C/D)."""
    labels = ["A", "B", "C", "D"]
    rows = []
    for i in range(0, min(len(options), 4), 2):
        row = []
        for j in range(i, min(i + 2, len(options))):
            row.append(inline_button(f"{labels[j]}: {options[j][:40]}", f"quiz_{labels[j]}"))
        rows.append(row)
    return json.dumps({"inline_keyboard": rows})


# ---------------------------------------------------------------------------
# Menu routing
# ---------------------------------------------------------------------------

def menu_for_text(text: str, is_admin: bool = False) -> str | None:
    """Return the keyboard for a menu text label, or None if not a menu item."""
    normalized = text.strip()

    menu_map = {
        "📚 Learn Law": learn_menu,
        "⚖️ Cases": case_law_menu,
        "📝 Practice": practice_menu,
        "🧠 Study Tools": study_tools_menu,
        "📄 Documents": documents_menu,
        "🎓 Progress": progress_menu,
        "⚙️ Settings": settings_menu,
        "❓ Help": None,  # handled specially — shows help text + main menu
        "🔙 Back to Menu": main_menu,
        "🔙 User Menu": main_menu,
    }

    admin_menu_map = {
        "🔧 Bot Health": admin_bot_health_menu,
        "👥 User Activity": None,  # handled by bot logic
        "📚 Knowledge Mgmt": admin_knowledge_menu,
        "🤖 AI Mgmt": admin_ai_menu,
        "🔐 Security": admin_security_menu,
        "📊 Stats Dashboard": None,  # handled by bot logic
        "🔙 Admin Menu": admin_main_menu,
    }

    # Sub-menu back buttons
    sub_backs = {
        "🇬🇭 Ghana Constitution": None,
        "⚖️ Criminal Law": None,
        "🏛️ Civil Law": None,
        "📋 Contract Law": None,
        "🏠 Property Law": None,
        "👨‍👩‍👧 Family Law": None,
        "💼 Business Law": None,
        "🔍 Search Topic": None,
        "📋 Case Summaries": None,
        "⚡ Legal Principles": None,
        "📜 Precedents": None,
        "🔎 Case Analysis": None,
        "📂 Source References": None,
        "🔍 Search Case": None,
        "📝 Generate Questions": None,
        "⚖️ IRAC Practice": None,
        "✍️ Essay Practice": None,
        "📋 Mock Exams": None,
        "✅ Answer Evaluation": None,
        "🃏 Flashcards": None,
        "🧠 Memory Drills": None,
        "⏱️ Quick Quiz": None,
        "📝 Revision Notes": None,
        "📤 Upload Document": None,
        "📋 Summarize": None,
        "⚖️ Legal Concepts from Doc": None,
        "📌 Key Points": None,
        "📂 Recent Documents": None,
        "📊 Learning History": None,
        "✅ Completed Topics": None,
        "🎯 Weak Areas": None,
        "🗺️ Study Path": None,
        "📈 Stats": None,
        "🌐 Language": None,
        "📊 Learning Level": None,
        "🔔 Notifications": None,
        "👤 Account Info": None,
        "💳 Subscription": None,
        # Admin sub-menu items
        "📊 System Status": None,
        "⚠️ Error Logs": None,
        "🔌 API Status": None,
        "🤖 Provider Status": None,
        "💰 Cost Monitor": None,
        "✅ Approved Sources": None,
        "➕ Add Document": None,
        "🔍 Verify Source": None,
        "📚 Update Database": None,
        "📋 Source Versions": None,
        "🔄 Model Routing": None,
        "📈 Token Usage": None,
        "⚡ Performance": None,
        "⚠️ Failover Control": None,
        "👥 Permissions": None,
        "📊 Sessions": None,
        "🚨 Suspicious Activity": None,
        "📋 Access Logs": None,
    }

    if is_admin and normalized in admin_menu_map:
        fn = admin_menu_map[normalized]
        return fn() if fn else None
    if is_admin and normalized in sub_backs:
        return None  # handled by bot logic

    if normalized in menu_map:
        fn = menu_map[normalized]
        return fn() if fn else None
    if normalized in sub_backs:
        return None  # handled by bot logic

    return None


# ---------------------------------------------------------------------------
# Free-text mode (remove keyboard)
# ---------------------------------------------------------------------------

def free_text_keyboard() -> str:
    """Remove keyboard for free-text legal queries."""
    return _remove_keyboard()
