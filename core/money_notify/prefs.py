"""core.money_notify.prefs — notification preferences (directive §45).

Storage: ``memory/money_notify_prefs.json`` (operator-editable, contains NO
secrets — same plain-JSON-in-memory/ conventions as every other Kai store).
Runtime state (digest buffer, rate counters, dedupe cache) lives in
``memory/money_notify_state.json`` and survives restarts.

Preference model:
  * per-type mode: ``instant`` | ``digest`` | ``off``
  * quiet hours (local time): non-security instant messages are held into the
    daily digest while quiet
  * daily digest at ``digest_hour``; weekly rollup on Mondays, monthly on
    the 1st (§45 digest rollups)
  * rate limit: max N instant messages/hour
  * dedupe window: identical (type, key) events suppressed for N seconds

Security/anomaly notifications are ALWAYS on (forced instant, never quiet-
held, never rate-limited to silence) — operator protection.
"""
from __future__ import annotations

import fnmatch
import json
import os
import threading
from datetime import datetime
from pathlib import Path

PREFS_PATH = Path(os.environ.get(
    "MONEY_NOTIFY_PREFS_PATH",
    "/opt/ai-orchestrator/memory/money_notify_prefs.json"))
STATE_PATH = Path(os.environ.get(
    "MONEY_NOTIFY_STATE_PATH",
    "/opt/ai-orchestrator/memory/money_notify_state.json"))

# Topic families akush-core / bridge publish (architecture doc §5.1 + the
# Phase-6 topic names). Both spellings map to the same §45 type.
TYPE_PATTERNS: dict[str, tuple] = {
    "account_discovered": ("money.account.discovered", "money.account.detected",
                           "money.account.confirmed"),
    "transaction_confirmed": ("money.transaction.confirmed", "money.tx.confirmed",
                              "money.tx.created", "money.tx.adjusted"),
    "bill_detected": ("money.bill.detected", "money.bill.due",
                      "money.bill.overdue", "money.commitment.due"),
    "bill_paid": ("money.bill.paid", "money.commitment.paid"),
    "debt_payment": ("money.debt.payment",),
    "debt_completed": ("money.debt.completed",),
    "income_detected": ("money.income.detected", "money.income.missing"),
    "anomaly": ("money.anomaly.detected",),
    "reconciliation_failed": ("money.reconciliation.failed",
                              "money.reconciliation.open",
                              "money.sms.bridge_failed"),
    "recurring": ("money.recurring.*",),
    "payday": ("money.payday.*",),
}

ALWAYS_ON = frozenset({"anomaly", "reconciliation_failed"})

# Defaults are digest-friendly (no spam): routine confirmations roll into the
# daily digest; human-meaningful events (bill due, payday, debt completed)
# are instant; security/anomaly always instant.
DEFAULT_PREFS: dict = {
    "chat_id": None,
    "allowed_chats": [],
    "quiet_hours": {"start": "22:00", "end": "07:00"},
    "digest_hour": 8,
    "rate_limit_per_hour": 20,
    "dedupe_window_sec": 600,
    "modes": {
        "account_discovered": "digest",
        "transaction_confirmed": "digest",
        "bill_detected": "instant",
        "bill_paid": "digest",
        "debt_payment": "digest",
        "debt_completed": "instant",
        "income_detected": "digest",
        "payday": "instant",
        "anomaly": "instant",
        "reconciliation_failed": "instant",
        "recurring": "digest",
    },
}

_prefs_lock = threading.Lock()
_prefs: dict | None = None

_state_lock = threading.Lock()
_state: dict | None = None


def load_prefs() -> dict:
    """Load prefs, deep-merged over defaults. Cheap cache; never raises."""
    global _prefs
    with _prefs_lock:
        if _prefs is not None:
            return _prefs
        merged = json.loads(json.dumps(DEFAULT_PREFS))
        try:
            stored = json.loads(PREFS_PATH.read_text() or "{}")
        except Exception:
            stored = {}
        if isinstance(stored, dict):
            for key, val in stored.items():
                if key == "modes" and isinstance(val, dict):
                    merged["modes"].update(val)
                else:
                    merged[key] = val
        _prefs = merged
        return _prefs


def save_prefs(prefs: dict) -> None:
    global _prefs
    with _prefs_lock:
        try:
            PREFS_PATH.parent.mkdir(parents=True, exist_ok=True)
            PREFS_PATH.write_text(json.dumps(prefs, indent=1))
            _prefs = prefs
        except Exception:
            pass


def reset_cache() -> None:
    global _prefs, _state
    with _prefs_lock:
        _prefs = None
    with _state_lock:
        _state = None


def classify(topic: str) -> str | None:
    """Map a money.* topic to its §45 notification type (None = unrouted)."""
    for type_name, patterns in TYPE_PATTERNS.items():
        for pattern in patterns:
            if fnmatch.fnmatch(topic or "", pattern):
                return type_name
    return None


def mode_for(type_name: str, prefs: dict | None = None) -> str:
    prefs = prefs or load_prefs()
    if type_name in ALWAYS_ON:
        return "instant"          # cannot be disabled (§45)
    mode = (prefs.get("modes") or {}).get(type_name, "digest")
    if mode not in ("instant", "digest", "off"):
        mode = "digest"
    return mode


def chat_id(prefs: dict | None = None) -> str | None:
    prefs = prefs or load_prefs()
    cid = prefs.get("chat_id") or os.environ.get("AKUSH_OPERATOR_CHAT_ID")
    return str(cid) if cid else None


def allowed_chats(prefs: dict | None = None) -> set:
    prefs = prefs or load_prefs()
    from_env = os.environ.get("AKUSH_TELEGRAM_ALLOWED_CHATS", "")
    chats = set(prefs.get("allowed_chats") or [])
    chats |= {c.strip() for c in from_env.split(",") if c.strip()}
    return chats


def in_quiet_hours(prefs: dict | None = None, now: datetime | None = None) -> bool:
    prefs = prefs or load_prefs()
    qh = prefs.get("quiet_hours") or {}
    start_s, end_s = qh.get("start", "22:00"), qh.get("end", "07:00")
    try:
        now = now or datetime.now()
        cur = now.hour * 60 + now.minute
        start = int(start_s.split(":")[0]) * 60 + int(start_s.split(":")[1])
        end = int(end_s.split(":")[0]) * 60 + int(end_s.split(":")[1])
        if start <= end:
            return start <= cur < end
        return cur >= start or cur < end
    except Exception:
        return False


def load_state() -> dict:
    global _state
    with _state_lock:
        if _state is None:
            try:
                _state = json.loads(STATE_PATH.read_text() or "{}")
            except Exception:
                _state = {}
            _state.setdefault("digest_buffer", [])
            _state.setdefault("sent_log", [])
            _state.setdefault("dedupe", {})
            _state.setdefault("last_digest_ts", None)
        return _state


def save_state() -> None:
    with _state_lock:
        try:
            STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
            STATE_PATH.write_text(json.dumps(_state, indent=1))
        except Exception:
            pass
