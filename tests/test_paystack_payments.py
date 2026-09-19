"""Tests for the Paystack payments subsystem (core/payments).

All HTTP is mocked — no real charges are ever made. Covers initialize,
verify, refund, webhook signature accept/reject, idempotency, mode gating,
the FastAPI routes, and the betting-module provider switch.
"""

from __future__ import annotations

import hashlib
import hmac
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.payments import keys as payments_keys
from core.payments import store as payments_store
from core.payments.paystack import (
    GH_MOMO_PROVIDERS,
    PaystackError,
    PaystackProvider,
)


# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def isolated_payments_db(tmp_path, monkeypatch):
    """Every test gets its own payments SQLite file."""
    monkeypatch.setattr(payments_store, "DB_PATH", str(tmp_path / "payments.db"))
    yield


class FakeResponse:
    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self._payload = payload
        self.text = json.dumps(payload)

    def json(self):
        return self._payload


class FakeClient:
    """Records requests and replays queued responses."""

    def __init__(self, responses=None):
        self.responses = list(responses or [])
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append({"method": method, "url": url, "kwargs": kwargs})
        if not self.responses:
            raise AssertionError(f"unexpected HTTP call: {method} {url}")
        return self.responses.pop(0)

    def close(self):
        pass


def _init_payload(reference="ref-test-1", amount=1000, currency="GHS"):
    return {
        "status": True,
        "message": "Authorization URL created",
        "data": {
            "authorization_url": "https://checkout.paystack.com/abc123",
            "access_code": "abc123",
            "reference": reference,
            "amount": amount,
            "currency": currency,
        },
    }


def _verify_payload(reference="ref-test-1", amount=1000, status="success"):
    return {
        "status": True,
        "message": "Verification successful",
        "data": {
            "id": 4099260516,
            "status": status,
            "reference": reference,
            "amount": amount,
            "currency": "GHS",
            "channel": "mobile_money",
            "gateway_response": "Approved",
            "paid_at": "2026-01-01T00:00:00.000Z",
        },
    }


@pytest.fixture
def make_provider(monkeypatch):
    monkeypatch.setenv("PAYSTACK_SECRET_KEY", "sk_test_dummy")
    monkeypatch.setenv("PAYSTACK_PUBLIC_KEY", "pk_test_dummy")
    monkeypatch.setenv("PAYSTACK_MODE", "test")
    monkeypatch.delenv("PAYSTACK_ALLOW_LIVE", raising=False)

    def _make(responses=None, mode="test", **kwargs):
        client = FakeClient(responses)
        provider = PaystackProvider(
            secret_key="sk_test_dummy",
            public_key="pk_test_dummy",
            mode=mode,
            http_client=client,
            **kwargs,
        )
        return provider, client

    return _make


# ── initialize ──────────────────────────────────────────────────────────────


class TestInitialize:
    def test_initialize_success_posts_minor_units(self, make_provider):
        provider, client = make_provider([FakeResponse(200, _init_payload())])

        result = provider.initialize(
            amount=1000,
            currency="GHS",
            email="buyer@example.com",
            reference="ref-test-1",
            channels=["mobile_money", "card"],
            callback_url="https://kai.example/cb",
        )

        assert result["authorization_url"] == "https://checkout.paystack.com/abc123"
        assert result["reference"] == "ref-test-1"
        assert result["idempotent"] is False

        call = client.calls[0]
        assert call["method"] == "POST"
        assert call["url"].endswith("/transaction/initialize")
        body = call["kwargs"]["json"]
        assert body["amount"] == 1000  # minor units, integer
        assert body["currency"] == "GHS"
        assert body["channels"] == ["mobile_money", "card"]
        assert body["callback_url"] == "https://kai.example/cb"
        assert call["kwargs"]["headers"]["Authorization"] == "Bearer sk_test_dummy"

        stored = payments_store.get_payment("ref-test-1")
        assert stored["status"] == "initialized"
        assert stored["amount"] == 1000
        assert stored["currency"] == "GHS"
        assert stored["email"] == "buyer@example.com"
        assert stored["requested_channel"] == "mobile_money,card"

    def test_initialize_rejects_non_integer_amount(self, make_provider):
        provider, _ = make_provider([])
        with pytest.raises(ValueError):
            provider.initialize(amount=10.5, email="a@b.com", reference="r1")

    def test_initialize_rejects_negative_amount(self, make_provider):
        provider, _ = make_provider([])
        with pytest.raises(ValueError):
            provider.initialize(amount=-1, email="a@b.com", reference="r1")

    def test_initialize_rejects_unsupported_currency(self, make_provider):
        provider, _ = make_provider([])
        with pytest.raises(ValueError):
            provider.initialize(amount=100, currency="XYZ", email="a@b.com", reference="r1")

    def test_initialize_rejects_bad_email(self, make_provider):
        provider, _ = make_provider([])
        with pytest.raises(ValueError):
            provider.initialize(amount=100, email="not-an-email", reference="r1")

    def test_initialize_rejects_bad_reference_chars(self, make_provider):
        provider, _ = make_provider([])
        with pytest.raises(ValueError):
            provider.initialize(amount=100, email="a@b.com", reference="bad ref!")

    def test_initialize_idempotent_by_reference(self, make_provider):
        provider, client = make_provider([FakeResponse(200, _init_payload())])

        first = provider.initialize(amount=1000, email="a@b.com", reference="ref-dup")
        second = provider.initialize(amount=1000, email="a@b.com", reference="ref-dup")

        assert len(client.calls) == 1  # no second HTTP call
        assert second["idempotent"] is True
        assert second["authorization_url"] == first["authorization_url"]

    def test_initialize_paystack_error_is_honest(self, make_provider):
        provider, _ = make_provider(
            [FakeResponse(401, {"status": False, "message": "Invalid key"})]
        )
        with pytest.raises(PaystackError) as excinfo:
            provider.initialize(amount=100, email="a@b.com", reference="ref-err")
        assert "Invalid key" in str(excinfo.value)
        assert payments_store.get_payment("ref-err") is None


