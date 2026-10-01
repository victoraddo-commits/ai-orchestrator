"""tests/test_money_notify.py — §45 notification fan-out (offline, RED->GREEN).

Covers: event→message formatting (every §45 type incl. digest rollups),
prefs gating + quiet hours + rate limit + dedupe, token-absent → clean
disabled hold, and bus subscription wiring. Offline: transport + clock are
seams; the event bus is the real in-process singleton.
"""

import time
from datetime import datetime

import pytest

from core import kai_event_bus
from core.money_notify import prefs as prefs_mod
from core.money_notify import notify, formatter


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    (tmp_path / "memory").mkdir()
    monkeypatch.setattr(prefs_mod, "PREFS_PATH",
                        tmp_path / "memory" / "money_notify_prefs.json")
    monkeypatch.setattr(prefs_mod, "STATE_PATH",
                        tmp_path / "memory" / "money_notify_state.json")
    prefs_mod.reset_cache()
    monkeypatch.delenv("AKUSH_OPERATOR_CHAT_ID", raising=False)
    monkeypatch.delenv("AKUSH_TELEGRAM_ALLOWED_CHATS", raising=False)
    yield tmp_path
    prefs_mod.reset_cache()


@pytest.fixture
def sent(monkeypatch):
    calls = []

    def fake_send_now(text, buttons=None, *, now=None):
        calls.append({"text": text, "buttons": buttons, "ts": now or time.time()})
        return {"sent": True}

    monkeypatch.setattr(notify, "send_now", fake_send_now)
    return calls


def _with_chat(**over):
    prefs = prefs_mod.load_prefs()
    prefs["chat_id"] = "111"
    # default: never quiet (00:00-00:00 window matches nothing); quiet-hours
    # tests override explicitly
    prefs["quiet_hours"] = {"start": "00:00", "end": "00:00"}
    prefs.update(over)
    prefs_mod.save_prefs(prefs)
    return prefs


# ---------------------------------------------------------------------------
# formatting — every §45 type
# ---------------------------------------------------------------------------
def test_bill_due_formats_with_mark_paid_button():
    ev = formatter.format_event("bill_detected", {
        "commitment_id": 7, "occurrence_id": 42, "name": "ECG bill",
        "amount": 250, "due_date": "2026-10-05"})
    assert "ECG bill" in ev["text"] and "250" in ev["text"]
    assert ev["buttons"][0][0]["callback_data"] == "cmtpaid:7:42:0"
    assert ev["buttons"][0][0]["text"] == "Mark Paid"


def test_bill_paid_formats():
    ev = formatter.format_event("bill_paid", {"name": "ECG bill", "amount": 250})
    assert "paid" in ev["text"].lower() and "ECG" in ev["text"]


def test_account_discovered_masks_identifier():
    ev = formatter.format_event("account_discovered", {
        "name": "MoMo wallet", "kind": "mobile_money",
        "account_number": "0244999123", "status": "detected"})
    assert "••9123" in ev["text"]
    assert "0244999123" not in ev["text"]


def test_transaction_confirmed_formats_direction():
    ev = formatter.format_event("transaction_confirmed",
                                {"name": "Market", "amount": 45.5,
                                 "direction": "out"})
    assert "45.5" in ev["text"] and "out" in ev["text"]


def test_debt_payment_and_completed_format():
    ev = formatter.format_event("debt_payment", {
        "name": "Car loan", "amount": 400, "remaining_balance": 3200})
    assert "Car loan" in ev["text"] and "3200" in ev["text"]
    ev2 = formatter.format_event("debt_completed", {"name": "Car loan"})
    assert "completed" in ev2["text"].lower()


def test_income_and_payday_format():
    ev = formatter.format_event("income_detected", {
        "source": "Salary", "amount": 4500})
    assert "Salary" in ev["text"] and "4500" in ev["text"]
    ev2 = formatter.format_event("payday", {
        "date": "2026-10-28", "name": "Monthly"})
    assert "2026-10-28" in ev2["text"]


def test_anomaly_has_investigate_button():
    ev = formatter.format_event("anomaly", {
        "severity": "high", "kind": "duplicate_charge", "name": "MTN topup"})
    assert "Anomaly" in ev["text"] and "high" in ev["text"]
    assert ev["buttons"][0][0]["callback_data"] == "dash:open"


def test_reconciliation_failed_formats():
    ev = formatter.format_event("reconciliation_failed", {
        "name": "GCB account", "delta": 120.5})
    assert "Reconciliation" in ev["text"] and "120.5" in ev["text"]


# ---------------------------------------------------------------------------
# digest rollups
# ---------------------------------------------------------------------------
def test_digest_empty_is_honest():
    text = formatter.format_digest("daily", [])
    assert "No money events" in text


