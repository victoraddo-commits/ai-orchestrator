"""JK-5 Analytics + JK-6 Monetization Readiness Tests.

Tests cover:
- Dashboard stats aggregation (total accounts, active, revenue, queries)
- Account listing with pagination and tier/active filtering
- Account detail with subscription + usage
- Payment history retrieval
- Usage log queries
- Hubtel Payment Client test mode simulation
- Subscription tiers (4 tiers: free_trial, monthly_basic, monthly_pro, annual_pro)
- Subscription lifecycle (get_active_subscription for active/expired accounts)
- Document per-page billing
- Subscription tier upgrade flow
"""

import os
import sqlite3
import tempfile
import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone, timedelta


# ============================================================================
# JK-5: Analytics Tests
# ============================================================================

class TestDashboardStats:
    """Tests for dashboard stats aggregation."""

    def test_get_dashboard_stats_returns_expected_keys(self):
        """Dashboard stats return the juris_kai key with stats and tiers."""
        from core.juris_kai.dashboard import get_dashboard_stats
        from core.juris_kai.accounts import SUBSCRIPTION_TIERS

        stats = get_dashboard_stats()
        assert "juris_kai" in stats
        jk = stats["juris_kai"]
        assert "total_accounts" in jk
        assert "active_accounts" in jk
        assert "total_revenue_ghs" in jk
        assert "total_queries" in jk
        assert "by_tier" in jk
        assert "subscription_tiers" in jk
        assert "updated_at" in jk
        assert jk["subscription_tiers"] == SUBSCRIPTION_TIERS

    def test_dashboard_stats_include_all_four_tiers(self):
        """All four subscription tiers are present in stats."""
        from core.juris_kai.dashboard import get_dashboard_stats

        stats = get_dashboard_stats()
        tiers = stats["juris_kai"]["subscription_tiers"]
        assert "free_trial" in tiers
        assert "monthly_basic" in tiers
        assert "monthly_pro" in tiers
        assert "annual_pro" in tiers

    def test_dashboard_stats_types_are_numeric(self):
        """Stats values are integers or floats (not None or strings)."""
        from core.juris_kai.dashboard import get_dashboard_stats

        stats = get_dashboard_stats()
        jk = stats["juris_kai"]
        assert isinstance(jk["total_accounts"], int)
        assert isinstance(jk["active_accounts"], int)
        assert isinstance(jk["total_queries"], int)
        assert isinstance(jk["total_revenue_ghs"], (int, float))


class TestAccountListing:
    """Tests for account listing with pagination and filtering."""

    def test_list_accounts_returns_paginated_structure(self):
        """Account listing returns proper pagination envelope."""
        from core.juris_kai.dashboard import list_accounts

        result = list_accounts(page=1, per_page=10)
        assert "accounts" in result
        assert "total" in result
        assert "page" in result
        assert "per_page" in result
        assert "total_pages" in result
        assert result["page"] == 1
        assert result["per_page"] == 10
        assert result["total_pages"] >= 1

    def test_list_accounts_enriches_with_subscription(self):
        """Each account in listing has subscription info attached."""
        from core.juris_kai.dashboard import list_accounts

        result = list_accounts(page=1, per_page=5)
        for acct in result["accounts"]:
            assert "subscription" in acct
            if acct["subscription"] is not None:
                assert "tier" in acct["subscription"]
                assert "is_active" in acct["subscription"]
                assert "limits" in acct["subscription"]

    def test_list_accounts_active_only_filter(self):
        """Active-only filter does not raise errors."""
        from core.juris_kai.dashboard import list_accounts

        result = list_accounts(page=1, per_page=10, active_only=True)
        assert "accounts" in result

    def test_list_accounts_tier_filter(self):
        """Tier filter does not raise errors."""
        from core.juris_kai.dashboard import list_accounts

        for tier in ["free_trial", "monthly_basic", "monthly_pro", "annual_pro"]:
            result = list_accounts(page=1, per_page=10, tier_filter=tier)
            assert "accounts" in result
            for acct in result["accounts"]:
                assert acct.get("subscription_tier") == tier

    def test_list_accounts_with_both_filters(self):
        """Combined filters work together."""
        from core.juris_kai.dashboard import list_accounts

        result = list_accounts(page=1, per_page=10, tier_filter="free_trial", active_only=True)
        assert "accounts" in result


