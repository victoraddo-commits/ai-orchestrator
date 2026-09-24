"""TDD tests for the Juris Kai plan entitlement matrix (Phase 7, Task 5).

``core/juris_kai/entitlements.py`` is the single source of truth for which
features and quotas each plan tier grants. These tests pin the matrix, the
feature/upgrade-prompt behaviour, and the fact that operator edits to the
pricing store flow through to entitlements (so the bot and the Command Center
Pricing tab can never disagree).
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

from core.juris_kai import entitlements  # noqa: E402


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


# ── matrix ────────────────────────────────────────────────────────────────

class TestEntitlementMatrix:
    def test_every_plan_has_features_and_quotas(self):
        from core.juris_kai.accounts import SUBSCRIPTION_TIERS
        for tier in SUBSCRIPTION_TIERS:
            ent = entitlements.entitlements_for(tier)
            assert ent["plan"] == tier
            assert isinstance(ent["features"], list)
            assert isinstance(ent["quotas"], dict)
            for q in ("queries_per_day", "documents_per_month",
                      "deep_research_per_day"):
                assert q in ent["quotas"]
                assert isinstance(ent["quotas"][q], int)

    def test_matrix_covers_expected_plans(self):
        m = entitlements.matrix()
        for plan in ("free_trial", "student", "monthly_basic", "monthly_pro",
                     "annual_pro", "institution"):
            assert plan in m, plan

    def test_free_tier_is_limited(self):
        assert entitlements.has_feature("free_trial", "basic_legal_qa") is True
        assert entitlements.has_feature("free_trial", "everyday_law") is True
        assert entitlements.has_feature("free_trial", "flashcards") is False
        assert entitlements.has_feature("free_trial",
                                        "argument_construction") is False
        assert entitlements.has_feature("free_trial", "api_access") is False
        # The free tier keeps a single daily Deep Research run (metered/capped).
        assert entitlements.has_feature("free_trial", "deep_research") is True

    def test_student_tier_includes_student_tools(self):
        for feature in ("flashcards", "practice_tools", "mock_exams",
                        "revision_notes", "deep_research"):
            assert entitlements.has_feature("student", feature) is True, feature

    def test_pro_and_annual_legacy_features_preserved(self):
        assert entitlements.has_feature("monthly_pro", "export_reports") is True
        assert entitlements.has_feature("monthly_pro", "api_access") is False
        assert entitlements.has_feature("annual_pro", "api_access") is True

    def test_institution_has_seats(self):
        assert entitlements.has_feature("institution",
                                        "institutional_seats") is True

    def test_quotas_per_tier(self):
        assert entitlements.quota("free_trial", "queries_per_day") == 20
        assert entitlements.quota("student", "queries_per_day") >= 100
        assert entitlements.quota("monthly_pro", "queries_per_day") == 500
        assert entitlements.quota("free_trial", "deep_research_per_day") == 1
        assert entitlements.quota("monthly_pro",
                                  "deep_research_per_day") >= 10

    def test_no_entitlement_leaks_on_unknown_plan_or_feature(self):
        assert entitlements.has_feature("does_not_exist", "flashcards") is False
        assert entitlements.has_feature("monthly_pro", "not_a_feature") is False
        assert entitlements.features_for("does_not_exist") == []
        assert entitlements.quota("does_not_exist", "queries_per_day") == 0

    def test_features_for_returns_a_copy(self):
        feats = entitlements.features_for("monthly_pro")
        feats.append("hacked")
        assert "hacked" not in entitlements.features_for("monthly_pro")


# ── upgrade prompt ────────────────────────────────────────────────────────

class TestUpgradePrompt:
    def test_prompt_is_human_and_actionable(self):
        msg = entitlements.upgrade_prompt("flashcards", "free_trial")
        assert "Flashcards" in msg
        assert "/subscribe" in msg

    def test_unknown_feature_never_crashes(self):
        msg = entitlements.upgrade_prompt("mystery_feature", "free_trial")
        assert "mystery_feature" in msg or "mystery feature" in msg
        assert "/subscribe" in msg


# ── enforcement helper ────────────────────────────────────────────────────

class TestCheckFeature:
    def test_free_user_gets_prompt_for_paid_feature(self):
        acct = _mgr().get_or_create("ent-1", "T")
        msg = entitlements.check_feature(_mgr(), acct["account_id"],
                                         "argument_construction")
        assert msg and "/subscribe" in msg

    def test_paid_user_is_allowed(self):
        acct = _mgr().get_or_create("ent-2", "T")
        _mgr().set_subscription(acct["account_id"], "monthly_pro")
        assert entitlements.check_feature(_mgr(), acct["account_id"],
                                          "argument_construction") is None

    def test_unknown_account_never_crashes(self):
        # An unknown account is treated as free: it must be denied, not raise.
        msg = entitlements.check_feature(_mgr(), "nope", "flashcards")
        assert msg

    def test_bot_blocks_student_step_for_free_user(self):
        import core.juris_kai.bot as bot
        acct = _mgr().get_or_create("ent-bot-1", "T")
        bot._conversation_state["555"] = {"step": "flashcards", "data": {}}
        try:
            out = bot._handle_conversation_flow("contract law", 555, acct)
            assert "/subscribe" in out["text"]
            assert "555" not in bot._conversation_state
        finally:
            bot._conversation_state.clear()


# ── pricing store is the runtime source (bot ⇄ CC agreement) ──────────────

class TestPricingAgreement:
    def test_operator_feature_edit_flows_into_entitlements(self, monkeypatch,
                                                           tmp_path):
        from copy import deepcopy
        from core.juris_kai import pricing
        monkeypatch.setenv("JURIS_PRICING_PATH",
                           str(tmp_path / "juris_pricing.json"))
        accts = sys.modules["core.juris_kai.accounts"]
        saved = deepcopy(accts.SUBSCRIPTION_TIERS)
        try:
            tiers = deepcopy(pricing.DEFAULT_TIERS)
            tiers["free_trial"]["features"] = ["basic_legal_qa", "flashcards"]
            pricing.save_pricing(tiers, 2.0, updated_by="tester")
            assert entitlements.has_feature("free_trial", "flashcards") is True
        finally:
            accts.SUBSCRIPTION_TIERS.clear()
            accts.SUBSCRIPTION_TIERS.update(saved)
