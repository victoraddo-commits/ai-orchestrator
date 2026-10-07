"""core.money_telegram.handlers — akush233-bot inbound handlers (§44).

Allowlist-only inbound (the operator's chat). Every data fetch/action goes
through akush-core's own API with the ``bot`` service token — same endpoints
the PWA uses, no separate truth. NL queries answer ONLY from the query
results and always show the payload basis as a footer line.

Nothing here logs message text, amounts, or tokens.
"""
from __future__ import annotations

import logging

from core.telegram import registry as reg
from core.telegram import transport

from . import menu
from .client import get_client
from .token import BOT_ID

logger = logging.getLogger("kai.money_telegram")

CURRENCY = "GHS"

SECTION_EMPTY = "Nothing to show right now."


def _fmt_amount(value) -> str:
    try:
        n = float(value)
        n = int(n) if n == int(n) else round(n, 2)
        return f"{CURRENCY} {n}"
    except (TypeError, ValueError):
        return ""


def _allowed(chat_id, prefs: dict) -> bool:
    from core.money_notify import prefs as prefs_mod
    chats = prefs_mod.allowed_chats(prefs)
    return str(chat_id) in chats


def _deliver(chat_id, text: str, buttons=None) -> dict:
    bot = reg.get(BOT_ID)
    if bot is None:
        return {"ok": False, "error": "unknown bot"}
    reply_markup = {"inline_keyboard": buttons} if buttons else None
    return transport.send_message(bot, chat_id, text, reply_markup=reply_markup)


# ---------------------------------------------------------------------------
# section fetchers — all reads go through akush-core
# ---------------------------------------------------------------------------
def _section_money(client) -> tuple[str, list]:
    parts, buttons = [], []
    st, nw = client.get("/net-worth")
    if st == 200 and isinstance(nw, dict):
        parts.append(f"Net worth: {nw.get('net_worth', '—')}")
    else:
        parts.append("Net worth: unavailable right now")
    st, s2s = client.get("/forecasts/safe-to-spend")
    if st == 200 and isinstance(s2s, dict):
        val = s2s.get("safe_to_spend", s2s.get("per_period"))
        label = s2s.get("assumptions_note") or "per current commitments"
        parts.append(f"Safe to spend: {val if val is not None else '—'} ({label})")
    return "\n".join(parts) or SECTION_EMPTY, buttons


def _section_bills(client) -> tuple[str, list]:
    lines, buttons = [], []
    st, due = client.get("/commitments/due-today")
    if st == 200:
        for row in (due.get("data") or [])[:10]:
            amt = _fmt_amount(row.get("amount"))
            lines.append(f"• Due today: {row.get('name')} {amt}")
            buttons.append([{"text": f"Mark paid: {row.get('name')}",
                             "callback_data":
                             f"cmtpaid:{row.get('commitment_id')}:{row.get('occurrence_id')}:0"}])
    st, over = client.get("/commitments/overdue")
    if st == 200:
        for row in (over.get("data") or [])[:10]:
            lines.append(f"• OVERDUE: {row.get('name')} {_fmt_amount(row.get('amount'))}")
            buttons.append([{"text": f"Mark paid: {row.get('name')}",
                             "callback_data":
                             f"cmtpaid:{row.get('commitment_id')}:{row.get('occurrence_id')}:0"}])
    return ("\n".join(lines) if lines else "No bills due or overdue."), buttons


def _section_debts(client) -> tuple[str, list]:
    st, data = client.get("/debts")
    if st != 200:
        return "Debts: unavailable right now", []
    rows = data.get("data") or []
    if not rows:
        return "No debts tracked.", []
    lines = [f"• {r.get('name')} — remaining {_fmt_amount(r.get('remaining_balance', r.get('principal')))}"
             for r in rows[:10]]
    return "\n".join(lines), []