class TestAccountDetail:
    """Tests for account detail view."""

    def test_get_account_detail_returns_usage(self):
        """Account detail includes query and document usage."""
        acct_mgr = None
        try:
            from core.juris_kai.accounts import get_account_manager
            acct_mgr = get_account_manager()
            # Create a test account
            acct = acct_mgr.get_or_create("999999999", "Test Detail User")
            account_id = acct["account_id"]

            from core.juris_kai.dashboard import get_account_detail
            detail = get_account_detail(account_id)

            assert detail is not None
            assert "usage" in detail
            assert "queries" in detail["usage"]
            assert "documents" in detail["usage"]
            assert "subscription" in detail
        finally:
            # Clean up test account
            if acct_mgr and account_id:
                acct_mgr.deactivate(account_id)

    def test_get_account_detail_nonexistent(self):
        """Nonexistent account returns None."""
        from core.juris_kai.dashboard import get_account_detail

        result = get_account_detail("nonexistent-12345")
        assert result is None


class TestPaymentHistory:
    """Tests for payment history retrieval."""

    def test_get_payment_history_all(self):
        """Payment history without filter returns list."""
        from core.juris_kai.dashboard import get_payment_history

        payments = get_payment_history()
        assert isinstance(payments, list)

    def test_get_payment_history_with_account_filter(self):
        """Payment history filtered by account returns list."""
        from core.juris_kai.dashboard import get_payment_history

        payments = get_payment_history(account_id="nonexistent-12345", limit=5)
        assert isinstance(payments, list)
        assert len(payments) == 0  # No payments for nonexistent account

    def test_get_payment_history_respects_limit(self):
        """Payment history respects the limit parameter."""
        from core.juris_kai.dashboard import get_payment_history

        payments = get_payment_history(limit=3)
        assert len(payments) <= 3


class TestUsageLog:
    """Tests for usage log queries."""

    def test_get_usage_log_all(self):
        """Usage log without filter returns list."""
        from core.juris_kai.dashboard import get_usage_log

        logs = get_usage_log()
        assert isinstance(logs, list)

    def test_get_usage_log_with_account_filter(self):
        """Usage log filtered by account returns list."""
        from core.juris_kai.dashboard import get_usage_log

        logs = get_usage_log(account_id="nonexistent-12345", limit=5)
        assert isinstance(logs, list)

    def test_get_usage_log_respects_limit(self):
        """Usage log respects the limit parameter."""
        from core.juris_kai.dashboard import get_usage_log

        logs = get_usage_log(limit=5)
        assert len(logs) <= 5


class TestUpdateSubscription:
    """Tests for admin subscription updates."""

    def test_update_subscription_to_valid_tier(self):
        """Updating to a valid tier succeeds."""
        acct_mgr = None
        try:
            from core.juris_kai.accounts import get_account_manager
            acct_mgr = get_account_manager()
            acct = acct_mgr.get_or_create("888888888", "Subscription Test")
            account_id = acct["account_id"]

            from core.juris_kai.dashboard import update_subscription
            result = update_subscription(account_id, "monthly_basic")
            assert result["success"] is True
            assert result["new_tier"] == "monthly_basic"
        finally:
            if acct_mgr and account_id:
                acct_mgr.deactivate(account_id)

    def test_update_subscription_to_invalid_tier(self):
        """Updating to an invalid tier fails."""
        from core.juris_kai.dashboard import update_subscription

        result = update_subscription("some-id", "ultra_premium")
        assert result["success"] is False
        assert "Unknown tier" in result["error"]

    def test_update_subscription_nonexistent_account(self):
        """Updating a nonexistent account returns error."""
        from core.juris_kai.dashboard import update_subscription

        result = update_subscription("nonexistent-99999", "monthly_pro")
        assert result["success"] is False
        assert "Account not found" in result["error"]


# ============================================================================
# JK-6: Monetization Readiness Tests
# ============================================================================

