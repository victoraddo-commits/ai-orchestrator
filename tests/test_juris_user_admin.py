"""TDD tests for user administration, subscription sync and the CC endpoints."""

from __future__ import annotations

import json
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.juris_kai import accounts as accts
from core.juris_kai import cc_routes, plans, subscriptions

BRIDGE = {"Authorization": "Bearer test-bridge-token"}


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    monkeypatch.setattr(accts, "DB_DIR", str(tmp_path))
    monkeypatch.setattr(accts, "DB_PATH", str(tmp_path / "juris.db"))
    monkeypatch.setenv("JURIS_PLANS_PATH", str(tmp_path / "plans.json"))
    accts._account_manager = None
    cc_routes._rate_state.clear()
    yield
    accts._account_manager = None


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr("core.bridge_auth._load_api_token",
                        lambda: "test-bridge-token")
    cc_routes._rate_state.clear()
    app = FastAPI()
    app.include_router(cc_routes.router)
    return TestClient(app)


def _mgr():
    return accts.get_account_manager()


class TestCreateAccount:
    def test_create_with_email_name_tier(self):
        res = _mgr().create_account("new@example.com", "New User", "monthly_basic")
        assert res["success"] is True
        assert res["email"] == "new@example.com"
        assert res["subscription_tier"] == "monthly_basic"
        assert _mgr().find_by_email("NEW@example.com")["account_id"] == res["account_id"]

    def test_rejects_bad_email_and_duplicate(self):
        mgr = _mgr()
        assert mgr.create_account("nope", "X")["success"] is False
        assert mgr.create_account("a@b.com", "A", "monthly_basic")["success"] is True
        dup = mgr.create_account("a@b.com", "A2")
        assert dup["success"] is False and "already exists" in dup["error"]

    def test_unknown_tier_rejected(self):
        assert _mgr().create_account("x@y.com", "X", "platinum")["success"] is False

    def test_deactivate_reactivate(self):
        acct = _mgr().create_account("d@e.com", "D", "free_trial")
        aid = acct["account_id"]
        assert _mgr().ban_account(aid, "test")["success"]
        assert _mgr().get_account(aid)["is_active"] == 0
        assert _mgr().reactivate(aid) is True
        assert _mgr().get_account(aid)["is_active"] == 1


class TestSubscriptionState:
    def test_upsert_and_counts(self):
        mgr = _mgr()
        mgr.upsert_subscription("SUB_1", account_id="acc1", tier="monthly_basic",
                                status="active", plan_code="PLN_1")
        mgr.upsert_subscription("SUB_2", account_id="acc2", tier="monthly_pro",
                                status="cancelled", plan_code="PLN_2")
        counts = mgr.subscription_counts()
        assert counts["total"] == 2
        assert counts["active"] == 1
        assert counts["by_status"]["cancelled"] == 1
        mgr.upsert_subscription("SUB_1", status="non-renewing")
        assert mgr.get_subscription("SUB_1")["status"] == "non-renewing"

    def test_handle_create_activates_linked_account(self):
        mgr = _mgr()
        acct = mgr.create_account("sub@example.com", "Sub", "free_trial")
        plans.save_plan_map({"monthly_basic": {"plan_code": "PLN_T1",
                                               "tier": "monthly_basic"}},
                            mode="test")
        out = subscriptions.handle_subscription_event("subscription.create", {
            "subscription_code": "SUB_X", "email_token": "tok",
            "customer": {"email": "sub@example.com"},
            "plan": {"plan_code": "PLN_T1"}, "amount": 5000, "currency": "GHS",
            "next_payment_date": "2026-10-01T00:00:00.000Z",
        })
        assert out["account_id"] == acct["account_id"]
        assert out["tier"] == "monthly_basic"
        assert out["activated"] is True
        assert mgr.get_active_subscription(acct["account_id"])["tier"] == "monthly_basic"
        assert mgr.get_subscription("SUB_X")["status"] == "active"

    def test_payment_failed_expires(self):
        mgr = _mgr()
        acct = mgr.create_account("f@example.com", "F", "monthly_pro")
        plans.save_plan_map({"monthly_pro": {"plan_code": "PLN_P", "tier": "monthly_pro"}},
                            mode="test")
        subscriptions.handle_subscription_event("subscription.create", {
            "subscription_code": "SUB_F", "customer": {"email": "f@example.com"},
            "plan": {"plan_code": "PLN_P"},
        })
        out = subscriptions.handle_subscription_event("invoice.payment_failed", {
            "subscription_code": "SUB_F", "customer": {"email": "f@example.com"},
            "plan": {"plan_code": "PLN_P"},
        })
        assert out["status"] == "attention"
        assert out["expired"] is True
        assert mgr.get_active_subscription(acct["account_id"])["is_active"] is False

    def test_disable_cancels(self):
        out = subscriptions.handle_subscription_event("subscription.disable", {
            "subscription_code": "SUB_D", "customer": {"email": "z@example.com"},
            "plan": {"plan_code": "PLN_M"},
        })
        assert out["status"] == "cancelled"

    def test_not_renew_keeps_access(self):
        out = subscriptions.handle_subscription_event("subscription.not_renew", {
            "subscription_code": "SUB_N", "customer": {"email": "z@example.com"},
        })
        assert out["status"] == "non-renewing"
        assert out["expired"] is False


