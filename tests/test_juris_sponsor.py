"""TDD tests for the contextual, feature-flagged sponsor slot (Phase 7, Task 6).

The sponsor line is:
  * off by default;
  * free-tier only — never shown on any paid tier;
  * rendered as a footer *alongside* an answer, never embedded in the answer
    text (so it can never leak into the stored/learning-loop answer);
  * configurable from config/env and non-behavioural (no tracking).
"""

import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

os.environ.setdefault(
    "JURIS_KAI_DB_DIR",
    str(Path(tempfile.gettempdir()) / "juris_kai_test"),
)

from core.juris_kai import sponsor  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    monkeypatch.setenv("JURIS_SPONSORS_PATH", str(tmp_path / "sponsors.json"))
    monkeypatch.delenv("JURIS_SPONSOR_ENABLED", raising=False)
    monkeypatch.delenv("JURIS_SPONSORS", raising=False)
    yield


SPONSORS = [{"name": "Ghana Law Reports", "tagline": "Case law, weekly",
             "url": "https://example.gh"}]


def _enable(sponsors=SPONSORS):
    sponsor.save_config({"enabled": True, "sponsors": sponsors})


# ── defaults / config ─────────────────────────────────────────────────────

class TestConfig:
    def test_off_by_default(self):
        assert sponsor.enabled() is False
        assert sponsor.footer_for("free_trial") == ""

    def test_save_and_load_roundtrip(self):
        _enable()
        assert sponsor.enabled() is True
        cfg = sponsor.load_config()
        assert cfg["sponsors"][0]["name"] == "Ghana Law Reports"

    def test_env_enables_without_file(self, monkeypatch):
        monkeypatch.setenv("JURIS_SPONSOR_ENABLED", "1")
        monkeypatch.setenv("JURIS_SPONSORS",
                           json.dumps([{"name": "Env Sponsor"}]))
        assert sponsor.enabled() is True
        assert "Env Sponsor" in sponsor.footer_for("free_trial")


# ── free-tier only ────────────────────────────────────────────────────────

class TestTierGating:
    def test_footer_on_free_tier(self):
        _enable()
        footer = sponsor.footer_for("free_trial")
        assert "Ghana Law Reports" in footer

    def test_no_footer_on_paid_tiers(self):
        _enable()
        for tier in ("student", "monthly_basic", "monthly_pro", "annual_pro",
                     "institution"):
            assert sponsor.footer_for(tier) == "", tier

    def test_no_footer_when_no_sponsors_configured(self):
        sponsor.save_config({"enabled": True, "sponsors": []})
        assert sponsor.footer_for("free_trial") == ""

    def test_is_free_tier_helper(self):
        assert sponsor.is_free_tier("free_trial") is True
        assert sponsor.is_free_tier("free") is True
        assert sponsor.is_free_tier("monthly_pro") is False


# ── attach (never inside the answer) ──────────────────────────────────────

class TestAttach:
    def test_disabled_returns_answer_unchanged(self):
        assert sponsor.attach("ANSWER", "free_trial") == "ANSWER"

    def test_attach_appends_footer_separately(self):
        _enable()
        out = sponsor.attach("ANSWER", "free_trial")
        assert out.startswith("ANSWER")
        assert "Ghana Law Reports" in out
        # The footer is a distinct trailing block, not woven into the answer.
        answer_part, _, footer_part = out.partition("\n\n")
        assert answer_part == "ANSWER"
        assert "Ghana Law Reports" in footer_part

    def test_attach_paid_tier_unchanged(self):
        _enable()
        assert sponsor.attach("ANSWER", "monthly_pro") == "ANSWER"


# ── bot delivery: footer alongside, not in the stored answer ──────────────