# ── verify ──────────────────────────────────────────────────────────────────


class TestVerify:
    def test_verify_updates_store(self, make_provider):
        provider, client = make_provider(
            [
                FakeResponse(200, _init_payload()),
                FakeResponse(200, _verify_payload()),
            ]
        )
        provider.initialize(amount=1000, email="a@b.com", reference="ref-test-1")
        result = provider.verify("ref-test-1")

        assert result["status"] == "success"
        assert result["channel"] == "mobile_money"
        assert client.calls[1]["method"] == "GET"
        assert client.calls[1]["url"].endswith("/transaction/verify/ref-test-1")

        stored = payments_store.get_payment("ref-test-1")
        assert stored["status"] == "success"
        assert stored["verified_at"] is not None
        assert stored["gateway_response"] == "Approved"

    def test_verify_without_prior_initialize_records(self, make_provider):
        provider, _ = make_provider([FakeResponse(200, _verify_payload(reference="orphan"))])
        result = provider.verify("orphan")
        assert result["status"] == "success"
        stored = payments_store.get_payment("orphan")
        assert stored is not None
        assert stored["status"] == "success"

    def test_verify_records_provider_mode(self, make_provider):
        provider, _ = make_provider(
            [FakeResponse(200, _verify_payload(reference="orphan-mode"))])
        provider.verify("orphan-mode")
        assert payments_store.get_payment("orphan-mode")["mode"] == "test"

    def test_verify_paystack_error(self, make_provider):
        provider, _ = make_provider(
            [FakeResponse(404, {"status": False, "message": "Transaction not found"})]
        )
        with pytest.raises(PaystackError):
            provider.verify("nope")


# ── refund ──────────────────────────────────────────────────────────────────


class TestRefund:
    def test_refund_posts_transaction(self, make_provider):
        provider, client = make_provider(
            [FakeResponse(200, {"status": True, "message": "Refund queued",
                                "data": {"status": "pending", "reference": "ref-test-1"}})]
        )
        result = provider.refund("ref-test-1", amount=500)
        assert result["status"] == "pending"
        body = client.calls[0]["kwargs"]["json"]
        assert body["transaction"] == "ref-test-1"
        assert body["amount"] == 500


# ── webhook signature ───────────────────────────────────────────────────────


class TestWebhookSignature:
    def test_accepts_valid_signature(self, make_provider):
        provider, _ = make_provider([])
        body = json.dumps({"event": "charge.success", "data": {"reference": "r1"}}).encode()
        sig = hmac.new(b"sk_test_dummy", body, hashlib.sha512).hexdigest()
        assert provider.verify_webhook(body, sig) is True

    def test_rejects_tampered_body(self, make_provider):
        provider, _ = make_provider([])
        body = b'{"event":"charge.success","data":{"reference":"r1"}}'
        sig = hmac.new(b"sk_test_dummy", body, hashlib.sha512).hexdigest()
        assert provider.verify_webhook(body + b"x", sig) is False

    def test_rejects_wrong_signature(self, make_provider):
        provider, _ = make_provider([])
        body = b'{"event":"charge.success"}'
        assert provider.verify_webhook(body, "deadbeef") is False

    def test_rejects_missing_values(self, make_provider):
        provider, _ = make_provider([])
        assert provider.verify_webhook(b"{}", None) is False
        assert provider.verify_webhook(None, "abc") is False

    def test_accepts_str_body(self, make_provider):
        provider, _ = make_provider([])
        body = '{"event":"charge.success"}'
        sig = hmac.new(b"sk_test_dummy", body.encode(), hashlib.sha512).hexdigest()
        assert provider.verify_webhook(body, sig) is True


