"""tests/test_money_telegram.py — akush233-bot inbound handlers (§44).

Offline: fake akush-core client + send seam. Covers the §44 main menu,
NL ask with payload-basis footer, inbox/commitment callbacks (same API the
PWA uses), settings toggles, allowlist rejection, and the token-absent
fail-safe (disabled, no crash).
"""

import json

import pytest

from core.money_notify import prefs as prefs_mod
from core.money_telegram import handlers, menu
from core.money_telegram.token import runtime_ready


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


class FakeClient:
    def __init__(self):
        self.calls = []

    def _rec(self, method, path, **k):
        self.calls.append({"method": method, "path": path, **k})

    def get(self, path, query=None):
        self._rec("GET", path, query=query)
        return self._response(("GET", path))

    def post(self, path, body=None, idempotency_key=None):
        self._rec("POST", path, body=body, idempotency_key=idempotency_key)
        return self._response(("POST", path))

    def ask(self, q):
        self._rec("POST", "/search/ask", body={"q": q})
        return self._response(("ASK",))

    def _response(self, key):
        canned = {
            ("GET", "/commitments/due-today"): (200, {"data": [
                {"commitment_id": 7, "occurrence_id": 42,
                 "name": "ECG bill", "amount": 250}]}),
            ("GET", "/commitments/overdue"): (200, {"data": []}),
            ("GET", "/financial-inbox"): (200, {"data": [
                {"id": 5, "event_kind": "transaction_candidate",
                 "confidence": 0.86, "status": "pending"}]}),
            ("GET", "/net-worth"): (200, {"net_worth": "GHS 12400"}),
            ("GET", "/forecasts/safe-to-spend"): (200, {"safe_to_spend": 310}),
            ("GET", "/accounts"): (200, {"data": [
                {"name": "GCB main", "kind": "bank", "status": "ACTIVE",
                 "ledger_balance": 3200}]}),
        }
        if key[0] == "ASK":
            question = self.calls[-1]["body"]["q"]
            if "transport" in question:
                return (200, {"q": question, "intent": "spend_in_month",
                              "answer": {"text": "You spent GHS 120 on transport.",
                                         "suggestions": []},
                              "basis": {"source": "transactions",
                                        "rows": 4}})
            return (200, {"q": question, "intent": "unsupported",
                          "answer": {"text": "I can't answer that from the ledger yet.",
                                     "suggestions": ["try: how much did I spend this month?"]},
                          "basis": None})
        if key[0] == "POST":
            if key[1].startswith("/financial-inbox/"):
                inbox_id = key[1].split("/")[2]
                return (200, {"ok": True}) if inbox_id == "5" else \
                    (404, {"error": "not_found", "message": "no such item"})
            if key[1].startswith("/commitments/"):
                return (200, {"ok": True})
            return (404, {"error": "not_found"})
        return canned.get(key, (404, {"error": "not_found"}))


@pytest.fixture
def client():
    return FakeClient()


@pytest.fixture
def sent():
    calls = []
    return calls, lambda chat_id, text, buttons=None: (
        calls.append({"chat_id": chat_id, "text": text,
                      "buttons": buttons or []}) or {"ok": True})


def _allow(chat="111"):
    prefs = prefs_mod.load_prefs()
    prefs["quiet_hours"] = {"start": "00:00", "end": "00:00"}
    prefs["allowed_chats"] = [chat]
    prefs_mod.save_prefs(prefs)
    return prefs


def _update(text, chat=111):
    return {"message": {"chat": {"id": chat}, "text": text}}


def _cb(data, chat=111):
    return {"callback_query": {"data": data,
                               "message": {"chat": {"id": chat}}}}


# ---------------------------------------------------------------------------
# allowlist
# ---------------------------------------------------------------------------
def test_non_allowlisted_chat_is_silently_rejected(client, sent):
    calls, send = sent
    r = handlers.handle_update(_update("/start", chat=999), client=client,
                               send=send)
    assert r["handled"] is False and r["reason"] == "chat not allowlisted"
    assert not calls and not client.calls


def test_allowlisted_chat_served(client, sent):
    _allow()
    calls, send = sent
    r = handlers.handle_update(_update("/start"), client=client, send=send)
    assert r["handled"] is True