def _section_payday(client) -> tuple[str, list]:
    st, data = client.get("/paydays")
    if st != 200:
        return "Payday: unavailable right now", []
    rows = data.get("data") or []
    if not rows:
        return "No payday schedule configured yet.", []
    lines = [f"• {r.get('name')}: next {r.get('next_expected_at') or 'unknown'}"
             for r in rows[:8]]
    return "\n".join(lines), []


def _section_planning(client) -> tuple[str, list]:
    parts = []
    st, pay = client.get("/paydays")
    if st == 200 and (pay.get("data") or []):
        parts.append("Paydays: " + ", ".join(r.get("name", "?") for r in pay["data"][:5]))
    st, bud = client.get("/budgets")
    if st == 200 and (bud.get("data") or []):
        parts.append("Budgets: " + ", ".join(r.get("name", "?") for r in bud["data"][:5]))
    return ("\n".join(parts) if parts
            else "No planning data yet — add paydays or budgets in the dashboard.")


def _section_utilities(client) -> tuple[str, list]:
    st, data = client.get("/utilities/summary", {"period": "month"})
    if st != 200 or not isinstance(data, dict):
        return "Utilities summary: unavailable right now", []
    parts = []
    for key in ("money_in", "money_out", "net"):
        if data.get(key) is not None:
            parts.append(f"{key.replace('_', ' ')}: {_fmt_amount(data.get(key))}")
    return ("\n".join(parts) if parts
            else "No money movement recorded for this period.")


def _section_goals(client) -> tuple[str, list]:
    lines = []
    st, data = client.get("/savings-goals")
    if st == 200:
        for r in (data.get("data") or [])[:8]:
            lines.append(f"• {r.get('name')}: {_fmt_amount(r.get('target_amount'))}"
                         f" (saved {_fmt_amount(r.get('saved_amount', r.get('saved', 0)))})")
    st, sf = client.get("/sinking-funds")
    if st == 200:
        for r in (sf.get("data") or [])[:8]:
            lines.append(f"• [sinking] {r.get('name')}: {_fmt_amount(r.get('target_amount'))}")
    return ("\n".join(lines) if lines else "No goals or sinking funds yet."), []


def _section_reports(client) -> tuple[str, list]:
    st, data = client.get("/reports/financial-months")
    if st == 200 and isinstance(data, dict):
        rows = data.get("data") or []
        if rows:
            return "\n".join(f"• {r.get('month', r.get('ym'))}" for r in rows[:8]), []
    return ("Financial-month reports are not available yet — ask a question "
            "instead (e.g. \"how much did I spend in September?\")."), []


def _section_accounts(client) -> tuple[str, list]:
    st, data = client.get("/accounts")
    if st != 200:
        return "Accounts: unavailable right now", []
    rows = data.get("data") or []
    if not rows:
        return "No accounts yet.", []
    lines = [f"• {r.get('name')} ({r.get('kind')}, {r.get('status')})"
             f" ledger {_fmt_amount(r.get('ledger_balance'))}" for r in rows[:12]]
    return "\n".join(lines), []


def _section_inbox(client) -> tuple[str, list]:
    st, data = client.get("/financial-inbox", {"status": "pending", "limit": 8})
    if st != 200:
        return "Inbox: unavailable right now", []
    rows = data.get("data") or []
    if not rows:
        return "Inbox is empty — nothing needs your decision.", []
    lines, buttons = [], []
    for r in rows:
        conf = r.get("confidence")
        conf_txt = f", confidence {conf}" if conf is not None else ""
        lines.append(f"• #{r.get('id')} {r.get('event_kind')}{conf_txt}")
        iid = r.get("id")
        buttons.append([
            {"text": "Confirm", "callback_data": f"inbox:{iid}:confirm"},
            {"text": "Reject", "callback_data": f"inbox:{iid}:reject"},
            {"text": "Match", "callback_data": f"inbox:{iid}:match"},
        ])
        buttons.append([
            {"text": "Ignore", "callback_data": f"inbox:{iid}:ignore"},
            {"text": "Investigate", "callback_data": f"inbox:{iid}:investigate"},
        ])
    return "\n".join(lines), buttons