class TestWebhookHandling:
    def test_handle_webhook_records_provider_mode(self, make_provider):
        provider, _ = make_provider([])
        event = {"event": "charge.success",
                 "data": {"reference": "wh-mode", "status": "success",
                          "amount": 1000, "currency": "GHS"}}
        body = json.dumps(event).encode()
        sig = hmac.new(b"sk_test_dummy", body, hashlib.sha512).hexdigest()
        out = provider.handle_webhook(body, sig)
        assert out["handled"] is True
        stored = payments_store.get_payment("wh-mode")
        assert stored["status"] == "success"
        assert stored["mode"] == "test"


# ── mode gating ─────────────────────────────────────────────────────────────


class TestModeGating:
    def test_default_mode_is_test(self, monkeypatch):
        monkeypatch.delenv("PAYSTACK_MODE", raising=False)
        assert payments_keys.mode() == "test"
        assert payments_keys.live_allowed() is False

    def test_license_live_requires_allow_flag(self, monkeypatch, make_provider):
        monkeypatch.setenv("PAYSTACK_MODE", "live")
        monkeypatch.delenv("PAYSTACK_ALLOW_LIVE", raising=False)
        provider, client = make_provider(
            [FakeResponse(200, _init_payload())], mode="live"
        )
        with pytest.raises(payments_keys.PaymentConfigError):
            provider.initialize(amount=100, email="a@b.com", reference="live1")
        assert client.calls == []  # never called Paystack

    def test_live_allowed_when_explicitly_enabled(self, monkeypatch, make_provider):
        monkeypatch.setenv("PAYSTACK_MODE", "live")
        monkeypatch.setenv("PAYSTACK_ALLOW_LIVE", "true")
        assert payments_keys.live_allowed() is True
        provider, client = make_provider(
            [FakeResponse(200, _init_payload())], mode="live"
        )
        result = provider.initialize(amount=100, email="a@b.com", reference="live-ok")
        assert result["mode"] == "live"
        assert len(client.calls) == 1

    def test_missing_test_key_raises_config_error(self, monkeypatch):
        monkeypatch.setenv("PAYSTACK_MODE", "test")
        monkeypatch.delenv("PAYSTACK_SECRET_KEY", raising=False)

        def _boom():
            raise payments_keys.PaymentConfigError("no test secret")

        monkeypatch.setattr(payments_keys, "secret_key", _boom)
        provider = PaystackProvider(mode="test", http_client=FakeClient([]))
        with pytest.raises(payments_keys.PaymentConfigError):
            provider.initialize(amount=100, email="a@b.com", reference="nokey")


# ── Ghana mobile money mapping ──────────────────────────────────────────────


def test_gh_momo_provider_codes():
    assert GH_MOMO_PROVIDERS["mtn"] == "MTN"
    assert GH_MOMO_PROVIDERS["vod"] == "Telecel"
    assert GH_MOMO_PROVIDERS["atl"] == "AirtelTigo"


# ── FastAPI routes ──────────────────────────────────────────────────────────


class StubProvider:
    mode = "test"

    def __init__(self):
        self.webhook_ok = True
        self.verify_webhook_calls = []

    def initialize(self, **kwargs):
        payments_store.record_initialized(
            reference=kwargs["reference"],
            amount=kwargs["amount"],
            currency=kwargs["currency"],
            email=kwargs["email"],
            authorization_url="https://checkout.paystack.com/stub",
            access_code="stub",
            mode="test",
            requested_channel=",".join(kwargs.get("channels") or []),
        )
        return {
            "provider": "paystack",
            "reference": kwargs["reference"],
            "authorization_url": "https://checkout.paystack.com/stub",
            "access_code": "stub",
            "status": "initialized",
            "amount": kwargs["amount"],
            "currency": kwargs["currency"],
            "mode": "test",
            "idempotent": False,
        }

    def verify(self, reference):
        rec = payments_store.update_verified(
            reference, status="success", channel="card", gateway_response="Approved"
        )
        return {"provider": "paystack", "status": rec["status"], "reference": reference}

    def verify_webhook(self, raw_body, signature):
        self.verify_webhook_calls.append((raw_body, signature))
        return self.webhook_ok


@pytest.fixture
def stub_routes(monkeypatch):
    from core.payments import routes as payments_routes
    from core.bridge_auth import _load_api_token

    stub = StubProvider()
    monkeypatch.setattr(payments_routes, "get_provider", lambda: stub)
    app = FastAPI()
    app.include_router(payments_routes.router)
    client = TestClient(app)
    auth = {"Authorization": f"Bearer {_load_api_token()}"}
    return client, stub, auth


