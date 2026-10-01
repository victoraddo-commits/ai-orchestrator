"""core.money_notify.formatter — money.* event → concise Telegram message.

Factual, short, no spam. Raw sensitive numbers (account numbers, phone
numbers, full refs) are masked to their last 4 — amounts are shown because
the recipient is the account owner. Never includes secrets. Logs never see
message bodies (logging happens at the caller with type + id only).
"""
from __future__ import annotations

TYPE_LABELS = {
    "account_discovered": "Account",
    "transaction_confirmed": "Transaction",
    "bill_detected": "Bill due",
    "bill_paid": "Bill paid",
    "debt_payment": "Debt payment",
    "debt_completed": "Debt completed",
    "income_detected": "Income",
    "anomaly": "Anomaly",
    "reconciliation_failed": "Reconciliation",
    "payday": "Payday",
    "recurring": "Recurring",
}


def _s(value) -> str:
    return str(value) if value is not None else ""


def _num(value):
    try:
        n = float(value)
        return int(n) if n == int(n) else round(n, 2)
    except (TypeError, ValueError):
        return None


def mask_ref(value) -> str:
    """Keep short values; mask anything identifier-shaped to last 4."""
    text = _s(value).strip()
    if len(text) >= 6 and any(ch.isdigit() for ch in text):
        return "••" + text[-4:]
    return text


def _pick(payload: dict, *keys):
    for key in keys:
        if payload.get(key) is not None:
            return payload.get(key)
    return None


def _buttons_bill(payload: dict) -> list:
    cid = _pick(payload, "commitment_id", "bill_id")
    oid = _pick(payload, "occurrence_id", "oid")
    if cid is None:
        return []
    txid = _pick(payload, "transaction_id", "tx_id") or 0
    return [[{"text": "Mark Paid",
              "callback_data": f"cmtpaid:{cid}:{oid or 0}:{txid}"}]]


def _buttons_inbox(payload: dict) -> list:
    inbox_id = _pick(payload, "inbox_id", "id", "event_id")
    if inbox_id is None:
        return []
    return [[
        {"text": "Confirm", "callback_data": f"inbox:{inbox_id}:confirm"},
        {"text": "Reject", "callback_data": f"inbox:{inbox_id}:reject"},
        {"text": "Match", "callback_data": f"inbox:{inbox_id}:match"},
    ], [
        {"text": "Ignore", "callback_data": f"inbox:{inbox_id}:ignore"},
        {"text": "Investigate", "callback_data": f"inbox:{inbox_id}:investigate"},
    ]]


def format_event(type_name: str, payload: dict) -> dict:
    """Return {"text": ..., "buttons": [[{text, callback_data}], ...]}."""
    payload = payload or {}
    amount = _num(_pick(payload, "amount", "total", "delta"))
    amount_txt = f" — {_s(_pick(payload, 'currency') or 'GHS')} {amount}" if amount is not None else ""
    name = _s(_pick(payload, "name", "label", "memo", "merchant", "title"))
    due = _s(_pick(payload, "due_date", "due", "date", "as_of"))
    ref = mask_ref(_pick(payload, "ref", "reference", "account_number") or "")

    if type_name == "bill_detected":
        text = f"🗓 Bill due: {name or 'commitment'}{amount_txt}"
        if due:
            text += f" on {due}"
        return {"text": text, "buttons": _buttons_bill(payload)}

    if type_name == "bill_paid":
        text = f"✅ Bill paid: {name or 'commitment'}{amount_txt}"
        return {"text": text, "buttons": []}

    if type_name == "account_discovered":
        kind = _s(_pick(payload, "kind"))
        status = _s(_pick(payload, "status"))
        text = f"🏦 Account {status or 'detected'}: {name or 'new account'}"
        if kind:
            text += f" ({kind})"
        if ref:
            text += f" {ref}"
        return {"text": text, "buttons": []}

    if type_name == "transaction_confirmed":
        direction = _s(_pick(payload, "direction"))
        arrow = "in ⬅️" if direction == "in" else ("out ➡️" if direction == "out" else "")
        text = f"🔁 Transaction {arrow}: {name or 'entry'}{amount_txt}"
        return {"text": text, "buttons": []}

    if type_name == "debt_payment":
        remaining = _num(_pick(payload, "remaining_balance", "balance", "remaining"))
        text = f"💸 Debt payment: {name or 'debt'}{amount_txt}"
        if remaining is not None:
            text += f" (remaining {remaining})"
        return {"text": text, "buttons": []}

    if type_name == "debt_completed":
        return {"text": f"🎉 Debt completed: {name or 'debt'} — cleared",
                "buttons": []}

    if type_name == "income_detected":
        source = _s(_pick(payload, "source_name", "source", "employer"))
        text = f"💰 Income: {source or name or 'income'}{amount_txt}"
        return {"text": text, "buttons": []}

    if type_name == "anomaly":
        severity = _s(_pick(payload, "severity", "level")) or "info"
        kind = _s(_pick(payload, "kind", "anomaly_kind", "type"))
        text = f"⚠️ Anomaly ({severity}): {kind or 'unspecified'}"
        if name:
            text += f" — {name}"
        return {"text": text,
                "buttons": [[{"text": "Investigate",
                              "callback_data": "dash:open"}]]}

    if type_name == "reconciliation_failed":
        text = f"🧾 Reconciliation failed: {name or 'account'}{amount_txt}"
        return {"text": text,
                "buttons": [[{"text": "Investigate",
                              "callback_data": "dash:open"}]]}

    if type_name == "payday":
        text = f"📅 Payday"
        when = _s(_pick(payload, "next_expected_at", "date", "pay_date"))
        if when:
            text += f" on {when}"
        if name:
            text += f" — {name}"
        return {"text": text, "buttons": []}

    # unknown §45 type (defensive; classify() gates this today)
    return {"text": f"{TYPE_LABELS.get(type_name, 'Money')}: {name}{amount_txt}",
            "buttons": []}


DIGEST_HEADERS = {
    "daily": "📋 Daily money digest",
    "weekly": "📊 Weekly money rollup",
    "monthly": "🗓 Monthly money rollup",
}


def format_digest(kind: str, items: list) -> str:
    """Rollup text for a digest flush. items: [{type, topic, payload, ts}]."""
    lines = [DIGEST_HEADERS.get(kind, "📋 Money digest"), ""]
    if not items:
        lines.append("No money events during this period. All quiet.")
        return "\n".join(lines)
    counts: dict = {}
    for item in items:
        counts[item.get("type", "other")] = counts.get(item.get("type", "other"), 0) + 1
    summary = " · ".join(f"{TYPE_LABELS.get(k, k)} ×{v}"
                         for k, v in sorted(counts.items()))
    lines.append(summary)
    lines.append("")
    for item in items[:20]:
        ev = format_event(item.get("type", ""), item.get("payload") or {})
        first = ev["text"].splitlines()[0]
        lines.append(f"• {first}")
    if len(items) > 20:
        lines.append(f"… and {len(items) - 20} more")
    return "\n".join(lines)