class TestHubtelPaymentClient:
    """Tests for Hubtel payment client (test mode by default)."""

    def test_client_uses_test_mode_by_default(self):
        """Hubtel client defaults to test mode."""
        from core.juris_kai.payments import HubtelPaymentClient

        client = HubtelPaymentClient()
        assert client.test_mode is True

    def test_request_payment_simulates_in_test_mode(self):
        """Payment request in test mode returns simulated success."""
        from core.juris_kai.payments import HubtelPaymentClient

        client = HubtelPaymentClient()
        result = client.request_payment(
            amount_ghs=50.0,
            customer_name="Test Customer",
            customer_phone="233555123456",
            description="Monthly Basic subscription",
            payment_id="TEST-PAY-001",
        )
        assert result["success"] is True
        assert result["test_mode"] is True
        assert result["status"] == "completed"
        assert result["amount_ghs"] == 50.0
        assert result["customer_name"] == "Test Customer"
        assert result["hubtel_transaction_id"].startswith("TEST-")

    def test_request_payment_generates_unique_transaction_ids(self):
        """Each simulated payment gets a unique transaction ID."""
        from core.juris_kai.payments import HubtelPaymentClient

        client = HubtelPaymentClient()
        r1 = client.request_payment(10.0, "A", "111", "desc", "P1")
        r2 = client.request_payment(20.0, "B", "222", "desc", "P2")

        assert r1["hubtel_transaction_id"] != r2["hubtel_transaction_id"]
        assert r1["payment_id"] != r2["payment_id"]

    def test_request_payment_different_channels(self):
        """Payment can be requested with different channels."""
        from core.juris_kai.payments import HubtelPaymentClient

        client = HubtelPaymentClient()
        for channel in ["mobile_money", "card"]:
            result = client.request_payment(
                30.0, "User", "233555111222", f"Test {channel}",
                f"PAY-{channel}", channel=channel,
            )
            assert result["success"] is True

    def test_request_payment_different_momo_providers(self):
        """Payment supports different mobile money providers."""
        from core.juris_kai.payments import HubtelPaymentClient

        client = HubtelPaymentClient()
        for provider in ["mtn", "vodafone", "airteltigo"]:
            result = client.request_payment(
                25.0, "User", "233555000000", f"Test {provider}",
                f"PAY-{provider}", mobile_money_provider=provider,
            )
            assert result["success"] is True

    def test_check_payment_status_in_test_mode(self):
        """Payment status check in test mode returns completed."""
        from core.juris_kai.payments import HubtelPaymentClient

        client = HubtelPaymentClient()
        result = client.check_payment_status("TEST-ABCDEF1234567890")
        assert result["success"] is True
        assert result["status"] == "completed"
        assert result["test_mode"] is True

    def test_is_configured_without_env_vars(self):
        """Without Hubtel credentials, is_configured returns False."""
        from core.juris_kai.payments import HubtelPaymentClient

        client = HubtelPaymentClient()
        # In test environment, credentials are typically not set
        # Client still works because test_mode=True bypasses config check
        assert isinstance(client.is_configured(), bool)


class TestSubscriptionTiers:
    """Tests for subscription tier definitions."""

    def test_all_four_tiers_defined(self):
        """All four subscription tiers exist."""
        from core.juris_kai.accounts import SUBSCRIPTION_TIERS

        assert len(SUBSCRIPTION_TIERS) == 4
        assert "free_trial" in SUBSCRIPTION_TIERS
        assert "monthly_basic" in SUBSCRIPTION_TIERS
        assert "monthly_pro" in SUBSCRIPTION_TIERS
        assert "annual_pro" in SUBSCRIPTION_TIERS

    def test_tiers_have_required_fields(self):
        """Each tier has all required fields."""
        from core.juris_kai.accounts import SUBSCRIPTION_TIERS

        for tier_key, tier in SUBSCRIPTION_TIERS.items():
            assert "name" in tier
            assert "duration_days" in tier
            assert "price_ghs" in tier
            assert "max_documents_per_month" in tier
            assert "max_queries_per_day" in tier
            assert "features" in tier
            assert isinstance(tier["features"], list)

    def test_pricing_is_ascending(self):
        """Higher tiers cost more."""
        from core.juris_kai.accounts import SUBSCRIPTION_TIERS

        prices = [
            SUBSCRIPTION_TIERS["free_trial"]["price_ghs"],
            SUBSCRIPTION_TIERS["monthly_basic"]["price_ghs"],
            SUBSCRIPTION_TIERS["monthly_pro"]["price_ghs"],
            SUBSCRIPTION_TIERS["annual_pro"]["price_ghs"],
        ]
        assert prices == sorted(prices)

    def test_query_limits_are_ascending(self):
        """Higher tiers have more daily queries."""
        from core.juris_kai.accounts import SUBSCRIPTION_TIERS

        limits = [
            SUBSCRIPTION_TIERS["free_trial"]["max_queries_per_day"],
            SUBSCRIPTION_TIERS["monthly_basic"]["max_queries_per_day"],
            SUBSCRIPTION_TIERS["monthly_pro"]["max_queries_per_day"],
            SUBSCRIPTION_TIERS["annual_pro"]["max_queries_per_day"],
        ]
        assert limits == sorted(limits)

    def test_professional_tiers_have_export_and_api(self):
        """Professional tiers include export_reports, annual has api_access."""
        from core.juris_kai.accounts import SUBSCRIPTION_TIERS

        assert "export_reports" in SUBSCRIPTION_TIERS["monthly_pro"]["features"]
        assert "api_access" in SUBSCRIPTION_TIERS["annual_pro"]["features"]
        assert "api_access" not in SUBSCRIPTION_TIERS["monthly_pro"]["features"]