class TestCcAccountEndpoints:
    def test_create_requires_credentials(self, client):
        assert client.post("/api/juris-kai/cc/accounts",
                           json={"email": "a@b.com"}).status_code == 401

    def test_create_via_bridge_and_auth_proxy(self, client):
        r = client.post("/api/juris-kai/cc/accounts", headers=BRIDGE,
                        json={"email": "cc@example.com", "full_name": "CC",
                              "tier": "monthly_basic"})
        assert r.status_code == 200 and r.json()["success"] is True
        # auth-proxy identity headers are accepted for writes too
        r2 = client.post("/api/juris-kai/cc/accounts",
                         headers={"X-Kai-User": "owner@kai",
                                  "X-Kai-User-Id": "owner-1"},
                         json={"email": "proxy@example.com", "full_name": "P"})
        assert r2.status_code == 200 and r2.json()["success"] is True

    def test_account_admin_actions(self, client):
        aid = _mgr().create_account("adm@example.com", "Adm", "free_trial")["account_id"]
        g = client.post(f"/api/juris-kai/cc/accounts/{aid}/grant-days",
                        headers=BRIDGE, json={"days": 7})
        assert g.json()["success"] is True
        t = client.post(f"/api/juris-kai/cc/accounts/{aid}/subscription",
                        headers=BRIDGE, json={"tier": "monthly_pro"})
        assert t.json()["subscription"]["tier"] == "monthly_pro"
        d = client.post(f"/api/juris-kai/cc/accounts/{aid}/deactivate",
                        headers=BRIDGE, json={"reason": "test"})
        assert d.json()["success"] is True
        a = client.post(f"/api/juris-kai/cc/accounts/{aid}/activate", headers=BRIDGE)
        assert a.json()["success"] is True

    def test_plans_endpoint(self, client):
        r = client.get("/api/juris-kai/cc/plans", headers=BRIDGE)
        body = r.json()
        assert r.status_code == 200 and body["success"] is True
        assert body["provider"]["mode"] in ("test", "live")
        keys = {t["tier"] for t in body["tiers"]}
        assert "monthly_basic" in keys
        assert "subscriptions" in body

    def test_plans_sync_endpoint_uses_provider(self, client, monkeypatch):
        from core.juris_kai import paystack_checkout

        class Fake:
            mode = "test"

            def list_plans(self):
                return []

            def create_plan(self, name, amount, interval, **kw):
                return {"plan_code": f"PLN_{name[:4]}", "id": 1, "name": name,
                        "amount": amount, "interval": interval}

            def update_plan(self, code, **kw):
                return {"plan_code": code, "id": 1, **kw}

        monkeypatch.setattr(paystack_checkout, "get_paystack_provider",
                            lambda: Fake())
        r = client.post("/api/juris-kai/cc/plans/sync", headers=BRIDGE)
        assert r.status_code == 200 and r.json()["created"] >= 1


