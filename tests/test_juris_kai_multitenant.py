"""Tests for Juris Kai multi-tenant account management.

Verifies: account CRUD, subscription tiers, usage limits, billing,
disclaimer flow, security boundaries.
"""

import os
import sys
import json
import time
import tempfile
from pathlib import Path
import pytest

# Use a test database
os.environ["JURIS_KAI_DB_DIR"] = str(Path(tempfile.gettempdir()) / "juris_kai_test")

sys.path.insert(0, str(Path(__file__).parent.parent))


class TestAccountManager:
    """Multi-tenant account CRUD and subscription tests."""

    @pytest.fixture(autouse=True)
    def setup(self):
        """Clean test DB before each test."""
        import sqlite3
        db_path = Path(os.environ["JURIS_KAI_DB_DIR"]) / "juris_kai_accounts.db"
        if db_path.exists():
            db_path.unlink()
        # Force fresh singleton
        import core.juris_kai.accounts as accts
        accts._account_manager = None
        yield
        if db_path.exists():
            db_path.unlink()

    def test_get_or_create_new_account(self):
        """Creating a new account returns a trial account with disclaimer not accepted."""
        from core.juris_kai.accounts import get_account_manager
        mgr = get_account_manager()
        acct = mgr.get_or_create("123456789", "Test User")

        assert acct["is_new"] is True
        assert acct["telegram_id"] == "123456789"
        assert acct["full_name"] == "Test User"
        assert acct["subscription_tier"] == "free_trial"
        assert acct["disclaimer_accepted"] == 0
        assert acct["is_active"] == 1
        assert "account_id" in acct

    def test_get_or_create_existing(self):
        """Getting an existing account returns is_new=False."""
        from core.juris_kai.accounts import get_account_manager
        mgr = get_account_manager()
        first = mgr.get_or_create("123456789")
        assert first["is_new"] is True

        second = mgr.get_or_create("123456789")
        assert second["is_new"] is False
        assert second["account_id"] == first["account_id"]

    def test_different_users_get_different_accounts(self):
        """Each Telegram ID gets its own isolated account."""
        from core.juris_kai.accounts import get_account_manager
        mgr = get_account_manager()
        a1 = mgr.get_or_create("111")
        a2 = mgr.get_or_create("222")

        assert a1["account_id"] != a2["account_id"]
        assert a1["telegram_id"] == "111"
        assert a2["telegram_id"] == "222"

    def test_accept_disclaimer(self):
        """Accepting disclaimer sets the flag."""
        from core.juris_kai.accounts import get_account_manager
        mgr = get_account_manager()
        acct = mgr.get_or_create("123")
        assert acct["disclaimer_accepted"] == 0

        mgr.accept_disclaimer(acct["account_id"])
        updated = mgr.get_account(acct["account_id"])
        assert updated["disclaimer_accepted"] == 1

    def test_update_profile(self):
        """Profile fields can be updated."""
        from core.juris_kai.accounts import get_account_manager
        mgr = get_account_manager()
        acct = mgr.get_or_create("123")

        mgr.update_profile(acct["account_id"], full_name="John Doe", email="john@test.com")
        updated = mgr.get_account(acct["account_id"])
        assert updated["full_name"] == "John Doe"
        assert updated["email"] == "john@test.com"

    def test_deactivate_account(self):
        """Deactivating sets is_active=0."""
        from core.juris_kai.accounts import get_account_manager
        mgr = get_account_manager()
        acct = mgr.get_or_create("123")
        mgr.deactivate(acct["account_id"])

        updated = mgr.get_account(acct["account_id"])
        assert updated["is_active"] == 0

    def test_subscription_tiers_exist(self):
        """All required subscription tiers are defined."""
        from core.juris_kai.accounts import SUBSCRIPTION_TIERS
        assert "free_trial" in SUBSCRIPTION_TIERS
        assert "monthly_basic" in SUBSCRIPTION_TIERS
        assert "monthly_pro" in SUBSCRIPTION_TIERS
        assert "annual_pro" in SUBSCRIPTION_TIERS

        trial = SUBSCRIPTION_TIERS["free_trial"]
        assert trial["price_ghs"] == 0
        assert trial["duration_days"] == 7

    def test_set_subscription(self):
        """Upgrading subscription changes tier and extends end date."""
        from core.juris_kai.accounts import get_account_manager
        mgr = get_account_manager()
        acct = mgr.get_or_create("123")

        mgr.set_subscription(acct["account_id"], "monthly_pro")
        sub = mgr.get_active_subscription(acct["account_id"])

        assert sub["tier"] == "monthly_pro"
        assert sub["tier_name"] == "Professional Monthly"
        assert sub["is_active"] is True
        assert sub["limits"]["max_queries_per_day"] == 500
        assert sub["limits"]["max_documents_per_month"] == 50

    def test_invalid_tier_rejected(self):
        """Setting an unknown tier returns False."""
        from core.juris_kai.accounts import get_account_manager
        mgr = get_account_manager()
        acct = mgr.get_or_create("123")

        result = mgr.set_subscription(acct["account_id"], "nonexistent_tier")
        assert result is False


