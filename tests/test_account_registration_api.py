"""Command Center — Universal Account Registration API (roadmap STEP 8).

Integration tests for ``core/cc_account_routes.py``: every endpoint's auth
gate, validation, response shape, and the hard rule that **no secret, OTP code
or password may appear in any response**. Isolation: memory dir → tmp; Telegram
paging stubbed so no network is touched.
"""

import pytest
from fastapi.testclient import TestClient

OP = {"X-Kai-User": "cc@kai", "X-Kai-User-Id": "cc"}
OTP_CODE = "482913"
PASSWORD = "hunter2-not-real"


@pytest.fixture(autouse=True)
def isolated_memory(monkeypatch, tmp_path):
    monkeypatch.setenv("AI_ORCHESTRATOR_MEMORY_DIR", str(tmp_path))
    # Never let a human-action page hit the network during tests.
    import core.notify.human_action as ha

    monkeypatch.setattr(ha, "_send_telegram", lambda text: None)
    return tmp_path


@pytest.fixture
def client():
    from core.api import app

    return TestClient(app)


# ---------------------------------------------------------------------------
# seed helpers
# ---------------------------------------------------------------------------


def _seed_account(email="kai-amazon@example.com"):
    from core.accounts import AccountCreate, create_account

    return create_account(AccountCreate(
        provider="amazon", account_type="customer", username="kai-amazon",
        email=email, phone="+233248077604"))


def _seed_sms():
    from core.sms.adapter import RawSms
    from core.sms.manager import ingest_raw, register_line

    register_line("+233248077604", "mtn")
    return ingest_raw(RawSms(
        from_number="AmznID", to_number="+233248077604",
        body=f"Your Amazon verification code is {OTP_CODE}",
        timestamp="2026-09-30T10:00:00Z"))


def _seed_paused_session(account_id):
    from core.notify import human_action
    from core.onboarding import store
    from core.onboarding.schema import now_iso

    mission_id = "mis-seed-1"
    action_id = human_action.request_human_action(
        "CONNECT_PHONE_FOR_SMS", mission_id,
        instructions="Connect your phone so KAI can relay the SMS code.",
        provider="amazon", notify=False)
    now = now_iso()
    record = {
        "mission_id": mission_id,
        "objective": "Onboard KAI with provider amazon",
        "provider_id": "amazon",
        "identity_id": "ident-seed-1",
        "current_state": "PAUSED_HUMAN",
        "status": "paused_human",
        "completed_states": [
            "DISCOVER_PROVIDER", "CHECK_EXISTING_ACCOUNT", "SELECT_IDENTITY",
            "SELECT_PROVIDER_ADAPTER", "CHECK_REQUIREMENTS", "PREPARE_BROWSER",
            "START_REGISTRATION", "ENTER_INFORMATION", "EMAIL_VERIFICATION",
        ],
        "skipped_states": ["MFA"],
        "pre_pause_state": "SMS_VERIFICATION",
        "human_action_required": True,
        "human_action_id": action_id,
        "human_action_type": "CONNECT_PHONE_FOR_SMS",
        "requirements": {"email": True, "phone": True, "captcha": True,
                         "mfa": False, "kyc": False, "payment": True},
        "evidence": [
            {"state": "DISCOVER_PROVIDER", "kind": "provider_descriptor",
             "at": now, "hash": "deadbeef",
             "detail": {"automation_policy": "UNKNOWN"}},
            {"state": "EMAIL_VERIFICATION", "kind": "email_verification",
             "at": now, "ref": "msg-1", "detail": {"message_id": "msg-1"}},
        ],
        "errors": [],
        "account_id": account_id,
        "vault_reference": f"secrets/accounts/amazon/{account_id}",
        "created_at": now,
        "updated_at": now,
        "completed_at": None,
    }

    def _mutate(records):
        records = [r for r in records if r.get("mission_id") != mission_id]
        records.append(record)
        return records

    store.update_sessions(_mutate)
    return record, action_id


# ---------------------------------------------------------------------------
# auth gate
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", [
    "/api/onboarding", "/api/accounts", "/api/providers",
    "/api/sms/inbox", "/api/notifications",
])
def test_reads_require_operator(client, path):
    assert client.get(path).status_code == 401


