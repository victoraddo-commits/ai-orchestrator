"""core.money_telegram.menu — §44 main menu + settings keyboards."""
from __future__ import annotations

import os

# Deep-link to the Akush PWA (tailscale serve path, no funnel). Placeholder
# until the operator confirms the final host — override with env.
DASHBOARD_URL = os.environ.get(
    "AKUSH_PWA_URL", "https://proxmox-b.tail82a9ca.ts.net/akush/")

# §44 main menu, in order
MENU_ITEMS: list[tuple[str, str]] = [
    ("Money", "money"),
    ("Planning", "planning"),
    ("Bills", "bills"),
    ("Debts", "debts"),
    ("Payday", "payday"),
    ("Utilities", "utilities"),
    ("Goals", "goals"),
    ("Reports", "reports"),
    ("Accounts", "accounts"),
    ("Inbox", "inbox"),
    ("Open Dashboard", "dashboard"),
    ("Settings", "settings"),
]

MENU_KEYS = [key for _, key in MENU_ITEMS]

WELCOME = (
    "Akush Money — your money, one chat.\n"
    "Pick a section below, or just ask a question in plain English "
    "(e.g. \"how much did I spend on transport this month?\").\n"
    "Answers come only from your ledger (akush-core); every answer shows "
    "the query basis it used."
)


def build_main_menu() -> dict:
    rows, row = [], []
    for label, key in MENU_ITEMS:
        row.append({"text": label, "callback_data": f"menu:{key}"})
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return {"inline_keyboard": rows}


def build_settings_menu(modes: dict, always_on: set) -> dict:
    """Per-type toggle buttons (§45). Always-on types are shown locked."""
    rows = []
    for type_name in sorted(set(modes) | always_on):
        if type_name in always_on:
            rows.append([{"text": f"🔒 {type_name}: always on",
                          "callback_data": "set:locked"}])
            continue
        current = modes.get(type_name, "digest")
        nxt = {"off": "digest", "digest": "instant", "instant": "off"}[current]
        rows.append([{"text": f"{type_name}: {current} → tap for {nxt}",
                      "callback_data": f"set:{type_name}:{nxt}"}])
    rows.append([{"text": "« Back", "callback_data": "menu:start"}])
    return {"inline_keyboard": rows}