class TestUsageLimits:
    """Daily query and monthly document limit tests."""

    @pytest.fixture(autouse=True)
    def setup(self):
        import sqlite3
        db_path = Path(os.environ["JURIS_KAI_DB_DIR"]) / "juris_kai_accounts.db"
        if db_path.exists():
            db_path.unlink()
        import core.juris_kai.accounts as accts
        accts._account_manager = None
        yield
        if db_path.exists():
            db_path.unlink()

    def test_query_limit_free_trial(self):
        """Free trial users have limited daily queries."""
        from core.juris_kai.accounts import get_account_manager, SUBSCRIPTION_TIERS
        mgr = get_account_manager()
        acct = mgr.get_or_create("123")

        limit_check = mgr.check_query_limit(acct["account_id"])
        assert limit_check["allowed"] is True
        assert limit_check["limit"] == SUBSCRIPTION_TIERS["free_trial"]["max_queries_per_day"]
        assert limit_check["remaining"] == limit_check["limit"]

    def test_query_limit_decrements(self):
        """Recording queries decrements remaining count."""
        from core.juris_kai.accounts import get_account_manager
        mgr = get_account_manager()
        acct = mgr.get_or_create("123")

        initial = mgr.check_query_limit(acct["account_id"])
        mgr.record_query(acct["account_id"])
        after = mgr.check_query_limit(acct["account_id"])

        assert after["remaining"] == initial["remaining"] - 1

    def test_query_limit_pro_tier_higher(self):
        """Professional tier has higher limits."""
        from core.juris_kai.accounts import get_account_manager
        mgr = get_account_manager()
        acct = mgr.get_or_create("123")
        mgr.set_subscription(acct["account_id"], "monthly_pro")

        limit_check = mgr.check_query_limit(acct["account_id"])
        assert limit_check["limit"] == 500

    def test_document_limit_free_trial(self):
        """Free trial has limited document analyses."""
        from core.juris_kai.accounts import get_account_manager
        mgr = get_account_manager()
        acct = mgr.get_or_create("123")

        doc_check = mgr.check_document_limit(acct["account_id"])
        assert doc_check["allowed"] is True
        assert doc_check["limit"] == 3

    def test_bill_document_analysis(self):
        """Document analysis billing creates a record and reports cost."""
        from core.juris_kai.accounts import get_account_manager
        mgr = get_account_manager()
        acct = mgr.get_or_create("123")

        result = mgr.bill_document_analysis(acct["account_id"], "contract.pdf", page_count=5)
        assert result["cost_ghs"] == 10.0  # 5 pages × GH₵2
        assert result["document_name"] == "contract.pdf"
        assert "analysis_id" in result


class TestHubtelPayments:
    """Hubtel payment integration tests."""

    def test_payment_client_test_mode(self):
        """In test mode, payments are simulated successfully."""
        from core.juris_kai.payments import get_payment_client
        client = get_payment_client()

        result = client.request_payment(
            amount_ghs=50,
            customer_name="Test User",
            customer_phone="0244123456",
            description="Monthly Basic subscription",
            payment_id="test-payment-001",
        )

        assert result["success"] is True
        assert result["test_mode"] is True
        assert result["amount_ghs"] == 50
        assert "hubtel_transaction_id" in result

    def test_payment_client_not_configured_without_creds(self):
        """Client reports unconfigured when env vars are missing."""
        import core.juris_kai.payments as pmts
        pmts._payment_client = None
        # Temporarily unset credentials
        old_id = pmts.HUBTEL_CLIENT_ID
        pmts.HUBTEL_CLIENT_ID = ""
        try:
            client = pmts.get_payment_client()
            assert client.is_configured() is False
        finally:
            pmts.HUBTEL_CLIENT_ID = old_id
            pmts._payment_client = None