SECTIONS = {
    "money": _section_money,
    "planning": _section_planning,
    "bills": _section_bills,
    "debts": _section_debts,
    "payday": _section_payday,
    "utilities": _section_utilities,
    "goals": _section_goals,
    "reports": _section_reports,
    "accounts": _section_accounts,
    "inbox": _section_inbox,
}


# ---------------------------------------------------------------------------
# NL ask — answers ONLY from query results; basis shown as footer (§12/§44)
# ---------------------------------------------------------------------------
def _footer(basis, intent: str) -> str:
    if not basis:
        return "basis: none — answered honestly as unsupported (no ledger basis)"
    source = basis.get("source", "")
    rows = basis.get("rows")
    rows_txt = f"{rows} row(s)" if isinstance(rows, int) else "query results"
    return f"basis: {intent} · {source} · {rows_txt} · deterministic rules"


def _ask(client, question: str) -> tuple[str, list]:
    st, data = client.ask(question)
    if st != 200 or not isinstance(data, dict):
        return ("I couldn't reach your ledger just now — try again shortly."), []
    answer = data.get("answer") or {}
    intent = data.get("intent", "?")
    text = answer.get("text") or "No answer available."
    for sugg in (answer.get("suggestions") or [])[:3]:
        text += f"\n• {sugg}"
    text += f"\n\n{_footer(data.get('basis'), intent)}"
    return text, []


# ---------------------------------------------------------------------------
# callback actions (same endpoints the PWA uses — no separate truth)
# ---------------------------------------------------------------------------
def _act_inbox(client, inbox_id, action) -> tuple[str, list]:
    st, data = client.post(f"/financial-inbox/{inbox_id}/act", {"action": action})
    if st in (200, 201, 202):
        return f"Inbox #{inbox_id}: {action} ✓", []
    msg = (data or {}).get("message") if isinstance(data, dict) else None
    return f"Inbox #{inbox_id}: {action} failed ({st}){': ' + msg if msg else ''}", []


def _mark_paid(client, cid, oid, txid) -> tuple[str, list]:
    if not oid or not txid or str(txid) == "0":
        return ("Marking paid needs the matching payment transaction — open "
                "the Dashboard → Bills to link it."), []
    st, data = client.post(f"/commitments/{cid}/occurrences/{oid}/mark-paid",
                           {"transaction_id": int(txid)})
    if st == 200:
        return "Marked paid ✓", []
    msg = (data or {}).get("message") if isinstance(data, dict) else None
    return f"Mark-paid failed ({st}){': ' + msg if msg else ''}", []