class TestCheckoutWebhookSubscription:
    def test_subscription_create_webhook_syncs(self):
        from core.juris_kai import paystack_checkout

        class Fake:
            mode = "test"

            def parse_webhook(self, raw, sig):
                if isinstance(raw, (bytes, bytearray)):
                    raw = raw.decode()
                return json.loads(raw)

        mgr = _mgr()
        acct = mgr.create_account("wh@example.com", "WH", "free_trial")
        plans.save_plan_map(
            {"monthly_basic": {"plan_code": "PLN_W", "mode": "test"}},
            mode="test")
        event = {"event": "subscription.create", "data": {
            "subscription_code": "SUB_WH",
            "customer": {"email": "wh@example.com"},
            "plan": {"plan_code": "PLN_W"},
            "amount": 5000, "currency": "GHS"}}
        out = paystack_checkout.handle_webhook(
            json.dumps(event).encode(), "sig", provider=Fake())
        assert out["subscription"]["activated"] is True
        assert mgr.get_active_subscription(acct["account_id"])["tier"] == "monthly_basic"
        assert mgr.get_subscription("SUB_WH")["status"] == "active"


class TestCcGroupEndpoints:
    def test_group_crud_and_members(self, client):
        mgr = _mgr()
        owner = mgr.create_account("owner@example.com", "Owner", "monthly_pro")
        member = mgr.create_account("member@example.com", "Member", "free_trial")
        # create
        r = client.post("/api/juris-kai/cc/groups", headers=BRIDGE,
                        json={"name": "Chambers", "account_id": owner["account_id"]})
        gid = r.json()["group"]["group_id"]
        assert r.json()["success"] is True
        # list
        assert client.get("/api/juris-kai/cc/groups", headers=BRIDGE).json()["count"] == 1
        # add member
        add = client.post(f"/api/juris-kai/cc/groups/{gid}/members",
                          headers=BRIDGE,
                          json={"account_id": member["account_id"], "role": "admin"})
        assert add.json()["added"] is True
        # role
        role = client.put(f"/api/juris-kai/cc/groups/{gid}/members/{member['account_id']}",
                          headers=BRIDGE, json={"role": "member"})
        assert role.json()["success"] is True
        # detail shows members
        detail = client.get(f"/api/juris-kai/cc/groups/{gid}", headers=BRIDGE).json()
        assert len(detail["members"]) == 2
        # remove
        rm = client.delete(f"/api/juris-kai/cc/groups/{gid}/members/{member['account_id']}",
                           headers=BRIDGE)
        assert rm.json()["removed"] is True

    def test_bulk_add_and_user_search(self, client):
        mgr = _mgr()
        owner = mgr.create_account("o2@example.com", "O2", "monthly_pro")
        u1 = mgr.create_account("u1@example.com", "U1", "free_trial")
        u2 = mgr.create_account("u2@example.com", "U2", "free_trial")
        gid = client.post("/api/juris-kai/cc/groups", headers=BRIDGE,
                          json={"name": "Bulk", "account_id": owner["account_id"]}
                          ).json()["group"]["group_id"]
        bulk = client.post(f"/api/juris-kai/cc/groups/{gid}/members/bulk",
                           headers=BRIDGE,
                           json={"account_ids": [u1["account_id"], u2["account_id"]]})
        assert bulk.json()["added"] == 2
        users = client.get("/api/juris-kai/cc/users/search?q=u1", headers=BRIDGE).json()
        assert users["users"] and users["users"][0]["email"] == "u1@example.com"

    def test_group_audit_and_reports(self, client):
        mgr = _mgr()
        owner = mgr.create_account("aud@example.com", "Aud", "monthly_pro")
        gid = client.post("/api/juris-kai/cc/groups", headers=BRIDGE,
                          json={"name": "Aud", "account_id": owner["account_id"]}
                          ).json()["group"]["group_id"]
        mgr.db.execute(
            "INSERT INTO juris_document_analyses "
            "(analysis_id, account_id, document_name, page_count, cost_ghs, status) "
            "VALUES ('a1', ?, 'Companies Act 2019', 1, 2.0, 'completed')",
            (owner["account_id"],))
        mgr.db.commit()
        r = client.post(f"/api/juris-kai/cc/groups/{gid}/audit", headers=BRIDGE)
        body = r.json()
        assert body["success"] is True
        assert body["report"]["document_count"] == 1
        reports = client.get(f"/api/juris-kai/cc/groups/{gid}/reports",
                             headers=BRIDGE).json()
        assert reports["count"] == 1
