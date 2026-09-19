"""TDD tests for Paystack Plans/Subscriptions + the Juris plan map.

HTTP is always mocked via a fake ``httpx.Client``; no real API call is made.
"""

from __future__ import annotations

import json

import pytest

from core.juris_kai import plans as juris_plans
from core.payments.paystack import PaystackError, PaystackProvider, USER_AGENT


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload


class FakeClient:
    """Records requests and returns queued responses."""

    def __init__(self, responses=None):
        self.calls = []
        self.responses = list(responses or [])

    def request(self, method, url, json=None, headers=None, timeout=None):
        self.calls.append({"method": method, "url": url, "json": json,
                           "headers": headers or {}, "timeout": timeout})
        if self.responses:
            return self.responses.pop(0)
        return FakeResponse({"status": True, "data": {}})

    def close(self):
        pass


def _provider(client, secret="sk_test_dummy"):
    return PaystackProvider(secret_key=secret, mode="test", http_client=client)


# ── provider HTTP contract ────────────────────────────────────────────────

class TestProviderPlans:
    def test_create_plan_posts_and_sets_user_agent(self):
        client = FakeClient([FakeResponse({"status": True, "data": {
            "id": 7, "plan_code": "PLN_abc", "name": "Juris Kai — Basic",
            "amount": 5000, "interval": "monthly", "currency": "GHS"}})])
        prov = _provider(client)
        plan = prov.create_plan("Juris Kai — Basic", 5000, "monthly")
        call = client.calls[0]
        assert call["method"] == "POST"
        assert call["url"].endswith("/plan")
        assert call["json"]["amount"] == 5000
        assert call["json"]["interval"] == "monthly"
        assert call["json"]["currency"] == "GHS"
        # Paystack 403s a bare User-Agent — ours must be sent.
        assert call["headers"]["User-Agent"] == USER_AGENT
        assert "urllib" not in call["headers"]["User-Agent"].lower()
        assert plan["plan_code"] == "PLN_abc"

    def test_create_plan_validates(self):
        prov = _provider(FakeClient())
        with pytest.raises(ValueError):
            prov.create_plan("", 5000)
        with pytest.raises(ValueError):
            prov.create_plan("Basic", 0)
        with pytest.raises(ValueError):
            prov.create_plan("Basic", 5000, interval="fortnightly")

    def test_list_plans_returns_list(self):
        client = FakeClient([FakeResponse({"status": True, "data": [
            {"plan_code": "PLN_1"}, {"plan_code": "PLN_2"}]})])
        plans = _provider(client).list_plans()
        assert [p["plan_code"] for p in plans] == ["PLN_1", "PLN_2"]
        assert "/plan?perPage=" in client.calls[0]["url"]

    def test_update_plan_puts(self):
        client = FakeClient([FakeResponse({"status": True, "data": {
            "plan_code": "PLN_1", "amount": 6000}})])
        prov = _provider(client)
        out = prov.update_plan("PLN_1", amount=6000)
        assert client.calls[0]["method"] == "PUT"
        assert client.calls[0]["json"] == {"amount": 6000}
        assert out["amount"] == 6000

    def test_error_status_raises(self):
        client = FakeClient([FakeResponse({"status": False,
                                           "message": "plan not found"}, 200)])
        with pytest.raises(PaystackError):
            _provider(client).fetch_plan("PLN_missing")

    def test_create_plan_error_raises(self):
        client = FakeClient([FakeResponse({"status": False,
                                           "message": "invalid key"}, 403)])
        with pytest.raises(PaystackError):
            _provider(client).create_plan("Basic", 5000)

    def test_initialize_attaches_plan_and_ua(self):
        client = FakeClient([FakeResponse({"status": True, "data": {
            "authorization_url": "https://checkout.paystack.com/x",
            "access_code": "acc", "reference": "JURIS-P1"}})])
        prov = _provider(client)
        prov.initialize(5000, email="a@b.com", reference="JURIS-P1",
                        plan="PLN_T1")
        body = client.calls[0]["json"]
        assert body["plan"] == "PLN_T1"
        assert body["amount"] == 5000
        assert client.calls[0]["headers"]["User-Agent"] == USER_AGENT


class TestProviderSubscriptions:
    def test_list_subscriptions(self):
        client = FakeClient([FakeResponse({"status": True, "data": [
            {"subscription_code": "SUB_1"}]})])
        subs = _provider(client).list_subscriptions()
        assert subs[0]["subscription_code"] == "SUB_1"
        assert "/subscription?perPage=" in client.calls[0]["url"]

    def test_disable_subscription_posts_code_and_token(self):
        client = FakeClient([FakeResponse({"status": True, "data": {
            "subscription_code": "SUB_1", "status": "cancelled"}})])
        out = _provider(client).disable_subscription("SUB_1", "tok_1")
        assert client.calls[0]["url"].endswith("/subscription/disable")
        assert client.calls[0]["json"] == {"code": "SUB_1", "token": "tok_1"}
        assert out["status"] == "cancelled"

    def test_disable_requires_token(self):
        with pytest.raises(ValueError):
            _provider(FakeClient()).disable_subscription("SUB_1", "")