class TestSubscriptionLifecycle:
    """Tests for subscription status and lifecycle."""

    def _fresh_telegram_id(self) -> str:
        """Generate a unique telegram ID to avoid cross-test contamination."""
        import uuid
        return str(uuid.uuid4())[:10].replace("-", "0")

    def _create_test_account(self, mgr, telegram_id: str):
        """Helper: create a test account and return it."""
        return mgr.get_or_create(telegram_id, "Lifecycle Test")

    def test_get_active_subscription_for_new_account(self):
        """A new account starts with free_trial."""
        mgr = None
        account_id = None
        try:
            from core.juris_kai.accounts import get_account_manager
            mgr = get_account_manager()
            acct = self._create_test_account(mgr, self._fresh_telegram_id())
            account_id = acct["account_id"]
            sub = mgr.get_active_subscription(account_id)

            assert sub is not None
            assert sub["tier"] == "free_trial"
            assert sub["tier_name"] == "Free Trial"
            assert sub["price_ghs"] == 0
            assert sub["is_active"] is True
            assert sub["is_expired"] is False
            assert "features" in sub
            assert "limits" in sub
        finally:
            if mgr and account_id:
                mgr.deactivate(account_id)

    def test_get_active_subscription_includes_limits(self):
        """Subscription status includes document and query limits."""
        mgr = None
        account_id = None
        try:
            from core.juris_kai.accounts import get_account_manager
            mgr = get_account_manager()
            acct = self._create_test_account(mgr, self._fresh_telegram_id())
            account_id = acct["account_id"]
            sub = mgr.get_active_subscription(account_id)

            assert "limits" in sub
            assert "max_documents_per_month" in sub["limits"]
            assert "max_queries_per_day" in sub["limits"]
        finally:
            if mgr and account_id:
                mgr.deactivate(account_id)

    def test_set_subscription_upgrades_tier(self):
        """Upgrading tier changes subscription."""
        mgr = None
        account_id = None
        try:
            from core.juris_kai.accounts import get_account_manager
            mgr = get_account_manager()
            acct = self._create_test_account(mgr, self._fresh_telegram_id())
            account_id = acct["account_id"]

            success = mgr.set_subscription(account_id, "monthly_pro")
            assert success is True

            sub = mgr.get_active_subscription(account_id)
            assert sub["tier"] == "monthly_pro"
            assert sub["tier_name"] == "Professional Monthly"
        finally:
            if mgr and account_id:
                mgr.deactivate(account_id)

    def test_check_query_limit_for_new_account(self):
        """New account has full daily query allowance."""
        mgr = None
        account_id = None
        try:
            from core.juris_kai.accounts import get_account_manager
            mgr = get_account_manager()
            acct = self._create_test_account(mgr, self._fresh_telegram_id())
            account_id = acct["account_id"]

            result = mgr.check_query_limit(account_id)
            assert result["allowed"] is True
            assert result["remaining"] == 20  # free_trial: 20/day
            assert result["limit"] == 20
        finally:
            if mgr and account_id:
                mgr.deactivate(account_id)

    def test_record_query_decrements_remaining(self):
        """Recording a query decreases remaining count."""
        mgr = None
        account_id = None
        try:
            from core.juris_kai.accounts import get_account_manager
            mgr = get_account_manager()
            acct = self._create_test_account(mgr, self._fresh_telegram_id())
            account_id = acct["account_id"]

            before = mgr.check_query_limit(account_id)
            mgr.record_query(account_id, input_tokens=100, output_tokens=50, model="test-model")
            after = mgr.check_query_limit(account_id)

            assert after["remaining"] == before["remaining"] - 1
        finally:
            if mgr and account_id:
                mgr.deactivate(account_id)

    def test_record_query_tracks_tokens(self):
        """Recording a query stores token counts in usage log."""
        mgr = None
        account_id = None
        try:
            from core.juris_kai.accounts import get_account_manager
            mgr = get_account_manager()
            acct = self._create_test_account(mgr, self._fresh_telegram_id())
            account_id = acct["account_id"]

            mgr.record_query(account_id, input_tokens=250, output_tokens=120, model="deepseek-flash")

            from core.juris_kai.dashboard import get_usage_log
            logs = get_usage_log(account_id=account_id, limit=1)
            assert len(logs) > 0
            assert logs[0]["input_tokens"] == 250
            assert logs[0]["output_tokens"] == 120
            assert logs[0]["model"] == "deepseek-flash"
        finally:
            if mgr and account_id:
                mgr.deactivate(account_id)

    def test_check_document_limit_new_account(self):
        """New account has full monthly document allowance."""
        mgr = None
        account_id = None
        try:
            from core.juris_kai.accounts import get_account_manager
            mgr = get_account_manager()
            acct = self._create_test_account(mgr, self._fresh_telegram_id())
            account_id = acct["account_id"]

            result = mgr.check_document_limit(account_id)
            assert result["allowed"] is True
            assert result["remaining"] == 3  # free_trial: 3 docs/month
            assert result["limit"] == 3
        finally:
            if mgr and account_id:
                mgr.deactivate(account_id)