# ---------------------------------------------------------------------------
# update routing
# ---------------------------------------------------------------------------
def handle_update(update: dict, *, client=None, prefs: dict | None = None,
                  send=None) -> dict:
    """Route one Telegram update. Returns a summary dict (never raises).

    ``send`` is a test seam: callable(chat_id, text, buttons) -> dict.
    """
    send = send or _deliver
    client = client or get_client()
    from core.money_notify import prefs as prefs_mod
    prefs = prefs or prefs_mod.load_prefs()

    if not isinstance(update, dict):
        return {"handled": False, "reason": "malformed update"}
    callback = update.get("callback_query")
    message = update.get("message") or {}
    chat_id = (callback or {}).get("message", {}).get("chat", {}).get("id") \
        or message.get("chat", {}).get("id")
    if chat_id is None:
        return {"handled": False, "reason": "no chat"}

    if not _allowed(chat_id, prefs):
        # silent rejection — never reveal that the bot exists to strangers
        logger.info("money bot inbound rejected chat (not allowlisted) chat_id=%s", chat_id)
        return {"handled": False, "reason": "chat not allowlisted"}

    if callback:
        data = callback.get("data") or ""
        reply_to = chat_id
        text, buttons = _route_callback(client, data, prefs)
        send(reply_to, text, buttons)
        return {"handled": True, "kind": "callback", "data": data}

    text = (message.get("text") or "").strip()
    if not text:
        return {"handled": False, "reason": "no text"}

    if text.startswith("/start") or text.startswith("/help"):
        send(chat_id, menu.WELCOME, menu.build_main_menu()["inline_keyboard"])
        return {"handled": True, "kind": "menu"}

    if text.startswith("menu:") or text in {label for label, _ in menu.MENU_ITEMS}:
        key = text.split(":", 1)[1] if ":" in text else next(
            k for l, k in menu.MENU_ITEMS if l == text)
        if key == "dashboard":
            send(chat_id,
                 f"Dashboard: {menu.DASHBOARD_URL}\n(Opens the Akush PWA — "
                 "same ledger, richer views.)")
            return {"handled": True, "kind": "dashboard"}
        if key == "settings":
            from core.money_notify import prefs as _pm
            p = _pm.load_prefs()
            kb = menu.build_settings_menu(p.get("modes") or {},
                                          _pm.ALWAYS_ON)
            send(chat_id,
                 "Settings — tap a type to cycle off → digest → instant.\n"
                 f"Daily digest at {p.get('digest_hour', 8)}:00; quiet hours "
                 f"{(p.get('quiet_hours') or {}).get('start')}–"
                 f"{(p.get('quiet_hours') or {}).get('end')}.", kb["inline_keyboard"])
            return {"handled": True, "kind": "settings"}
        if key == "start":
            send(chat_id, menu.WELCOME, menu.build_main_menu()["inline_keyboard"])
            return {"handled": True, "kind": "menu"}
        fetcher = SECTIONS.get(key)
        if fetcher is None:
            send(chat_id, menu.WELCOME, menu.build_main_menu()["inline_keyboard"])
            return {"handled": True, "kind": "menu"}
        body, buttons = fetcher(client)
        send(chat_id, body, buttons or None)
        return {"handled": True, "kind": "section", "section": key}

    body, buttons = _ask(client, text)
    send(chat_id, body, buttons or None)
    return {"handled": True, "kind": "ask"}


def _route_callback(client, data: str, prefs: dict) -> tuple[str, list]:
    parts = (data or "").split(":")
    kind = parts[0] if parts else ""
    if kind == "menu":
        key = parts[1] if len(parts) > 1 else "start"
        if key == "dashboard":
            return (f"Dashboard: {menu.DASHBOARD_URL}"), []
        if key == "settings":
            from core.money_notify import prefs as _pm
            p = _pm.load_prefs()
            kb = menu.build_settings_menu(p.get("modes") or {}, _pm.ALWAYS_ON)
            return ("Settings — tap a type to cycle off → digest → instant.",
                    kb["inline_keyboard"])
        if key == "start":
            return menu.WELCOME, menu.build_main_menu()["inline_keyboard"]
        fetcher = SECTIONS.get(key)
        if fetcher is None:
            return menu.WELCOME, menu.build_main_menu()["inline_keyboard"]
        body, buttons = fetcher(client)
        return body, buttons
    if kind == "dash":
        return f"Dashboard: {menu.DASHBOARD_URL}", []
    if kind == "inbox" and len(parts) >= 3:
        inbox_id, action = parts[1], parts[2]
        return _act_inbox(client, inbox_id, action)
    if kind == "cmtpaid" and len(parts) >= 4:
        return _mark_paid(client, parts[1], parts[2], parts[3])
    if kind == "set" and len(parts) >= 3:
        type_name, mode = parts[1], parts[2]
        return _set_mode(type_name, mode, prefs)
    return "Unrecognized action.", []


def _set_mode(type_name: str, mode: str, prefs: dict) -> tuple[str, list]:
    from core.money_notify import prefs as _pm
    if type_name in _pm.ALWAYS_ON:
        return "This type is always on (security).", []
    if mode not in ("off", "digest", "instant"):
        return "Unknown mode.", []
    prefs["modes"][type_name] = mode
    _pm.save_prefs(prefs)
    return f"{type_name}: set to {mode}.", []
