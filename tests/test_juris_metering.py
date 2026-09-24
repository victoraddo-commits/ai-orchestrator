"""TDD tests for per-account usage metering (Phase 7, Task 6).

The meter persists queries, deep-research runs and tokens per account, enforces
the free-tier caps (via the existing quota paths), and is surfaced in account
info and the Command Center. Existing limit behaviour for existing users must
not change.
"""

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


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    db_dir = tmp_path / "juris_db"
    db_dir.mkdir()
    import core.juris_kai.accounts as accts
    monkeypatch.setattr(accts, "DB_DIR", str(db_dir))
    monkeypatch.setattr(accts, "DB_PATH", str(db_dir / "juris_kai_accounts.db"))
    accts._account_manager = None
    yield
    accts._account_manager = None


def _mgr():
    from core.juris_kai.accounts import get_account_manager
    return get_account_manager()


def _free():
    return _mgr().get_or_create("meter-free", "Free")


class TestMeter:
    def test_meter_starts_empty(self):
        acct = _free()
        m = _mgr().usage_meter(acct["account_id"])
        assert m["plan"] == "free_trial"
        assert m["queries"]["used"] == 0
        assert m["queries"]["limit"] == 20
        assert m["deep_research"]["used"] == 0
        assert m["deep_research"]["limit"] == 1
        assert m["tokens_today"] == 0

    def test_queries_increment_the_meter(self):
        acct = _free()
        for _ in range(3):
            assert _mgr().try_record_query(acct["account_id"],
                                           input_tokens=5,
                                           output_tokens=7)["allowed"]
        m = _mgr().usage_meter(acct["account_id"])
        assert m["queries"]["used"] == 3
        assert m["queries"]["remaining"] == 17
        assert m["tokens_today"] == 3 * 12

    def test_free_query_cap_is_enforced(self):
        acct = _free()
        allowed = sum(1 for _ in range(25)
                      if _mgr().try_record_query(acct["account_id"])["allowed"])
        assert allowed == 20
        m = _mgr().usage_meter(acct["account_id"])
        assert m["queries"]["used"] == 20
        assert m["queries"]["remaining"] == 0

    def test_deep_research_meter_and_cap(self):
        acct = _free()
        first = _mgr().try_record_deep_research(acct["account_id"])
        assert first["allowed"] is True
        second = _mgr().try_record_deep_research(acct["account_id"])
        assert second["allowed"] is False
        m = _mgr().usage_meter(acct["account_id"])
        assert m["deep_research"]["used"] == 1
        assert m["deep_research"]["remaining"] == 0

    def test_report_export_counts_in_the_meter(self):
        acct = _free()
        assert _mgr().usage_meter(acct["account_id"])["report_exports"] == 0
        _mgr().record_usage(acct["account_id"], "report_export",
                            details="deep:r1")
        m = _mgr().usage_meter(acct["account_id"])
        assert m["report_exports"] == 1

    def test_paid_tier_has_room(self):
        acct = _mgr().create_account("pro@example.com", "Pro", "monthly_pro")
        m = _mgr().usage_meter(acct["account_id"])
        assert m["plan"] == "monthly_pro"
        assert m["queries"]["limit"] == 500
        assert m["deep_research"]["limit"] >= 10

    def test_meter_unknown_account_is_safe(self):
        m = _mgr().usage_meter("nope")
        assert m["plan"] == "free_trial"
        assert m["queries"]["used"] == 0


class TestAccountSurface:
    def test_bot_account_info_shows_meter(self):
        from core.juris_kai import bot
        acct = _free()
        out = bot._handle_account_info(999, acct)
        assert "Queries" in out["text"]
        assert "20" in out["text"]

    def test_command_account_shows_meter(self):
        from core.juris_kai import commands
        acct = _free()
        text = commands.handle_account(acct)
        assert "Queries" in text

    def test_cc_account_detail_includes_meter(self, monkeypatch):
        from core.juris_kai import dashboard
        acct = _free()
        detail = dashboard.get_account_detail(acct["account_id"])
        assert detail is not None
        assert "meter" in detail
        assert detail["meter"]["plan"] == "free_trial"