def test_writes_require_operator(client):
    r = client.post("/api/onboarding", json={"provider": "amazon"})
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# onboarding
# ---------------------------------------------------------------------------


def test_start_onboarding_amazon_pauses_for_human(client):
    r = client.post("/api/onboarding", json={"provider": "amazon",
                                             "account_type": "customer"}, headers=OP)
    assert r.status_code == 200, r.text
    session = r.json()["session"]
    assert session["provider_id"] == "amazon"
    assert session["human_action_required"] is True
    assert session["human_action_id"]
    assert session["status"] in ("paused_human", "active")
    assert session["progress"]["total"] > 0


def test_start_onboarding_unknown_provider_404(client):
    r = client.post("/api/onboarding", json={"provider": "nope-not-real"}, headers=OP)
    assert r.status_code == 404


def test_start_onboarding_validation(client):
    assert client.post("/api/onboarding", json={"provider": ""}, headers=OP).status_code == 422
    assert client.post("/api/onboarding", json={"nope": 1}, headers=OP).status_code == 422
    assert client.post("/api/onboarding", json={"provider": "x" * 500}, headers=OP).status_code == 422


def test_list_and_detail_onboarding(client):
    account = _seed_account()
    _seed_paused_session(account.account_id)

    listing = client.get("/api/onboarding", headers=OP)
    assert listing.status_code == 200
    body = listing.json()
    assert body["ok"] is True
    ids = [s["id"] for s in body["sessions"]]
    assert "mis-seed-1" in ids
    row = next(s for s in body["sessions"] if s["id"] == "mis-seed-1")
    assert row["status"] == "paused_human"
    assert row["vault_ref_present"] is True

    detail = client.get("/api/onboarding/mis-seed-1", headers=OP)
    assert detail.status_code == 200, detail.text
    session = detail.json()["session"]
    states = {s["state"]: s["status"] for s in session["states"]}
    assert states["PAUSED_HUMAN"] == "current"
    assert states["SMS_VERIFICATION"] == "pending_human"
    assert states["DISCOVER_PROVIDER"] == "completed"
    assert states["MFA"] == "skipped"
    assert session["human_action"]["instructions"]
    assert any(e.get("kind") == "email_verification" for e in session["evidence"])


def test_onboarding_detail_404(client):
    assert client.get("/api/onboarding/does-not-exist", headers=OP).status_code == 404


def test_resume_and_cancel(client):
    started = client.post("/api/onboarding", json={"provider": "amazon"}, headers=OP).json()
    mid = started["session"]["id"]

    resumed = client.post(f"/api/onboarding/{mid}/resume", headers=OP)
    assert resumed.status_code == 200
    assert resumed.json()["session"]["id"] == mid

    cancelled = client.post(f"/api/onboarding/{mid}/cancel",
                            json={"reason": "operator test"}, headers=OP)
    assert cancelled.status_code == 200
    assert cancelled.json()["session"]["status"] == "cancelled"
    assert client.post("/api/onboarding/missing/resume", headers=OP).status_code == 404


def test_human_action_complete_resumes(client):
    started = client.post("/api/onboarding", json={"provider": "amazon"}, headers=OP).json()
    mid = started["session"]["id"]
    action_id = started["session"]["human_action_id"]
    assert action_id

    r = client.post(f"/api/onboarding/{mid}/human-action/{action_id}/complete",
                    json={"reason": "confirmed"}, headers=OP)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["action_completed"] == action_id
    assert body["session"]["id"] == mid


def test_human_action_unknown_404(client):
    started = client.post("/api/onboarding", json={"provider": "amazon"}, headers=OP).json()
    mid = started["session"]["id"]
    r = client.post(f"/api/onboarding/{mid}/human-action/hact-nope/complete",
                    headers=OP)
    assert r.status_code == 404


def test_human_action_mismatch_400(client):
    _seed_paused_session("acct-x")
    started = client.post("/api/onboarding", json={"provider": "amazon"}, headers=OP).json()
    other = started["session"]["human_action_id"]
    r = client.post(f"/api/onboarding/mis-seed-1/human-action/{other}/complete",
                    headers=OP)
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# account registry
# ---------------------------------------------------------------------------