class TestDashboard:
    """Dashboard management endpoint tests."""

    @pytest.fixture(autouse=True)
    def setup(self):
        import sqlite3
        db_path = Path(os.environ["JURIS_KAI_DB_DIR"]) / "juris_kai_accounts.db"
        if db_path.exists():
            db_path.unlink()
        import core.juris_kai.accounts as accts
        accts._account_manager = None
        yield
        if db_path.exists():
            db_path.unlink()

    def test_get_dashboard_stats(self):
        """Dashboard stats return aggregate data."""
        from core.juris_kai.dashboard import get_dashboard_stats
        from core.juris_kai.accounts import get_account_manager
        mgr = get_account_manager()
        mgr.get_or_create("111", "User One")
        mgr.get_or_create("222", "User Two")

        stats = get_dashboard_stats()
        juris = stats["juris_kai"]
        assert juris["total_accounts"] == 2
        assert juris["active_accounts"] == 2
        assert "subscription_tiers" in juris

    def test_list_accounts_pagination(self):
        """Account listing supports pagination."""
        from core.juris_kai.dashboard import list_accounts
        from core.juris_kai.accounts import get_account_manager
        mgr = get_account_manager()
        for i in range(5):
            mgr.get_or_create(str(i), f"User {i}")

        result = list_accounts(page=1, per_page=3)
        assert result["total"] == 5
        assert len(result["accounts"]) == 3
        assert result["total_pages"] == 2

    def test_update_subscription_admin(self):
        """Admin can update a user's subscription."""
        from core.juris_kai.dashboard import update_subscription
        from core.juris_kai.accounts import get_account_manager
        mgr = get_account_manager()
        acct = mgr.get_or_create("123")

        result = update_subscription(acct["account_id"], "monthly_pro")
        assert result["success"] is True

        sub = mgr.get_active_subscription(acct["account_id"])
        assert sub["tier"] == "monthly_pro"

    def test_get_account_detail(self):
        """Account detail includes usage and subscription info."""
        from core.juris_kai.dashboard import get_account_detail
        from core.juris_kai.accounts import get_account_manager
        mgr = get_account_manager()
        acct = mgr.get_or_create("123")

        detail = get_account_detail(acct["account_id"])
        assert detail is not None
        assert "subscription" in detail
        assert "usage" in detail
        assert detail["usage"]["queries"]["allowed"] is True


class TestDisclaimerFlow:
    """New user disclaimer acceptance flow."""

    @pytest.fixture(autouse=True)
    def setup(self):
        import sqlite3
        db_path = Path(os.environ["JURIS_KAI_DB_DIR"]) / "juris_kai_accounts.db"
        if db_path.exists():
            db_path.unlink()
        import core.juris_kai.accounts as accts
        accts._account_manager = None
        yield
        if db_path.exists():
            db_path.unlink()

    def _get_text(self, response: dict) -> str:
        """Extract text from new dict-format or legacy str-format response."""
        if isinstance(response, str):
            return response
        return str(response.get("text", ""))

    def test_new_user_gets_disclaimer_message(self):
        """Bot returns disclaimer for new users."""
        from core.juris_kai.bot import handle_message

        response = handle_message({"chat_id": "999", "text": "/help"})
        assert "not a lawyer" in self._get_text(response).lower()

    def test_existing_user_without_disclaimer_gets_prompt(self):
        """User who hasn't accepted disclaimer gets prompted."""
        from core.juris_kai.accounts import get_account_manager
        mgr = get_account_manager()
        mgr.get_or_create("888")

        from core.juris_kai.bot import handle_message
        response = handle_message({"chat_id": "888", "text": "What is contract law?"})
        text = self._get_text(response)
        assert "acknowledge the disclaimer" in text.lower()
        # New bot uses inline keyboard button instead of text prompt
        assert "button" in text.lower() or "i understand" in text.lower()

    def test_start_command_accepts_disclaimer(self):
        """The /start command accepts the disclaimer."""
        from core.juris_kai.accounts import get_account_manager
        mgr = get_account_manager()
        mgr.get_or_create("777")

        from core.juris_kai.bot import handle_message
        response = handle_message({"chat_id": "777", "text": "/start"})
        assert "welcome" in self._get_text(response).lower()


class TestSecurityBoundary:
    """Juris Kai must not import operational modules."""

    def test_no_operational_imports_in_juris_kai(self):
        """Verify core.juris_kai modules don't import forbidden operational modules."""
        import subprocess
        import sys

        forbidden = {"core.build_manager", "core.approval", "core.deployment_manager"}
        modules_to_check = [
            "core.juris_kai.accounts",
            "core.juris_kai.bot",
            "core.juris_kai.commands",
            "core.juris_kai.payments",
            "core.juris_kai.dashboard",
            "core.juris_kai.session",
            "core.juris_kai.prompt",
            "core.juris_kai.menus",
        ]

        for mod_name in modules_to_check:
            script = (
                "import sys; "
                f"__import__('{mod_name}'); "
                "forbidden = {'core.build_manager', 'core.approval', 'core.deployment_manager'}; "
                "leaked = forbidden & set(sys.modules); "
                "print(','.join(sorted(leaked)))"
            )

            result = subprocess.run(
                [sys.executable, "-c", script],
                cwd=str(Path(__file__).resolve().parent.parent),
                capture_output=True, text=True, timeout=30,
                env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parent.parent)},
            )
            output = result.stdout.strip()
            # Filter out Qwen3 routing override noise
            output_lines = [
                l for l in output.split("\n")
                if "Qwen3" not in l and "\U0001f527" not in l and "\U0001f504" not in l
            ]
            leaked_str = "\n".join(output_lines).strip()
            assert not leaked_str, f"{mod_name} leaked: {leaked_str}"
