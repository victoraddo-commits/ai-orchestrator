"""TDD tests for the Juris Kai Paystack checkout + activation path.

Reuses ``core.payments.PaystackProvider`` (HTTP is always mocked), the payments
ledger, and the account manager on an isolated SQLite DB. No real charge is
ever attempted.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.juris_kai import cc_routes
from core.juris_kai import paystack_checkout as checkout
from core.payments import store as payments_store
from core.payments.paystack import PaystackError, PaystackProvider

BRIDGE = {"Authorization": "Bearer test-bridge-token"}


def _accts():
    """Import accounts lazily so the DB path is resolved after env setup."""
    from core.juris_kai import accounts
    return accounts


@pytest.fixture(autouse=True)
def isolated_dbs(tmp_path, monkeypatch):
    accts = _accts()
    monkeypatch.setattr(payments_store, "DB_PATH", str(tmp_path / "payments.db"))
    monkeypatch.setattr(accts, "DB_DIR", str(tmp_path))
    monkeypatch.setattr(accts, "DB_PATH", str(tmp_path / "juris_accounts.db"))
    monkeypatch.setenv("JURIS_PLANS_PATH", str(tmp_path / "juris_plans.json"))
    accts._account_manager = None
    checkout.reset_provider()
    yield
    accts._account_manager = None
    checkout.reset_provider()


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr("core.bridge_auth._load_api_token",
                        lambda: "test-bridge-token")
    cc_routes._rate_state.clear()
    app = FastAPI()
    app.include_router(cc_routes.router)
    return TestClient(app)


def _account(mgr=None):
    mgr = mgr or _accts().get_account_manager()
    return mgr.get_or_create(str(uuid.uuid4())[:8].replace("-", "0"), "Buyer")


class FakeProvider:
    """Records initialize/metadata and returns a synthetic checkout URL."""

    mode = "test"

    def __init__(self, secret="sk_test_dummy"):
        self.secret = secret
        self.initialize_calls = []
        self.parse_calls = []

    def initialize(self, **kwargs):
        self.initialize_calls.append(kwargs)
        return {
            "provider": "paystack",
            "reference": kwargs["reference"],
            "authorization_url": "https://checkout.paystack.com/juris-test",
            "access_code": "acc_juris",
            "status": "initialized",
            "mode": "test",
            "idempotent": False,
        }

    def verify(self, reference):
        return {"provider": "paystack", "reference": reference,
                "status": "success", "mode": "test"}

    def parse_webhook(self, raw_body, signature):
        self.parse_calls.append((raw_body, signature))
        if isinstance(raw_body, (bytes, bytearray)):
            raw_body = raw_body.decode()
        return json.loads(raw_body)


class RaisingProvider(FakeProvider):
    def initialize(self, **kwargs):
        raise PaystackError("paystack down")


# ── checkout creation ─────────────────────────────────────────────────────

class TestCreatePaystackCheckout:
    def test_metadata_amount_channels(self):
        acct = _account()
        fake = FakeProvider()
        result = checkout.create_paystack_checkout(
            acct, "monthly_basic", email="buyer@example.com", provider=fake)

        call = fake.initialize_calls[0]
        assert call["amount"] == 5000  # GHS 50 -> pesewas
        assert call["currency"] == "GHS"
        assert call["channels"] == ["mobile_money", "card"]
        assert call["metadata"] == {
            "module": "juris_kai", "account_id": acct["account_id"],
            "tier": "monthly_basic"}
        assert result["authorization_url"].startswith("https://checkout.paystack.com/")
        assert result["reference"].startswith("JURIS-")
        assert result["provider"] == "paystack"

    def test_plan_code_attached_when_mapped(self):
        from core.juris_kai import plans
        plans.save_plan_map(
            {"monthly_basic": {"plan_code": "PLN_test_basic"}},
            mode="test", path=plans.plans_path())
        acct = _account()
        fake = FakeProvider()
        result = checkout.create_paystack_checkout(
            acct, "monthly_basic", email="buyer@example.com", provider=fake)
        call = fake.initialize_calls[0]
        # The test-mode plan must be attached so Paystack creates a Subscription.
        assert call["plan"] == "PLN_test_basic"
        assert call["metadata"]["plan_code"] == "PLN_test_basic"
        assert result["plan_code"] == "PLN_test_basic"

    def test_live_plan_not_attached_in_test_mode(self):
        from core.juris_kai import plans
        plans.save_plan_map(
            {"monthly_basic": {"plan_code": "PLN_live_basic"}},
            mode="live", path=plans.plans_path())
        acct = _account()
        fake = FakeProvider()
        result = checkout.create_paystack_checkout(
            acct, "monthly_basic", email="buyer@example.com", provider=fake)
        # A live plan code must never be attached to a test-mode transaction.
        assert fake.initialize_calls[0].get("plan") is None
        assert result["plan_code"] is None

    def test_free_tier_rejected(self):
        acct = _account()
        with pytest.raises(ValueError):
            checkout.create_paystack_checkout(acct, "free_trial",
                                              email="a@b.com", provider=FakeProvider())

    def test_unknown_tier_rejected(self):
        acct = _account()
        with pytest.raises(ValueError):
            checkout.create_paystack_checkout(acct, "ultra",
                                              email="a@b.com", provider=FakeProvider())

    def test_missing_email_rejected(self):
        acct = _account()
        with pytest.raises(ValueError):
            checkout.create_paystack_checkout(acct, "monthly_basic",
                                              provider=FakeProvider())

    def test_email_falls_back_to_account_profile(self):
        acct = _account()
        mgr = _accts().get_account_manager()
        mgr.update_profile(acct["account_id"], email="profile@example.com")
        acct = mgr.get_account(acct["account_id"])
        fake = FakeProvider()
        checkout.create_paystack_checkout(acct, "monthly_pro", provider=fake)
        assert fake.initialize_calls[0]["email"] == "profile@example.com"


class TestProviderSwitch:
    def test_default_is_paystack(self, monkeypatch):
        monkeypatch.delenv("JURIS_PAYMENT_PROVIDER", raising=False)
        assert checkout.provider_name() == "paystack"

    def test_switch_to_hubtel(self, monkeypatch):
        monkeypatch.setenv("JURIS_PAYMENT_PROVIDER", "hubtel")
        assert checkout.provider_name() == "hubtel"

    def test_invalid_value_falls_back(self, monkeypatch):
        monkeypatch.setenv("JURIS_PAYMENT_PROVIDER", "stripe")
        assert checkout.provider_name() == "paystack"


# ── activation ────────────────────────────────────────────────────────────

def _success_data(account_id, tier, reference, amount=5000):
    return {
        "reference": reference,
        "status": "success",
        "amount": amount,
        "currency": "GHS",
        "channel": "mobile_money",
        "metadata": {"module": "juris_kai", "account_id": account_id, "tier": tier},
    }


class TestActivation:
    def test_activates_tier(self):
        mgr = _accts().get_account_manager()
        acct = _account(mgr)
        ref = "JURIS-ACT1"
        out = checkout.activate_reference(
            ref, status="success", data=_success_data(acct["account_id"], "monthly_pro", ref))
        assert out["activated"] is True
        assert out["tier"] == "monthly_pro"
        assert mgr.get_active_subscription(acct["account_id"])["tier"] == "monthly_pro"

    def test_idempotent_by_reference(self):
        mgr = _accts().get_account_manager()
        acct = _account(mgr)
        ref = "JURIS-ACT2"
        checkout.activate_reference(ref, status="success",
                                    data=_success_data(acct["account_id"], "monthly_basic", ref))
        first_end = mgr.get_account(acct["account_id"])["subscription_end"]
        second = checkout.activate_reference(ref, status="success",
                                             data=_success_data(acct["account_id"], "monthly_basic", ref))
        assert second["activated"] is False
        assert second.get("duplicate") is True
        assert mgr.get_account(acct["account_id"])["subscription_end"] == first_end

    def test_non_success_status_ignored(self):
        acct = _account()
        out = checkout.activate_reference("JURIS-ACT3", status="failed",
                                          data=_success_data(acct["account_id"], "monthly_basic", "JURIS-ACT3"))
        assert out["activated"] is False

    def test_non_juris_metadata_ignored(self):
        acct = _account()
        data = _success_data(acct["account_id"], "monthly_basic", "JURIS-ACT4")
        data["metadata"]["module"] = "betting"
        out = checkout.activate_reference("JURIS-ACT4", status="success", data=data)
        assert out["activated"] is False
        assert out["reason"] == "not_juris_kai"

    def test_unknown_account_ignored(self):
        out = checkout.activate_reference(
            "JURIS-ACT5", status="success",
            data=_success_data("does-not-exist", "monthly_basic", "JURIS-ACT5"))
        assert out["activated"] is False
        assert out["reason"] == "unknown_account"

    def test_metadata_resolved_from_ledger(self):
        mgr = _accts().get_account_manager()
        acct = _account(mgr)
        ref = "JURIS-ACT6"
        payments_store.record_initialized(
            reference=ref, amount=5000, currency="GHS", email="a@b.com",
            metadata={"module": "juris_kai", "account_id": acct["account_id"],
                      "tier": "annual_pro"})
        out = checkout.activate_reference(ref, status="success",
                                          data={"reference": ref, "status": "success"})
        assert out["activated"] is True
        assert mgr.get_active_subscription(acct["account_id"])["tier"] == "annual_pro"


# ── webhook ───────────────────────────────────────────────────────────────

class TestWebhook:
    def test_valid_signature_activates(self):
        mgr = _accts().get_account_manager()
        acct = _account(mgr)
        secret = "sk_test_webhook_secret"
        provider = PaystackProvider(secret_key=secret, mode="test")
        event = {"event": "charge.success",
                 "data": _success_data(acct["account_id"], "monthly_basic", "JURIS-WH1")}
        body = json.dumps(event).encode()
        sig = hmac.new(secret.encode(), body, hashlib.sha512).hexdigest()

        out = checkout.handle_webhook(body, sig, provider=provider)
        assert out["status"] == "success"
        assert out["activation"]["activated"] is True
        assert mgr.get_active_subscription(acct["account_id"])["tier"] == "monthly_basic"

    def test_bad_signature_raises(self):
        provider = PaystackProvider(secret_key="sk_test_webhook_secret", mode="test")
        body = json.dumps({"event": "charge.success",
                           "data": {"reference": "r1"}}).encode()
        with pytest.raises(PaystackError):
            checkout.handle_webhook(body, "deadbeef", provider=provider)

    def test_webhook_records_ledger_mode(self):
        mgr = _accts().get_account_manager()
        acct = _account(mgr)
        secret = "sk_test_webhook_secret"
        provider = PaystackProvider(secret_key=secret, mode="test")
        event = {"event": "charge.success",
                 "data": _success_data(acct["account_id"], "monthly_basic", "JURIS-WH3")}
        body = json.dumps(event).encode()
        sig = hmac.new(secret.encode(), body, hashlib.sha512).hexdigest()
        checkout.handle_webhook(body, sig, provider=provider)
        stored = payments_store.get_payment("JURIS-WH3")
        assert stored is not None
        assert stored["status"] == "success"
        assert stored["mode"] == "test"


# ── CC endpoints ──────────────────────────────────────────────────────────

class TestCheckoutEndpoints:
    def test_checkout_requires_credentials(self, client):
        assert client.post("/api/juris-kai/cc/checkout",
                           json={"account_id": "a", "tier": "monthly_basic"}).status_code == 401

    def test_checkout_success(self, client, monkeypatch):
        acct = _account()
        fake = FakeProvider()
        monkeypatch.setattr(checkout, "get_paystack_provider", lambda: fake)
        monkeypatch.setattr(cc_routes, "_log_admin", lambda *a, **k: None)
        r = client.post("/api/juris-kai/cc/checkout", headers=BRIDGE,
                        json={"account_id": acct["account_id"],
                              "tier": "monthly_basic",
                              "email": "buyer@example.com"})
        body = r.json()
        assert body["success"] is True
        assert body["checkout"]["authorization_url"].startswith(
            "https://checkout.paystack.com/")
        assert body["checkout"]["mode"] == "test"

    def test_checkout_unknown_account_404(self, client):
        r = client.post("/api/juris-kai/cc/checkout", headers=BRIDGE,
                        json={"account_id": "nope", "tier": "monthly_basic"})
        assert r.status_code == 404

    def test_checkout_provider_error_is_reported(self, client, monkeypatch):
        acct = _account()
        monkeypatch.setattr(checkout, "get_paystack_provider", lambda: RaisingProvider())
        monkeypatch.setattr(cc_routes, "_log_admin", lambda *a, **k: None)
        r = client.post("/api/juris-kai/cc/checkout", headers=BRIDGE,
                        json={"account_id": acct["account_id"],
                              "tier": "monthly_basic", "email": "a@b.com"})
        body = r.json()
        assert body["success"] is False
        assert "down" in body["error"]

    def test_webhook_endpoint_activates(self, client, monkeypatch):
        mgr = _accts().get_account_manager()
        acct = _account(mgr)
        fake = FakeProvider()
        monkeypatch.setattr(checkout, "get_paystack_provider", lambda: fake)
        event = {"event": "charge.success",
                 "data": _success_data(acct["account_id"], "monthly_pro", "JURIS-WH2")}
        body = json.dumps(event).encode()
        r = client.post("/api/juris-kai/paystack/webhook", content=body,
                        headers={"x-paystack-signature": "valid"})
        assert r.status_code == 200
        assert r.json()["activation"]["activated"] is True
        assert mgr.get_active_subscription(acct["account_id"])["tier"] == "monthly_pro"

    def test_webhook_endpoint_bad_signature_401(self, client, monkeypatch):
        fake = FakeProvider()

        def boom(raw, sig):
            raise PaystackError("invalid Paystack webhook signature")

        fake.parse_webhook = boom
        monkeypatch.setattr(checkout, "get_paystack_provider", lambda: fake)
        r = client.post("/api/juris-kai/paystack/webhook", content=b"{}",
                        headers={"x-paystack-signature": "bad"})
        assert r.status_code == 401