def test_accounts_list_and_detail(client):
    account = _seed_account()
    listing = client.get("/api/accounts", headers=OP)
    assert listing.status_code == 200
    body = listing.json()
    assert body["count"] >= 1
    row = next(a for a in body["accounts"] if a["account_id"] == account.account_id)
    assert row["provider"] == "amazon"
    assert row["vault_ref_present"] is True
    assert row["vault_ref_hint"].startswith("secrets/accounts/amazon/")

    detail = client.get(f"/api/accounts/{account.account_id}", headers=OP)
    assert detail.status_code == 200
    assert detail.json()["account"]["vault_ref_present"] is True

    assert client.get("/api/accounts/nope", headers=OP).status_code == 404


def test_accounts_are_redacted(client):
    account = _seed_account(email="secret.user@example.com")
    r = client.get("/api/accounts", headers=OP)
    text = r.text
    assert "secret.user@example.com" not in text
    assert "hunter2" not in text
    acct = next(a for a in r.json()["accounts"] if a["account_id"] == account.account_id)
    assert acct["email_masked"].endswith("@example.com")
    assert acct["email_masked"].startswith("s")
    assert acct["phone_masked"].startswith("***")


# ---------------------------------------------------------------------------
# providers
# ---------------------------------------------------------------------------


def test_providers_honest_policy_posture(client):
    r = client.get("/api/providers", headers=OP)
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True and body["count"] >= 1
    amazon = next(p for p in body["providers"] if p["provider_id"] == "amazon")
    assert amazon["automation_policy"] == "UNKNOWN"
    assert amazon["policy_source"] and "Amazon" in amazon["policy_source"]
    assert amazon["requires_human"] is True
    assert amazon["readiness"] == "human_required"
    assert "customer" in amazon["account_types"]
    assert amazon["requirements"]["captcha"] is True


# ---------------------------------------------------------------------------
# SMS inbox + notifications
# ---------------------------------------------------------------------------


def test_sms_inbox_redacts_otp(client):
    _seed_sms()
    r = client.get("/api/sms/inbox", headers=OP)
    assert r.status_code == 200
    body = r.json()
    assert body["count"] >= 1
    msg = body["messages"][0]
    assert msg["classification"] == "otp"
    assert msg["otp_present"] is True
    assert "body" not in msg
    # The code value must never appear anywhere in the response.
    assert OTP_CODE not in r.text


def test_sms_inbox_rejects_bad_classification(client):
    assert client.get("/api/sms/inbox?classification=bogus", headers=OP).status_code == 400


def test_notifications_queue(client):
    _seed_paused_session("acct-q")
    r = client.get("/api/notifications", headers=OP)
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert any(n["mission_id"] == "mis-seed-1" for n in body["notifications"])
    assert OTP_CODE not in r.text


# ---------------------------------------------------------------------------
# leak-prevention sweep across every endpoint
# ---------------------------------------------------------------------------


def test_no_secret_leak_across_all_endpoints(client):
    account = _seed_account(email="leak.test@example.com")
    _seed_sms()
    _seed_paused_session(account.account_id)
    started = client.post("/api/onboarding", json={"provider": "amazon"}, headers=OP).json()
    mid = started["session"]["id"]

    calls = [
        ("get", "/api/onboarding", None),
        ("get", "/api/onboarding/mis-seed-1", None),
        ("get", f"/api/onboarding/{mid}", None),
        ("get", "/api/accounts", None),
        ("get", f"/api/accounts/{account.account_id}", None),
        ("get", "/api/providers", None),
        ("get", "/api/sms/inbox", None),
        ("get", "/api/notifications", None),
    ]
    forbidden = (OTP_CODE, PASSWORD, "leak.test@example.com")
    for method, path, payload in calls:
        resp = getattr(client, method)(path, headers=OP, json=payload) if payload \
            else getattr(client, method)(path, headers=OP)
        assert resp.status_code == 200, (path, resp.status_code)
        text = resp.text
        for needle in forbidden:
            assert needle not in text, f"{needle!r} leaked from {path}"
        # No secret-looking JSON keys in the shaped payload (house secret guard).
        from core.secret_guard import find_secret_fields

        data = resp.json()
        leaked = find_secret_fields(data)
        assert leaked == [], f"secret-looking fields in {path}: {leaked}"