# ---------------------------------------------------------------------------
# §44 main menu
# ---------------------------------------------------------------------------
def test_start_menu_contains_all_44_items(client, sent):
    _allow()
    calls, send = sent
    handlers.handle_update(_update("/start"), client=client, send=send)
    kb = calls[0]["buttons"]
    labels = [b["text"] for row in kb for b in row]
    for expected in ["Money", "Planning", "Bills", "Debts", "Payday",
                     "Utilities", "Goals", "Reports", "Accounts", "Inbox",
                     "Open Dashboard", "Settings"]:
        assert expected in labels, f"missing {expected} in menu"
    assert calls[0]["chat_id"] == 111


def test_menu_callback_routes_to_section(client, sent):
    _allow()
    calls, send = sent
    r = handlers.handle_update(_cb("menu:bills"), client=client, send=send)
    assert r["handled"] is True
    paths = [c["path"] for c in client.calls]
    assert "/commitments/due-today" in paths
    assert "Due today" in calls[-1]["text"]
    assert any(b["callback_data"].startswith("cmtpaid:7:42")
               for row in calls[-1]["buttons"] for b in row)


def test_inbox_section_lists_pending_with_action_buttons(client, sent):
    _allow()
    calls, send = sent
    handlers.handle_update(_cb("menu:inbox"), client=client, send=send)
    flat = [b["callback_data"] for row in calls[-1]["buttons"] for b in row]
    for action in ["confirm", "reject", "match", "ignore", "investigate"]:
        assert f"inbox:5:{action}" in flat, f"missing {action} button"


def test_dashboard_callback_deep_links(client, sent):
    _allow()
    calls, send = sent
    handlers.handle_update(_cb("menu:dashboard"), client=client, send=send)
    assert menu.DASHBOARD_URL in calls[-1]["text"]


def test_reports_section_is_honest_when_unavailable(client, sent):
    _allow()
    calls, send = sent
    handlers.handle_update(_cb("menu:reports"), client=client, send=send)
    assert "not available yet" in calls[-1]["text"]


# ---------------------------------------------------------------------------
# NL ask — answers only from query results + basis footer
# ---------------------------------------------------------------------------
def test_nl_query_calls_search_ask_with_basis_footer(client, sent):
    _allow()
    calls, send = sent
    handlers.handle_update(
        _update("how much did I spend on transport this month?"),
        client=client, send=send)
    ask_calls = [c for c in client.calls if c["path"] == "/search/ask"]
    assert len(ask_calls) == 1
    assert "transport" in ask_calls[0]["body"]["q"]
    text = calls[-1]["text"]
    assert "GHS 120" in text
    assert "basis: spend_in_month · transactions · 4 row(s)" in text


def test_unsupported_query_is_honest(client, sent):
    _allow()
    calls, send = sent
    handlers.handle_update(_update("what is my bitcoin worth?"),
                           client=client, send=send)
    text = calls[-1]["text"]
    assert "can't answer" in text or "can’t answer" in text
    assert "basis: none" in text


# ---------------------------------------------------------------------------
# callbacks → akush-core API (same endpoints as the PWA)
# ---------------------------------------------------------------------------
def test_inbox_confirm_callback_calls_act_endpoint(client, sent):
    _allow()
    calls, send = sent
    handlers.handle_update(_cb("inbox:5:confirm"), client=client, send=send)
    posts = [c for c in client.calls if c["method"] == "POST"]
    assert posts[0]["path"] == "/financial-inbox/5/act"
    assert posts[0]["body"] == {"action": "confirm"}
    assert "confirm ✓" in calls[-1]["text"]


def test_inbox_reject_callback(client, sent):
    _allow()
    calls, send = sent
    handlers.handle_update(_cb("inbox:5:reject"), client=client, send=send)
    posts = [c for c in client.calls if c["method"] == "POST"]
    assert posts[0]["body"] == {"action": "reject"}


