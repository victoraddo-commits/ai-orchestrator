"""TDD tests for institutional seats (Phase 7, Task 5).

An organisation buys N seats; each seat is redeemed with a seat code, which
joins the redeemer to the org plan (per-seat quota) and records an org
membership. The org admin gets a usage view (``org_usage`` / ``GET /org/usage``).
Only the minimum data needed is stored (DPA-safe).
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


def _org():
    return _mgr().create_account("org@lawschool.edu.gh", "Law School",
                                 "institution")


def _user(n):
    acct = _mgr().get_or_create(f"seat-user-{n}", f"Student {n}")
    return acct


# ── create / redeem ───────────────────────────────────────────────────────

class TestSeatCodes:
    def test_create_seat_code(self):
        org = _org()
        res = _mgr().create_seat_code(org["account_id"], seats=3)
        assert res["success"] is True
        assert res["code"].startswith("SEAT-")
        assert res["seats"] == 3
        assert res["tier"] == "institution"

    def test_redeem_joins_org_plan(self):
        org = _org()
        code = _mgr().create_seat_code(org["account_id"], seats=1)["code"]
        user = _user(1)
        res = _mgr().redeem_seat_code(code, user["account_id"])
        assert res["success"] is True
        assert res["org_account_id"] == org["account_id"]
        sub = _mgr().get_active_subscription(user["account_id"])
        assert sub["tier"] == "institution"

    def test_redeem_twice_is_idempotent(self):
        org = _org()
        code = _mgr().create_seat_code(org["account_id"], seats=1)["code"]
        user = _user(2)
        assert _mgr().redeem_seat_code(code, user["account_id"])["success"]
        again = _mgr().redeem_seat_code(code, user["account_id"])
        assert again["success"] is True
        assert again.get("already_member") is True

    def test_seat_inventory_is_capped(self):
        org = _org()
        code = _mgr().create_seat_code(org["account_id"], seats=2)["code"]
        assert _mgr().redeem_seat_code(code, _user(3)["account_id"])["success"]
        assert _mgr().redeem_seat_code(code, _user(4)["account_id"])["success"]
        exhausted = _mgr().redeem_seat_code(code, _user(5)["account_id"])
        assert exhausted["success"] is False
        assert "exhaust" in exhausted["error"].lower()

    def test_invalid_code_fails_gracefully(self):
        user = _user(6)
        res = _mgr().redeem_seat_code("SEAT-NOPE", user["account_id"])
        assert res["success"] is False
        assert res["error"]

    def test_revoked_code_cannot_be_redeemed(self):
        org = _org()
        code = _mgr().create_seat_code(org["account_id"], seats=1)["code"]
        assert _mgr().revoke_seat_code(code, org["account_id"])["success"]
        res = _mgr().redeem_seat_code(code, _user(7)["account_id"])
        assert res["success"] is False

    def test_expired_code_cannot_be_redeemed(self):
        org = _org()
        code = _mgr().create_seat_code(
            org["account_id"], seats=1,
            expires_at="2000-01-01T00:00:00+00:00")["code"]
        res = _mgr().redeem_seat_code(code, _user(8)["account_id"])
        assert res["success"] is False


# ── leave ─────────────────────────────────────────────────────────────────

class TestLeave:
    def test_leave_reverts_to_previous_tier(self):
        org = _org()
        code = _mgr().create_seat_code(org["account_id"], seats=1)["code"]
        user = _user(9)
        _mgr().redeem_seat_code(code, user["account_id"])
        assert _mgr().get_active_subscription(
            user["account_id"])["tier"] == "institution"

        res = _mgr().leave_org(user["account_id"])
        assert res["success"] is True
        assert _mgr().get_active_subscription(
            user["account_id"])["tier"] == "free_trial"

    def test_leave_without_membership_is_safe(self):
        res = _mgr().leave_org(_user(10)["account_id"])
        assert res["success"] is True
        assert res.get("removed") in (False, 0)


# ── org usage ─────────────────────────────────────────────────────────────

class TestOrgUsage:
    def test_org_usage_reports_seats_and_members(self):
        org = _org()
        code = _mgr().create_seat_code(org["account_id"], seats=5)["code"]
        u1, u2 = _user(11), _user(12)
        _mgr().redeem_seat_code(code, u1["account_id"])
        _mgr().redeem_seat_code(code, u2["account_id"])

        usage = _mgr().org_usage(org["account_id"])
        assert usage["org_account_id"] == org["account_id"]
        assert usage["seats_total"] == 5
        assert usage["seats_used"] == 2
        assert len(usage["members"]) == 2
        member_ids = {m["account_id"] for m in usage["members"]}
        assert member_ids == {u1["account_id"], u2["account_id"]}

    def test_org_usage_unknown_org_is_safe(self):
        usage = _mgr().org_usage("nope")
        assert usage["seats_total"] == 0
        assert usage["seats_used"] == 0
        assert usage["members"] == []

    def test_list_seat_codes(self):
        org = _org()
        _mgr().create_seat_code(org["account_id"], seats=1, note="cohort-a")
        codes = _mgr().list_seat_codes(org["account_id"])
        assert len(codes) == 1
        assert codes[0]["note"] == "cohort-a"
        # The raw code is needed by the org admin to hand out seats.
        assert codes[0]["code"].startswith("SEAT-")


# ── CC endpoint ───────────────────────────────────────────────────────────

class TestOrgUsageEndpoint:
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

    def test_org_usage_requires_credentials(self, monkeypatch):
        client = self._client(monkeypatch)
        assert client.get(
            "/api/juris-kai/cc/org/usage?org_account_id=x"
        ).status_code == 401

    def test_org_usage_returns_seats(self, monkeypatch):
        client = self._client(monkeypatch)
        org = _org()
        _mgr().create_seat_code(org["account_id"], seats=4)
        r = client.get(
            f"/api/juris-kai/cc/org/usage?org_account_id={org['account_id']}",
            headers={"Authorization": "Bearer test-bridge-token"})
        body = r.json()
        assert body["seats_total"] == 4
        assert body["seats_used"] == 0