class TestRoutes:
    def test_initialize_requires_auth(self, stub_routes):
        client, _, _ = stub_routes
        resp = client.post(
            "/api/payments/initialize",
            json={"amount": 100, "email": "a@b.com", "reference": "r-auth"},
        )
        assert resp.status_code == 401

    def test_initialize_with_bridge_token(self, stub_routes):
        client, _, auth = stub_routes
        resp = client.post(
            "/api/payments/initialize",
            headers=auth,
            json={"amount": 100, "email": "a@b.com", "reference": "r-ok"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["data"]["authorization_url"].startswith("https://checkout.paystack.com/")

    def test_initialize_rejects_float_amount(self, stub_routes):
        client, _, auth = stub_routes
        resp = client.post(
            "/api/payments/initialize",
            headers=auth,
            json={"amount": 10.5, "email": "a@b.com", "reference": "r-bad"},
        )
        assert resp.status_code == 422

    def test_verify_requires_auth(self, stub_routes):
        client, _, _ = stub_routes
        assert client.get("/api/payments/verify/r1").status_code == 401

    def test_get_payment_not_found(self, stub_routes):
        client, _, auth = stub_routes
        resp = client.get("/api/payments/missing_ref", headers=auth)
        assert resp.status_code == 404

    def test_get_payment_after_initialize(self, stub_routes):
        client, _, auth = stub_routes
        client.post(
            "/api/payments/initialize",
            headers=auth,
            json={"amount": 100, "email": "a@b.com", "reference": "r-get"},
        )
        resp = client.get("/api/payments/r-get", headers=auth)
        assert resp.status_code == 200
        assert resp.json()["data"]["reference"] == "r-get"

    def test_webhook_rejects_bad_signature(self, stub_routes):
        client, stub, _ = stub_routes
        stub.webhook_ok = False
        resp = client.post(
            "/api/payments/webhook",
            content=b'{"event":"charge.success"}',
            headers={"x-paystack-signature": "bad"},
        )
        assert resp.status_code == 401

    def test_webhook_accepts_and_is_idempotent(self, stub_routes):
        client, _, _ = stub_routes
        event = {
            "event": "charge.success",
            "data": {
                "reference": "r-wh",
                "status": "success",
                "amount": 100,
                "currency": "GHS",
                "channel": "mobile_money",
                "gateway_response": "Approved",
            },
        }
        payload = json.dumps(event).encode()
        first = client.post(
            "/api/payments/webhook",
            content=payload,
            headers={"x-paystack-signature": "valid"},
        )
        assert first.status_code == 200
        assert first.json()["duplicate"] is False

        second = client.post(
            "/api/payments/webhook",
            content=payload,
            headers={"x-paystack-signature": "valid"},
        )
        assert second.status_code == 200
        assert second.json()["duplicate"] is True


# ── betting module provider switch ──────────────────────────────────────────


class TestBettingProviderSwitch:
    def test_defaults_to_hubtel(self, monkeypatch):
        monkeypatch.delenv("PAYMENT_PROVIDER", raising=False)
        from core.kai_betting.payments import BettingPaymentClient

        assert BettingPaymentClient().provider_name == "hubtel"

    def test_switch_to_paystack(self, monkeypatch):
        monkeypatch.setenv("PAYMENT_PROVIDER", "paystack")
        from core.kai_betting.payments import BettingPaymentClient

        assert BettingPaymentClient().provider_name == "paystack"

    def test_paystack_request_payment_uses_provider(self, monkeypatch):
        monkeypatch.setenv("PAYMENT_PROVIDER", "paystack")
        called = {}

        def fake_initialize(self, **kwargs):
            called.update(kwargs)
            return {
                "provider": "paystack",
                "reference": kwargs["reference"],
                "authorization_url": "https://checkout.paystack.com/xyz",
                "status": "initialized",
                "mode": "test",
            }

        monkeypatch.setattr(PaystackProvider, "initialize", fake_initialize)
        from core.kai_betting.payments import BettingPaymentClient

        client = BettingPaymentClient()
        result = client.request_payment(
            user_id=7,
            amount=20.0,
            currency="GHS",
            phone_number="0553241149",
            plan_key="daily",
            email="buyer@example.com",
        )
        assert result["success"] is True
        assert result["provider"] == "paystack"
        assert result["checkout_url"] == "https://checkout.paystack.com/xyz"
        assert called["amount"] == 2000  # GHS 20.00 -> 2000 pesewas
        assert called["email"] == "buyer@example.com"