def test_mark_paid_with_transaction_id_calls_endpoint(client, sent):
    _allow()
    calls, send = sent
    handlers.handle_update(_cb("cmtpaid:7:42:15"), client=client, send=send)
    posts = [c for c in client.calls if c["method"] == "POST"]
    assert posts[0]["path"] == "/commitments/7/occurrences/42/mark-paid"
    assert posts[0]["body"] == {"transaction_id": 15}
    assert "paid ✓" in calls[-1]["text"]


def test_mark_paid_without_transaction_id_is_honest(client, sent):
    _allow()
    calls, send = sent
    handlers.handle_update(_cb("cmtpaid:7:42:0"), client=client, send=send)
    assert not [c for c in client.calls if c["method"] == "POST"]
    assert "needs the matching payment transaction" in calls[-1]["text"]


def test_action_failure_surfaces_status_not_secrets(client, sent):
    _allow()
    calls, send = sent
    handlers.handle_update(_cb("inbox:9:confirm"), client=client, send=send)
    assert "404" in calls[-1]["text"]


# ---------------------------------------------------------------------------
# settings (§45 prefs over Telegram)
# ---------------------------------------------------------------------------
def test_settings_menu_shows_modes_and_always_on(client, sent):
    _allow()
    calls, send = sent
    handlers.handle_update(_cb("menu:settings"), client=client, send=send)
    flat_labels = [b["text"] for row in calls[-1]["buttons"] for b in row]
    assert any("always on" in lbl for lbl in flat_labels)
    assert any("\U0001f512" in lbl for lbl in flat_labels)  # locked emoji
    flat = [b["callback_data"] for row in calls[-1]["buttons"] for b in row]
    assert any(d.startswith("set:bill_detected:") for d in flat)


def test_settings_toggle_updates_prefs(client, sent):
    _allow()
    calls, send = sent
    handlers.handle_update(_cb("set:bill_detected:off"), client=client,
                           send=send)
    assert prefs_mod.load_prefs()["modes"]["bill_detected"] == "off"
    assert "bill_detected: set to off" in calls[-1]["text"]


def test_settings_always_on_type_cannot_change(client, sent):
    _allow()
    calls, send = sent
    handlers.handle_update(_cb("set:anomaly:off"), client=client, send=send)
    assert "always on" in calls[-1]["text"]


# ---------------------------------------------------------------------------
# fail-safes
# ---------------------------------------------------------------------------
def test_malformed_update_is_rejected_cleanly(client, sent):
    _allow()
    calls, send = sent
    r = handlers.handle_update({"message": {}}, client=client, send=send)
    assert r["handled"] is False and r["reason"] == "no chat"


def test_runtime_ready_requires_registry_flag_and_token(monkeypatch):
    import core.telegram.registry as reg
    import core.money_telegram.token as token_mod
    monkeypatch.setattr(token_mod, "token_available", lambda **k: True)
    # real registry: akush233-bot is disabled until the operator flag flip
    assert runtime_ready() is False
    # flag flipped + token present → active
    monkeypatch.setattr(reg, "is_enabled", lambda b: True)
    assert runtime_ready() is True


def test_token_env_file_from_vault(monkeypatch, tmp_path):
    import core.money_telegram.token as token_mod
    env_file = tmp_path / "akush_bot.env"
    monkeypatch.setattr(token_mod, "ENV_FILE", str(env_file))
    monkeypatch.setattr(token_mod, "_vault_fetch", lambda path: "12:ABC")
    assert token_mod.ensure_env_file() is True
    raw = env_file.read_text()
    assert raw == "TELEGRAM_BOT_TOKEN=12:ABC\n"
    import os, stat
    mode = stat.S_IMODE(os.stat(env_file).st_mode)
    assert mode == 0o600
    # second call without force does not rewrite
    monkeypatch.setattr(token_mod, "_vault_fetch",
                        lambda path: (_ for _ in ()).throw(AssertionError("no fetch")))
    assert token_mod.ensure_env_file() is True


def test_token_absent_fails_safe(monkeypatch, tmp_path):
    import core.money_telegram.token as token_mod
    monkeypatch.setattr(token_mod, "ENV_FILE", str(tmp_path / "missing.env"))
    monkeypatch.setattr(token_mod, "_vault_fetch", lambda path: None)
    assert token_mod.ensure_env_file() is False
    assert token_mod.runtime_ready() is False