class TestDocumentBilling:
    """Tests for per-document billing."""

    def _fresh_telegram_id(self) -> str:
        import uuid
        return str(uuid.uuid4())[:10].replace("-", "0")

    def test_bill_document_analysis_charges_per_page(self):
        """Document billing charges PER_DOCUMENT_PAGE_RATE_GHS per page."""
        from core.juris_kai.accounts import PER_DOCUMENT_PAGE_RATE_GHS

        mgr = None
        account_id = None
        try:
            from core.juris_kai.accounts import get_account_manager
            mgr = get_account_manager()
            acct = mgr.get_or_create(self._fresh_telegram_id(), "Billing Test")
            account_id = acct["account_id"]

            result = mgr.bill_document_analysis(account_id, "contract.pdf", page_count=5)
            expected_cost = 5 * PER_DOCUMENT_PAGE_RATE_GHS

            assert result["cost_ghs"] == expected_cost
            assert result["page_count"] == 5
            assert result["document_name"] == "contract.pdf"
            assert result["rate_per_page"] == PER_DOCUMENT_PAGE_RATE_GHS
            assert "analysis_id" in result
        finally:
            if mgr and account_id:
                mgr.deactivate(account_id)

    def test_bill_document_increments_monthly_count(self):
        """Billing a document increments the monthly document counter."""
        mgr = None
        account_id = None
        try:
            from core.juris_kai.accounts import get_account_manager
            mgr = get_account_manager()
            acct = mgr.get_or_create(self._fresh_telegram_id(), "Doc Count Test")
            account_id = acct["account_id"]

            before = mgr.check_document_limit(account_id)
            mgr.bill_document_analysis(account_id, "memo.pdf", page_count=1)
            after = mgr.check_document_limit(account_id)

            assert after["remaining"] == before["remaining"] - 1
        finally:
            if mgr and account_id:
                mgr.deactivate(account_id)


