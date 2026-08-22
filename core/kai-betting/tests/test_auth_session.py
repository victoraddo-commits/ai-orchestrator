from fastapi.testclient import TestClient

from core.kai_betting.api_server import app
from core.kai_betting.db import get_db


def _register_and_login(client, email="user@example.com", password="Passw0rd1", forwarded_for=None):
    """Register + log in, isolating the login route's rate limiter per-test via a
    unique X-Forwarded-For (defaults to the email) so tests never share a bucket."""
    headers = {"X-Forwarded-For": forwarded_for or email}
    client.post("/api/betting/auth/register", json={"email": email, "password": password})
    resp = client.post("/api/betting/auth/login", json={"email": email, "password": password}, headers=headers)
    return resp.json()["data"]


def test_login_returns_user_and_token(fresh_db):
    client = TestClient(app)
    data = _register_and_login(client)
    assert "token" in data
    assert data["user"]["email"] == "user@example.com"
    assert "password_hash" not in data["user"]


def test_login_wrong_password_401(fresh_db):
    client = TestClient(app)
    client.post("/api/betting/auth/register", json={"email": "x@example.com", "password": "Passw0rd1"})
    resp = client.post(
        "/api/betting/auth/login",
        json={"email": "x@example.com", "password": "wrong"},
        headers={"X-Forwarded-For": "x@example.com"},
    )
    assert resp.status_code == 401


def test_logout_deletes_the_session_row(fresh_db):
    client = TestClient(app)
    data = _register_and_login(client, email="logout@example.com")
    token = data["token"]

    resp = client.post("/api/betting/auth/logout", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200

    with get_db() as db:
        remaining = db.execute("SELECT COUNT(*) as c FROM sessions").fetchone()
    assert remaining["c"] == 0


def test_logout_with_missing_token_is_a_no_op(fresh_db):
    client = TestClient(app)
    resp = client.post("/api/betting/auth/logout")
    assert resp.status_code == 200


def test_login_rate_limited_after_too_many_attempts(fresh_db):
    client = TestClient(app)
    email = "rate-limited@example.com"
    client.post("/api/betting/auth/register", json={"email": email, "password": "Passw0rd1"})
    headers = {"X-Forwarded-For": "203.0.113.5"}
    for _ in range(20):
        r = client.post("/api/betting/auth/login", json={"email": email, "password": "wrong"}, headers=headers)
        assert r.status_code == 401
    r = client.post("/api/betting/auth/login", json={"email": email, "password": "wrong"}, headers=headers)
    assert r.status_code == 429


def test_admin_route_rejects_missing_auth(fresh_db):
    client = TestClient(app)
    resp = client.get("/api/betting/admin/users")
    assert resp.status_code == 401


def test_admin_route_requires_admin_session(fresh_db):
    client = TestClient(app)
    data = _register_and_login(client, email="plain@example.com")
    resp = client.get("/api/betting/admin/users", headers={"Authorization": f"Bearer {data['token']}"})
    assert resp.status_code == 403


def test_admin_route_allows_admin_session(fresh_db):
    client = TestClient(app)
    data = _register_and_login(client, email="admin@example.com")
    with get_db() as db:
        db.execute("UPDATE users SET is_admin = 1 WHERE email = ?", ("admin@example.com",))
        db.commit()

    resp = client.get("/api/betting/admin/users", headers={"Authorization": f"Bearer {data['token']}"})
    assert resp.status_code == 200


def test_preferences_owner_can_access(fresh_db):
    client = TestClient(app)
    data = _register_and_login(client, email="pref-owner@example.com")
    user_id = data["user"]["id"]
    resp = client.get(f"/api/betting/preferences/{user_id}", headers={"Authorization": f"Bearer {data['token']}"})
    assert resp.status_code == 200


def test_preferences_missing_auth_401(fresh_db):
    client = TestClient(app)
    data = _register_and_login(client, email="pref-noauth@example.com")
    resp = client.get(f"/api/betting/preferences/{data['user']['id']}")
    assert resp.status_code == 401


def test_preferences_other_user_forbidden(fresh_db):
    client = TestClient(app)
    data_a = _register_and_login(client, email="pref-a@example.com")
    data_b = _register_and_login(client, email="pref-b@example.com")
    resp = client.get(
        f"/api/betting/preferences/{data_b['user']['id']}",
        headers={"Authorization": f"Bearer {data_a['token']}"},
    )
    assert resp.status_code == 403


def test_admin_can_access_other_users_preferences(fresh_db):
    client = TestClient(app)
    admin_data = _register_and_login(client, email="pref-admin@example.com")
    other_data = _register_and_login(client, email="pref-other@example.com")
    with get_db() as db:
        db.execute("UPDATE users SET is_admin = 1 WHERE email = ?", ("pref-admin@example.com",))
        db.commit()

    resp = client.get(
        f"/api/betting/preferences/{other_data['user']['id']}",
        headers={"Authorization": f"Bearer {admin_data['token']}"},
    )
    assert resp.status_code == 200


def test_update_preferences_other_user_forbidden(fresh_db):
    client = TestClient(app)
    data_a = _register_and_login(client, email="prefput-a@example.com")
    data_b = _register_and_login(client, email="prefput-b@example.com")
    body = {
        "selected_sports": "football", "selected_markets": "match_result",
        "notification_picks": True, "notification_results": True,
        "notification_odds_groups": True, "notification_daily_summary": True,
        "telegram_notifications": False,
    }
    resp = client.put(
        f"/api/betting/preferences/{data_b['user']['id']}",
        json=body,
        headers={"Authorization": f"Bearer {data_a['token']}"},
    )
    assert resp.status_code == 403


def test_payments_ownership_enforced(fresh_db):
    client = TestClient(app)
    data_a = _register_and_login(client, email="pay-a@example.com")
    data_b = _register_and_login(client, email="pay-b@example.com")
    resp = client.get(
        f"/api/betting/payments/{data_b['user']['id']}",
        headers={"Authorization": f"Bearer {data_a['token']}"},
    )
    assert resp.status_code == 403


def test_subscriptions_ownership_enforced(fresh_db):
    client = TestClient(app)
    data_a = _register_and_login(client, email="sub-a@example.com")
    data_b = _register_and_login(client, email="sub-b@example.com")
    resp = client.get(
        f"/api/betting/subscriptions/{data_b['user']['id']}",
        headers={"Authorization": f"Bearer {data_a['token']}"},
    )
    assert resp.status_code == 403


def test_purchase_subscription_ownership_enforced(fresh_db):
    client = TestClient(app)
    data_a = _register_and_login(client, email="buy-a@example.com")
    data_b = _register_and_login(client, email="buy-b@example.com")
    resp = client.post(
        f"/api/betting/subscriptions/purchase?user_id={data_b['user']['id']}",
        headers={"Authorization": f"Bearer {data_a['token']}"},
        json={
            "plan_key": "daily", "payment_provider": "hubtel",
            "payment_method": "mobile_money", "phone_number": "+233000000000",
            "currency": "GHS",
        },
    )
    assert resp.status_code == 403