# ── juris plan map + sync ─────────────────────────────────────────────────

class FakePlanProvider:
    mode = "test"

    def __init__(self, existing=None):
        self.existing = list(existing or [])
        self.created = []
        self.updated = []

    def list_plans(self):
        return list(self.existing)

    def create_plan(self, name, amount, interval, currency="GHS", description=""):
        plan = {"id": 100 + len(self.created), "plan_code": f"PLN_NEW{len(self.created)}",
                "name": name, "amount": amount, "interval": interval,
                "currency": currency}
        self.created.append(plan)
        self.existing.append(plan)
        return plan

    def update_plan(self, code, **fields):
        self.updated.append({"code": code, **fields})
        for p in self.existing:
            if p.get("plan_code") == code:
                p.update(fields)
                return p
        return {"plan_code": code, **fields}


@pytest.fixture(autouse=True)
def isolated_plan_map(tmp_path, monkeypatch):
    path = tmp_path / "juris_plans.json"
    monkeypatch.setenv("JURIS_PLANS_PATH", str(path))
    from core.payments import store as payments_store
    monkeypatch.setattr(payments_store, "DB_PATH", str(tmp_path / "payments.db"))
    yield path


class TestPlanMap:
    def test_interval_for_tier(self):
        assert juris_plans.interval_for_tier(
            "monthly_basic", {"duration_days": 30}) == "monthly"
        assert juris_plans.interval_for_tier(
            "annual_pro", {"duration_days": 365}) == "annually"

    def test_paid_tiers_excludes_free(self):
        tiers = {
            "free_trial": {"price_ghs": 0},
            "monthly_basic": {"price_ghs": 50},
        }
        paid = juris_plans.paid_tiers(tiers)
        assert list(paid) == ["monthly_basic"]

    def test_sync_creates_plans_once_and_is_idempotent(self):
        prov = FakePlanProvider()
        first = juris_plans.sync_plans(provider=prov)
        assert first["created"] >= 1
        assert first["success"] is True
        assert all(p["plan_code"] for p in first["plans"])
        second = juris_plans.sync_plans(provider=prov)
        assert second["created"] == 0
        assert second["updated"] == 0
        assert second["unchanged"] >= 1

    def test_sync_matches_by_name_when_map_lost(self):
        tiers = {"monthly_basic": {"name": "Basic Monthly", "duration_days": 30,
                                   "price_ghs": 50}}
        name = juris_plans.plan_name("monthly_basic", tiers["monthly_basic"])
        prov = FakePlanProvider(existing=[{
            "id": 9, "plan_code": "PLN_EXISTING", "name": name,
            "amount": 5000, "interval": "monthly", "currency": "GHS"}])
        out = juris_plans.sync_plans(provider=prov, tiers=tiers)
        assert out["created"] == 0
        assert out["plans"][0]["plan_code"] == "PLN_EXISTING"
        assert not prov.created

    def test_sync_updates_on_price_change(self):
        tiers = {"monthly_basic": {"name": "Basic Monthly", "duration_days": 30,
                                   "price_ghs": 50}}
        name = juris_plans.plan_name("monthly_basic", tiers["monthly_basic"])
        prov = FakePlanProvider(existing=[{
            "id": 9, "plan_code": "PLN_EXISTING", "name": name,
            "amount": 3000, "interval": "monthly", "currency": "GHS"}])
        out = juris_plans.sync_plans(provider=prov, tiers=tiers)
        assert out["updated"] == 1
        assert prov.updated[0]["amount"] == 5000

    def test_plan_code_for_tier_and_reverse(self):
        prov = FakePlanProvider()
        out = juris_plans.sync_plans(provider=prov)
        code = juris_plans.plan_code_for_tier("monthly_basic")
        assert code
        assert juris_plans.tier_for_plan_code(code) == "monthly_basic"


# ── checkout attaches the plan ────────────────────────────────────────────

class TestCheckoutAttachesPlan:
    def test_plan_passed_to_initialize(self, tmp_path, monkeypatch):
        # Isolate the accounts DB and plan map.
        from core.juris_kai import accounts, paystack_checkout
        monkeypatch.setattr(accounts, "DB_DIR", str(tmp_path))
        monkeypatch.setattr(accounts, "DB_PATH", str(tmp_path / "a.db"))
        accounts._account_manager = None

        # Seed a plan code for the tier.
        juris_plans.save_plan_map(
            {"monthly_basic": {"plan_code": "PLN_T1", "tier": "monthly_basic"}},
            mode="test")
        acct = accounts.get_account_manager().get_or_create("b1", "Buyer")

        calls = {}

        class Fake:
            mode = "test"

            def initialize(self, **kwargs):
                calls.update(kwargs)
                return {"reference": kwargs["reference"], "mode": "test",
                        "authorization_url": "https://x", "access_code": "a"}

        result = paystack_checkout.create_paystack_checkout(
            acct, "monthly_basic", email="b@example.com", provider=Fake())
        assert calls["plan"] == "PLN_T1"
        assert result["plan_code"] == "PLN_T1"
        assert calls["metadata"]["plan_code"] == "PLN_T1"

        accounts._account_manager = None