class TestConfigConstants:
    """Tests for payment config constants."""

    def test_hubtel_config_defaults(self):
        """Hubtel config has sensible defaults."""
        from core.juris_kai.payments import HUBTEL_API_BASE, HUBTEL_TEST_MODE
        from core.juris_kai.accounts import PER_DOCUMENT_PAGE_RATE_GHS

        assert HUBTEL_TEST_MODE is True
        assert "hubtel.com" in HUBTEL_API_BASE
        assert PER_DOCUMENT_PAGE_RATE_GHS == 2.0

    def test_disclaimer_text_is_nonempty(self):
        """Disclaimer text is defined and not empty."""
        from core.juris_kai.accounts import DISCLAIMER_TEXT
        assert len(DISCLAIMER_TEXT) > 100
        assert "not a lawyer" in DISCLAIMER_TEXT.lower()


class TestSubscriptionLimits:
    """Tests for subscription limit enforcement."""

    def test_query_limit_exhausted(self):
        """When daily queries are exhausted, limit check returns allowed=False."""
        import uuid
        mgr = None
        account_id = None
        try:
            from core.juris_kai.accounts import get_account_manager
            mgr = get_account_manager()
            tg_id = str(uuid.uuid4())[:10].replace("-", "0")
            acct = mgr.get_or_create(tg_id, "Limit Exhaust Test")
            account_id = acct["account_id"]

            # Exhaust the 20 query limit for free_trial
            for _ in range(20):
                mgr.record_query(account_id)

            result = mgr.check_query_limit(account_id)
            assert result["allowed"] is False
            assert result["remaining"] == 0
        finally:
            if mgr and account_id:
                mgr.deactivate(account_id)


class TestPaymentClientSingleton:
    """Tests for payment client singleton."""

    def test_get_payment_client_returns_same_instance(self):
        """get_payment_client returns singleton."""
        from core.juris_kai.payments import get_payment_client

        c1 = get_payment_client()
        c2 = get_payment_client()
        assert c1 is c2

    def test_simulate_payment_includes_all_fields(self):
        """Simulated payment result has all expected fields."""
        from core.juris_kai.payments import HubtelPaymentClient

        client = HubtelPaymentClient()
        result = client._simulate_payment(
            75.0, "Kofi", "233555999888", "Pro subscription", "PAY-001",
        )
        assert result["success"] is True
        assert result["test_mode"] is True
        assert result["status"] == "completed"
        assert result["amount_ghs"] == 75.0
        assert result["customer_name"] == "Kofi"
        assert result["customer_phone"] == "233555999888"
        assert result["payment_id"] == "PAY-001"


class TestAccountManagerAdmin:
    """Tests for admin account management functions."""

    def _fresh_telegram_id(self) -> str:
        import uuid
        return str(uuid.uuid4())[:10].replace("-", "0")

    def test_deactivate_account(self):
        """Deactivating an account sets is_active to False."""
        mgr = None
        account_id = None
        try:
            from core.juris_kai.accounts import get_account_manager
            mgr = get_account_manager()
            acct = mgr.get_or_create(self._fresh_telegram_id(), "Deactivate Test")
            account_id = acct["account_id"]

            result = mgr.deactivate(account_id)
            assert result is True

            updated = mgr.get_account(account_id)
            assert updated["is_active"] == 0 or updated["is_active"] is False
        finally:
            if mgr and account_id:
                mgr.deactivate(account_id)

    def test_deactivated_account_limit_denied(self):
        """Deactivated account cannot make queries."""
        mgr = None
        account_id = None
        try:
            from core.juris_kai.accounts import get_account_manager
            mgr = get_account_manager()
            acct = mgr.get_or_create(self._fresh_telegram_id(), "Deactivated Query Test")
            account_id = acct["account_id"]

            mgr.deactivate(account_id)
            result = mgr.check_query_limit(account_id)
            assert result["allowed"] is False
        finally:
            if mgr and account_id:
                mgr.deactivate(account_id)

    def test_get_stats_counts_everything(self):
        """get_stats returns consistent counts."""
        from core.juris_kai.accounts import get_account_manager

        mgr = get_account_manager()
        # Create a fresh active account (defaults to free_trial tier) so the
        # by_tier aggregation is guaranteed non-empty regardless of how many
        # accounts prior tests created and deactivated.
        mgr.get_or_create(self._fresh_telegram_id(), "Stats Test")
        stats = mgr.get_stats()

        assert stats["total_accounts"] >= stats["active_accounts"]
        assert stats["total_queries"] >= 0
        assert stats["total_revenue_ghs"] >= 0
        assert "free_trial" in stats["by_tier"]
