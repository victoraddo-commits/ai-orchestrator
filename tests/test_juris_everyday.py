"""Everyday-Law menu + command for Juris Kai (Phase 7, Task 4).

The grounded explainer is produced on the Legal Brain; the bot only fetches,
renders, and passes it through the AgentGuard output gate + citation firewall.
These tests isolate the surface with a per-test DB and a stubbed client.
"""

import os
import sys
import tempfile
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

os.environ.setdefault(
    "JURIS_KAI_DB_DIR",
    str(Path(tempfile.gettempdir()) / "juris_kai_test"),
)

import core.juris_kai.bot as bot  # noqa: E402
from core.juris_kai import cache, everyday  # noqa: E402

GROUNDED = {
    "topic": "tenancy-rent",
    "label": "Tenancy & rent",
    "grounded": True,
    "verdict": "GROUNDED",
    "instrument": {"id": 5, "title": "Land Act 2020 (Act 1036)",
                   "citation": "Act 1036", "year": 2020, "store_mode": "full",
                   "temporal_status": "CURRENT"},
    "currency": "CURRENT",
    "explainer": "- A landlord shall give a tenant reasonable notice. [1]",
    "sources": [{"id": 5, "title": "Land Act 2020 (Act 1036)",
                 "citation": "Act 1036", "temporal_status": "CURRENT"}],
    "disclaimer": "Informational only — not legal advice.",
    "notice": None,
}

UNGROUNDED = {
    "topic": "traffic-tint",
    "label": "Traffic & vehicle tint",
    "grounded": False,
    "verdict": "UNGROUNDED",
    "instrument": None,
    "currency": "UNKNOWN",
    "explainer": "",
    "sources": [],
    "disclaimer": "Informational only — not legal advice.",
    "notice": "No authoritative Ghanaian source for this topic is in the "
              "database yet, so I won't guess.",
}


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    db_dir = tmp_path / "juris_db"
    db_dir.mkdir()
    import core.juris_kai.accounts as accts
    monkeypatch.setattr(accts, "DB_DIR", str(db_dir))
    monkeypatch.setattr(accts, "DB_PATH", str(db_dir / "juris_kai_accounts.db"))
    accts._account_manager = None
    cache.clear_caches()
    import core.juris_kai.session as session
    monkeypatch.setattr(session, "STORAGE_PATH", tmp_path / "sessions.json")
    yield
    accts._account_manager = None
    cache.clear_caches()


def _account():
    from core.juris_kai.accounts import get_account_manager
    mgr = get_account_manager()
    acct = mgr.get_or_create(str(uuid.uuid4().int)[:9], "Tester")
    mgr.accept_disclaimer(acct["account_id"])
    return acct


# --- pure helpers -----------------------------------------------------------

def test_resolve_topic_accepts_key_label_and_substring():
    assert everyday.resolve_topic("tenancy-rent") == "tenancy-rent"
    assert everyday.resolve_topic("🏠 Tenancy & rent") == "tenancy-rent"
    assert everyday.resolve_topic("employment") == "employment-rights"
    assert everyday.resolve_topic("nonsense") is None
    assert everyday.resolve_topic("") is None


def test_menu_has_every_topic_and_back():
    import json
    menu = json.loads(everyday.everyday_menu())
    buttons = [b["text"] for row in menu["keyboard"] for b in row]
    for _key, label in everyday.TOPIC_LABELS:
        assert label in buttons
    assert any("Back to Menu" in b for b in buttons)


def test_render_grounded_has_instrument_currency_disclaimer():
    txt = everyday.render_explainer(GROUNDED)
    assert "Everyday Law" in txt
    assert "Land Act 2020 (Act 1036)" in txt
    assert "Currency:" in txt and "CURRENT" in txt
    assert "not legal advice" in txt


def test_render_ungrounded_is_honest():
    txt = everyday.render_explainer(UNGROUNDED)
    assert "won't guess" in txt
    assert "not legal advice" in txt


# --- bot surface ------------------------------------------------------------

def test_everyday_command_without_args_shows_menu(monkeypatch):
    acct = _account()
    resp = bot._handle_everyday_command("", 123, acct)
    assert "Everyday Law" in resp["text"]
    assert "Traffic & vehicle tint" in resp["reply_markup"]


def test_everyday_command_unknown_topic_lists_choices():
    acct = _account()
    resp = bot._handle_everyday_command("xylophone", 123, acct)
    assert "don't know the topic" in resp["text"]
    assert "Tenancy & rent" in resp["text"]


def test_everyday_topic_runs_output_gate_and_firewall(monkeypatch):
    from core import legal_brain_client as lb
    acct = _account()
    monkeypatch.setattr(lb, "everyday", lambda topic: dict(GROUNDED))
    calls = {"guard": [], "fw": []}
    monkeypatch.setattr(bot, "_guard_outbound_text",
                        lambda text, source="": calls["guard"].append(source) or text)
    monkeypatch.setattr(bot, "_citation_firewall_transform",
                        lambda: (lambda t: calls["fw"].append(True) or t))

    resp = bot._handle_everyday_topic("tenancy-rent", 123, acct)

    assert calls["guard"] == ["juris_everyday"]
    assert calls["fw"] == [True]
    assert "Land Act 2020 (Act 1036)" in resp["text"]
    assert resp["parse_mode"] == "Markdown"


def test_everyday_topic_ungrounded_never_fabricates(monkeypatch):
    from core import legal_brain_client as lb
    acct = _account()
    monkeypatch.setattr(lb, "everyday", lambda topic: dict(UNGROUNDED))
    resp = bot._handle_everyday_topic("traffic-tint", 123, acct)
    assert "won't guess" in resp["text"]


def test_everyday_topic_client_failure_degrades_honestly(monkeypatch):
    from core import legal_brain_client as lb
    acct = _account()

    def boom(topic):
        raise RuntimeError("brain down")

    monkeypatch.setattr(lb, "everyday", boom)
    resp = bot._handle_everyday_topic("tenancy-rent", 123, acct)
    assert "couldn't reach the legal database" in resp["text"]


def test_everyday_menu_button_shows_intro():
    acct = _account()
    resp = bot._handle_menu_action("📖 Everyday Law", 123, acct, False, {})
    assert resp is not None
    assert "Everyday Law" in resp["text"]
    assert "Traffic & vehicle tint" in resp["reply_markup"]


def test_everyday_client_hits_endpoints(monkeypatch):
    from core import legal_brain_client as lb
    seen = {}

    def fake_get(path, timeout=8):
        seen["path"] = path
        if path == "/everyday":
            return {"topics": [{"key": "tenancy-rent"}]}
        return dict(GROUNDED)

    monkeypatch.setattr(lb, "_get", fake_get)
    assert lb.everyday_topics() == [{"key": "tenancy-rent"}]
    assert lb.everyday("tenancy-rent")["topic"] == "tenancy-rent"
    assert seen["path"] == "/everyday/tenancy-rent"