class TestBotDelivery:
    @pytest.fixture(autouse=True)
    def fresh_db(self, tmp_path, monkeypatch):
        db_dir = tmp_path / "juris_db"
        db_dir.mkdir()
        import core.juris_kai.accounts as accts
        monkeypatch.setattr(accts, "DB_DIR", str(db_dir))
        monkeypatch.setattr(accts, "DB_PATH",
                            str(db_dir / "juris_kai_accounts.db"))
        accts._account_manager = None
        from core.juris_kai import cache
        cache.clear_caches()
        yield
        accts._account_manager = None
        cache.clear_caches()

    def test_sponsor_footer_shown_but_not_stored_in_answer(self, monkeypatch):
        import core.juris_kai.bot as bot
        from core.juris_kai.accounts import get_account_manager

        _enable()
        acct = get_account_manager().get_or_create("sponsor-1", "T")
        monkeypatch.setattr(bot, "_generate_reply",
                            lambda *a, **k: ("ANSWER", "m", False, False))

        plan = {"prompt": "p", "banner": "", "footer": "",
                "source_key": "", "docs": []}
        out = bot._deliver_grounded_plan(plan, "q", 1, acct, None, "",
                                         "juris_research", 0.0)

        assert "Ghana Law Reports" in out["text"]
        rows = get_account_manager().qa_history(acct["account_id"])
        assert rows and "Ghana Law Reports" not in rows[0]["answer"]

    def test_streamed_suffix_carries_sponsor_not_stored(self, monkeypatch):
        import core.juris_kai.bot as bot
        from core.juris_kai.accounts import get_account_manager

        _enable()
        acct = get_account_manager().get_or_create("sponsor-2", "T")
        captured = {}

        def fake(prompt, task_type, query, fallback_label, account_id="",
                 chat_id=None, reply_markup=None, context="", prefix="",
                 suffix="", source_key="", **k):
            captured["suffix"] = suffix
            return "ANSWER", "m", True, False

        monkeypatch.setattr(bot, "_generate_reply", fake)
        plan = {"prompt": "p", "banner": "", "footer": "",
                "source_key": "", "docs": []}
        out = bot._deliver_grounded_plan(plan, "q", 1, acct, None, "",
                                         "juris_research", 0.0)

        # The streamed final edit carries the sponsor in its suffix; the caller
        # returns no text (the stream already delivered it)...
        assert "Ghana Law Reports" in captured["suffix"]
        assert out["text"] is None
        # ...and the stored answer never contains it.
        rows = get_account_manager().qa_history(acct["account_id"])
        assert rows and "Ghana Law Reports" not in rows[0]["answer"]

    def test_no_sponsor_on_paid_tier_delivery(self, monkeypatch):
        import core.juris_kai.bot as bot
        from core.juris_kai.accounts import get_account_manager

        _enable()
        acct = get_account_manager().create_account(
            "paid@example.com", "Paid", "monthly_pro")
        monkeypatch.setattr(bot, "_generate_reply",
                            lambda *a, **k: ("ANSWER", "m", False, False))

        plan = {"prompt": "p", "banner": "", "footer": "",
                "source_key": "", "docs": []}
        out = bot._deliver_grounded_plan(plan, "q", 1, acct, None, "",
                                         "juris_research", 0.0)
        assert "Ghana Law Reports" not in out["text"]


# ── CC settings endpoints ─────────────────────────────────────────────────

class TestSponsorEndpoints:
    def _client(self, monkeypatch):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from core.juris_kai import cc_routes
        monkeypatch.setattr("core.bridge_auth._load_api_token",
                            lambda: "test-bridge-token")
        cc_routes._rate_state.clear()
        app = FastAPI()
        app.include_router(cc_routes.router)
        return TestClient(app)

    def test_get_requires_credentials(self, monkeypatch):
        client = self._client(monkeypatch)
        assert client.get("/api/juris-kai/cc/sponsors").status_code == 401

    def test_put_then_get_roundtrip(self, monkeypatch):
        client = self._client(monkeypatch)
        h = {"Authorization": "Bearer test-bridge-token"}
        r = client.put("/api/juris-kai/cc/sponsors",
                       headers=h,
                       json={"enabled": True, "sponsors": SPONSORS})
        assert r.json()["success"] is True
        body = client.get("/api/juris-kai/cc/sponsors", headers=h).json()
        assert body["enabled"] is True
        assert body["sponsors"][0]["name"] == "Ghana Law Reports"