def test_daily_digest_rollup():
    items = [
        {"type": "bill_paid", "topic": "money.bill.paid",
         "payload": {"name": "ECG bill", "amount": 250}, "ts": 0},
        {"type": "transaction_confirmed", "topic": "money.tx.created",
         "payload": {"name": "Market", "amount": 30, "direction": "out"}, "ts": 0},
    ]
    text = formatter.format_digest("daily", items)
    assert "Daily money digest" in text
    assert "Bill paid ×1" in text and "Transaction ×1" in text
    assert "ECG bill" in text


def test_weekly_and_monthly_digest_headers():
    assert "Weekly money rollup" in formatter.format_digest("weekly", [])
    assert "Monthly money rollup" in formatter.format_digest("monthly", [])


# ---------------------------------------------------------------------------
# gating: prefs modes, quiet hours, rate limit, dedupe
# ---------------------------------------------------------------------------
def test_mode_off_sends_nothing():
    _with_chat()
    prefs = prefs_mod.load_prefs()
    prefs["modes"]["bill_paid"] = "off"
    prefs_mod.save_prefs(prefs)
    r = notify.handle_event("money.bill.paid", {"name": "x"}, now=1000,
                            send=lambda *a, **k: {"sent": True})
    assert r["notified"] is False and r["reason"] == "type off"


def test_digest_mode_buffers_then_flushes(sent):
    _with_chat()
    prefs = prefs_mod.load_prefs()
    prefs["modes"]["transaction_confirmed"] = "digest"
    prefs_mod.save_prefs(prefs)
    r = notify.handle_event("money.tx.created", {"id": 1, "name": "m"},
                            now=1000)
    assert r["notified"] is False and r["held_digest"] is True
    assert not sent, "digest events must not send instantly"
    res = notify.flush_digest("daily", now=1001)
    assert res["sent"] is True and res["items"] == 1
    assert "Transaction ×1" in sent[0]["text"]
    assert prefs_mod.load_state()["digest_buffer"] == []


def test_quiet_hours_hold_instant_events(sent):
    prefs = _with_chat()
    prefs["quiet_hours"] = {"start": "22:00", "end": "07:00"}
    prefs_mod.save_prefs(prefs)
    epoch = datetime(2026, 10, 1, 23, 30).timestamp()
    r = notify.handle_event("money.commitment.due", {"id": 9, "name": "x"},
                            now=epoch)
    assert r["notified"] is False and r["reason"] == "quiet hours"


def test_quiet_hours_do_not_silence_always_on_types(sent):
    prefs = _with_chat()
    prefs["quiet_hours"] = {"start": "22:00", "end": "07:00"}
    prefs_mod.save_prefs(prefs)
    epoch = datetime(2026, 10, 1, 23, 30).timestamp()
    r = notify.handle_event("money.anomaly.detected",
                            {"id": 5, "severity": "high"}, now=epoch)
    assert r["notified"] is True, "anomaly must always get through"
    assert r["reason"] == "always on"


def test_always_on_cannot_be_disabled(sent):
    prefs = _with_chat()
    prefs["modes"]["anomaly"] = "off"
    prefs_mod.save_prefs(prefs)
    r = notify.handle_event("money.anomaly.detected", {"id": 6}, now=1000)
    assert r["notified"] is True


def test_rate_limit_rolls_excess_into_digest(sent):
    prefs = _with_chat()
    prefs["rate_limit_per_hour"] = 1
    prefs_mod.save_prefs(prefs)
    r1 = notify.handle_event("money.commitment.due", {"id": 10}, now=2000)
    r2 = notify.handle_event("money.commitment.due", {"id": 11}, now=2010)
    assert r1["notified"] is True
    assert r2["notified"] is False and r2["reason"].startswith("rate limited")
    assert len(prefs_mod.load_state()["digest_buffer"]) == 1


def test_dedupe_window_suppresses_duplicates(sent):
    _with_chat()
    r1 = notify.handle_event("money.commitment.due", {"id": 20}, now=3000)
    r2 = notify.handle_event("money.commitment.due", {"id": 20}, now=3060)
    assert r1["notified"] is True
    assert r2["notified"] is False and r2["reason"] == "dedupe"


def test_unrouted_money_topic_is_ignored(sent):
    _with_chat()
    r = notify.handle_event("money.something_else", {"id": 1}, now=1000)
    assert r["notified"] is False and r["reason"] == "unrouted topic"
    assert not sent


# ---------------------------------------------------------------------------
# token-absent → disabled hold, never a crash
# ---------------------------------------------------------------------------
def test_token_absent_holds_message_no_crash(monkeypatch):
    _with_chat()
    import core.telegram.transport as transport
    import core.telegram.registry as reg
    # registry flag flipped ON (the operator's activation step) — only the
    # token is missing now
    monkeypatch.setattr(reg, "is_enabled", lambda bot: True)
    monkeypatch.setattr(transport, "token_for", lambda bot: "")
    monkeypatch.setattr("core.money_telegram.token.ensure_env_file",
                        lambda **k: False)
    r = notify.handle_event("money.commitment.due", {"id": 30, "name": "Water"},
                            now=2000)
    assert r["notified"] is False
    assert (r.get("send") or {}).get("reason") == "no token"
    assert any(p["payload"].get("id") == 30
               for p in prefs_mod.load_state()["digest_buffer"])


def test_send_now_requires_chat(monkeypatch):
    import core.telegram.transport as transport
    import core.telegram.registry as reg
    monkeypatch.setattr(transport, "token_for", lambda bot: "tok")
    monkeypatch.setattr(reg, "is_enabled", lambda bot: True)
    res = notify.deliver_now("t", now=1)
    assert res["sent"] is False
    assert res["reason"] == "no operator chat configured"


def test_send_now_bot_disabled(monkeypatch):
    _with_chat()
    import core.telegram.transport as transport
    monkeypatch.setattr(transport, "token_for", lambda bot: "tok")
    res = notify.deliver_now("t", now=1)
    assert res["sent"] is False and res["reason"] == "bot disabled"


# ---------------------------------------------------------------------------
# bus subscription wiring
# ---------------------------------------------------------------------------
def test_bus_subscription_delivers_money_events(sent):
    _with_chat()
    assert notify.register_subscriber() is True
    assert notify.register_subscriber() is False  # idempotent
    kai_event_bus.publish("money.commitment.due",
                          {"commitment_id": 77, "occurrence_id": 9,
                           "name": "Water bill", "amount": 60},
                          source="tests")
    assert any("Water bill" in c["text"] for c in sent)


def test_bus_subscription_ignores_unknown_topics(sent):
    _with_chat()
    notify.register_subscriber()
    n = len(sent)
    kai_event_bus.publish("money.unknown_topic", {"id": 1}, source="tests")
    assert len(sent) == n


def test_runtime_state_shape():
    st = notify.runtime_state()
    assert set(st) == {"bot", "registry_enabled", "token_present", "active",
                       "digest_buffered", "subscriber_registered"}
    assert st["bot"] == "akush233-bot"


# ---------------------------------------------------------------------------
# relay: akush-core tagged financial_events → bus
# ---------------------------------------------------------------------------
def test_relay_publishes_tagged_inbox_items(monkeypatch):
    from core.money_notify import relay

    class RelayClient:
        def get(self, path, query=None):
            if path == "/financial-inbox":
                return 200, {"data": [{"id": 12}, {"id": 11}]}
            if path == "/financial-inbox/11":
                return 200, {"kind": "transaction_candidate", "confidence": 0.9,
                             "payload": {"tag": "money.bill.due",
                                         "name": "ECG bill", "amount": 250}}
            if path == "/financial-inbox/12":
                return 200, {"kind": "anomaly", "confidence": 0.7,
                             "payload": {"tag": "money.anomaly.detected",
                                         "severity": "high"}}
            return 404, {}

    published = []
    res = relay.poll_once(client=RelayClient(),
                          publish=lambda t, p, source: published.append((t, p)),
                          state={})
    topics = [t for t, _ in published]
    assert topics == ["money.bill.due", "money.anomaly.detected"], topics
    assert res["relayed"] == 2 and res["last_id"] == 12
    # money.* classification + delivery chain receives it
    kinds = [prefs_mod.classify(t) for t in topics]
    assert kinds == ["bill_detected", "anomaly"]


def test_relay_skips_untagged_and_never_goes_backwards(monkeypatch):
    from core.money_notify import relay

    class RelayClient:
        def __init__(self):
            self.detail_calls = 0

        def get(self, path, query=None):
            if path == "/financial-inbox":
                return 200, {"data": [{"id": 20}, {"id": 19}]}
            if path.endswith("/20"):
                self.detail_calls += 1
                return 200, {"kind": "x", "payload": {"no_tag": True}}
            if path.endswith("/19"):
                return 200, {"kind": "x", "payload": {"tag": "money.payday.upcoming"}}
            return 404, {}

    c = RelayClient()
    state = {"relay_last_id": 19}
    published = []
    res = relay.poll_once(client=c,
                          publish=lambda t, p, source: published.append(t),
                          state=state)
    assert published == []  # 19 already relayed; 20 untagged
    assert res["relayed"] == 0
    assert c.detail_calls == 1  # only the new, untagged one was fetched
    assert state["relay_last_id"] == 20


def test_relay_survives_client_errors():
    from core.money_notify import relay

    class Dead:
        def get(self, *a, **k):
            raise RuntimeError("connection refused")

    res = relay.poll_once(client=Dead(), publish=lambda *a, **k: None, state={})
    assert res["relayed"] == 0 and "error" in res["reason"]
