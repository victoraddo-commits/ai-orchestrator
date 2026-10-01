"""Command Center — Akush Money module (Phase 7, CT111 CT-111 panel).

Tests for ``core/cc_extra_routes.py`` /api/money/* proxy routes: operator
gating (401/403), response shape (mocked akush-core), OpenAPI registration,
the command_center.html panel wiring contract (auditPanelWiring), and the
hard rule that no secret / service token / OTP material appears in responses.
"""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

CC_HTML = Path("/opt/ai-orchestrator/core/kai/command_center.html")

VIEWER_TOKEN = "viewer-session-token-test"
OP = {"X-Kai-User": "cc@kai", "X-Kai-User-Id": "cc"}


@pytest.fixture(autouse=True)
def isolated_memory(monkeypatch, tmp_path):
    monkeypatch.setenv("AI_ORCHESTRATOR_MEMORY_DIR", str(tmp_path))
    import core.notify.human_action as ha

    monkeypatch.setattr(ha, "_send_telegram", lambda text: None)
    return tmp_path


@pytest.fixture
def client():
    from core.api import app

    return TestClient(app)


@pytest.fixture
def viewer_session():
    from core import authz

    return authz.create_session_for("cc-viewer-test", "viewer")


def _as_viewer(viewer_token):
    return {"x-kai-session": viewer_token}


# ---------------------------------------------------------------------------
# mock akush-core
# ---------------------------------------------------------------------------

MOCK_TOKEN_VALUE = "super-secret-cc-service-token-DO-NOT-LEAK"
FAKE_OTP = "981237"


@pytest.fixture
def mock_akush(monkeypatch):
    import core.cc_extra_routes as cce

    calls = []

    def fake_call(path, method="GET", body=None, params=None):
        calls.append((method, path, params))
        if path == "/health":
            return {"status": "ok", "db": "ok", "latency_ms": 2}
        if path == "/api/v1/accounts":
            return {"data": [
                {"id": 1, "name": "MTN MoMo", "institution": "MTN", "kind": "mobile_money",
                 "status": "ACTIVE", "opening_balance": "120.00", "currency": "GHS"},
                {"id": 2, "name": "Absa", "institution": "Absa", "kind": "bank",
                 "status": "CANDIDATE", "opening_balance": None, "currency": "GHS"},
            ]}
        if path == "/api/v1/financial-inbox":
            return {"data": [
                {"id": 25, "status": "pending", "event_kind": "transaction_candidate",
                 "suggested_action": "create_transaction", "confidence": 88.5,
                 "source": "sms", "created_at": "2026-10-01T05:00:00Z",
                 "body": f"OTP {FAKE_OTP}"},
                {"id": 24, "status": "confirmed", "event_kind": "balance_observation",
                 "suggested_action": "none", "confidence": 95.0, "source": "sms",
                 "created_at": "2026-10-01T04:00:00Z"},
            ], "next_cursor": None}
        if path == "/api/v1/anomalies":
            return {"open": 1, "data": [
                {"id": 7, "status": "pending", "suggested_action": "investigate",
                 "severity": "high", "summary": "duplicate debit suspected",
                 "confidence": 70.0, "created_at": "2026-10-01T03:00:00Z"}]}
        if path == "/api/v1/safe-to-spend":
            return {
                "safe_to_spend": "312.00", "until": "2026-10-31",
                "breakdown": {
                    "bills_due": {"amount": "450.00", "items": [
                        {"commitment_id": 3, "name": "ECG", "date": "2026-10-05", "amount": "450.00"}]},
                    "debt_due": {"amount": "120.00", "items": [
                        {"debt_id": 2, "date": "2026-10-06", "amount": "120.00"}]},
                },
            }
        if path == "/api/v1/sms-ingestion-status":
            return {"total_messages": 41, "last_24h": 6, "dead_letters": 0,
                    "last_received": "2026-10-01T06:00:00Z",
                    "last_ingested": "2026-10-01T06:00:00Z"}
        if path.startswith("/api/v1/accounts/") and path.endswith("/reconciliations"):
            return {"data": [{"id": 1, "status": "resolved"}]}
        if path == "/api/v1/security-events":
            return {"data": [{"id": 9, "severity": "warning", "kind": "auth_failure"}]}
        if path.startswith("/api/v1/financial-inbox/") and method == "POST":
            return {"status": "confirmed", "transaction_id": 55}
        raise AssertionError(f"unexpected _akush_call {method} {path}")

    def fake_health():
        return {"status": "ok", "db": "ok", "latency_ms": 2}

    monkeypatch.setattr(cce, "_akush_call", fake_call)
    monkeypatch.setattr(cce, "_akush_health", fake_health)
    # stale token cache must never be used in tests (no vault in tests)
    monkeypatch.setattr(cce, "_akush_service_token", lambda: MOCK_TOKEN_VALUE)
    # bridge state must be hermetic (§70 observability block in overview)
    monkeypatch.setattr("core.money_sms.bridge.load_state", lambda: {
        "counters": {"forwarded": 10, "duplicates": 1, "failed": 2,
                     "skipped_otp": 3, "dropped_overflow": 0},
        "dead_letter": [{"ts": 1.0, "message_id": "m1", "reason": "post_failed"}],
        "last_processed_at": None, "last_failed_at": None,
    })
    return calls


# ---------------------------------------------------------------------------
# operator gating
# ---------------------------------------------------------------------------

def test_overview_requires_auth(client):
    r = client.get("/api/money/overview")
    assert r.status_code == 401


def test_overview_forbids_non_operator(client, viewer_session):
    r = client.get("/api/money/overview", headers=_as_viewer(viewer_session))
    assert r.status_code == 403


def test_inbox_requires_auth(client):
    assert client.get("/api/money/inbox").status_code == 401
    assert client.get("/api/money/accounts").status_code == 401
    assert client.get("/api/money/anomalies").status_code == 401
    assert client.post("/api/money/inbox/25/act", json={"action": "confirm"}).status_code == 401


def test_inbox_act_forbids_non_operator(client, viewer_session):
    r = client.post("/api/money/inbox/25/act", headers=_as_viewer(viewer_session),
                    json={"action": "confirm", "confirmed": True})
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# happy shapes (mocked akush-core)
# ---------------------------------------------------------------------------

def test_overview_happy_shape(client, mock_akush):
    r = client.get("/api/money/overview", headers=OP)
    assert r.status_code == 200
    d = r.json()
    assert d["health"]["status"] == "ok"
    assert d["pwa_url"].startswith("https://192.168.1.118:8095")
    assert d["accounts"]["count"] == 2
    assert d["accounts"]["by_status"].get("ACTIVE") == 1
    assert d["inbox"]["pending"] == 1
    assert d["inbox"]["counts"].get("confirmed") == 1
    assert d["anomalies"]["open"] == 1
    # obligations 7d = 450 + 120
    assert d["obligations_7d"]["count"] == 2
    assert d["obligations_7d"]["total"] == 570.0
    assert d["reconciliation"]["total"] >= 1
    assert d["reconciliation"]["health_pct"] is not None
    assert "backup" in d
    assert "bridge" in d
    assert d["bridge"]["status"] == "degraded"
    assert d["bridge"]["dead_letter_count"] == 1
    assert set(d["sms_ingestion"]) >= {"total_messages", "last_24h", "dead_letters"}


def test_inbox_happy_shape_metadata_only(client, mock_akush):
    r = client.get("/api/money/inbox", headers=OP)
    assert r.status_code == 200
    d = r.json()
    assert d["status_filter"] == "pending"
    assert all(set(i) <= {"id", "status", "event_kind", "suggested_action",
                          "confidence", "source", "created_at"} for i in d["data"])
    assert all("body" not in i for i in d["data"]), "SMS bodies must never be proxied"


def test_accounts_happy_shape(client, mock_akush):
    r = client.get("/api/money/accounts", headers=OP)
    assert r.status_code == 200
    d = r.json()
    assert d["count"] == 2
    assert all(set(a) == {"id", "name", "institution", "kind", "status",
                          "opening_balance", "currency"} for a in d["data"])


def test_anomalies_happy_shape(client, mock_akush):
    r = client.get("/api/money/anomalies", headers=OP)
    assert r.status_code == 200
    d = r.json()
    assert d["open"] == 1
    assert d["data"][0]["severity"] == "high"


def test_inbox_act_requires_confirmed_flag(client, mock_akush):
    r = client.post("/api/money/inbox/25/act", headers=OP,
                    json={"action": "confirm"})
    assert r.status_code == 400
    assert "confirmation" in r.json()["detail"]


def test_inbox_act_rejects_unknown_action(client, mock_akush):
    r = client.post("/api/money/inbox/25/act", headers=OP,
                    json={"action": "explode", "confirmed": True})
    assert r.status_code == 400


def test_inbox_act_happy(client, mock_akush):
    r = client.post("/api/money/inbox/25/act", headers=OP,
                    json={"action": "confirm", "confirmed": True,
                          "payload": {"account_id": 1, "amount": 10}})
    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert r.json()["result"]["transaction_id"] == 55


# ---------------------------------------------------------------------------
# openapi registration
# ---------------------------------------------------------------------------

def test_openapi_has_money_routes(client):
    r = client.get("/openapi.json")
    assert r.status_code == 200
    paths = r.json()["paths"]
    for p in ["/api/money/overview", "/api/money/accounts", "/api/money/inbox",
              "/api/money/anomalies"]:
        assert p in paths, p
    assert "/api/money/inbox/{item_id}/act" in paths


# ---------------------------------------------------------------------------
# panel wiring (auditPanelWiring contract — static checks)
# ---------------------------------------------------------------------------

def _count(html: str, needle: str) -> int:
    return html.count(needle)


def test_panel_wiring_each_exactly_once():
    html = CC_HTML.read_text()
    assert _count(html, 'data-hash="akush"') == 1
    assert _count(html, 'id="panel-akush"') == 1
    assert _count(html, "akush:'Akush Money'") == 1
    assert _count(html, "akush:loadAkush") == 1
    assert _count(html, "async function loadAkush(") == 1
    assert _count(html, "async function akushInboxAct(") == 1


def test_panel_wiring_deep_link_present():
    html = CC_HTML.read_text()
    assert "AKUSH_PWA_URL = 'http://proxmox-b.tail82a9ca.ts.net:8770/'" in html
    assert "Open full Akush Money" in html


def test_panel_wiring_no_drift_names():
    """Simulate auditPanelWiring(): nav/panel/title/loader must agree on `akush`."""
    html = CC_HTML.read_text()
    nav = 'data-hash="akush"' in html
    panel = 'id="panel-akush"' in html
    title = "akush:'Akush Money'" in html
    loader = "akush:loadAkush" in html
    assert nav and panel and title and loader


# ---------------------------------------------------------------------------
# secret-leak guards
# ---------------------------------------------------------------------------

def test_no_service_token_in_responses(client, mock_akush):
    for path in ["/api/money/overview", "/api/money/accounts",
                 "/api/money/inbox", "/api/money/anomalies"]:
        r = client.get(path, headers=OP)
        assert r.status_code == 200
        assert MOCK_TOKEN_VALUE not in r.text, f"service token leaked via {path}"


def test_no_otp_in_responses(client, mock_akush):
    r = client.get("/api/money/inbox", headers=OP)
    assert r.status_code == 200
    assert FAKE_OTP not in r.text
    r2 = client.get("/api/money/overview", headers=OP)
    assert FAKE_OTP not in r2.text


def test_inbox_act_response_shaped_not_passthrough(client, mock_akush):
    r = client.post("/api/money/inbox/25/act", headers=OP,
                    json={"action": "confirm", "confirmed": True})
    body = r.json()
    assert set(body) == {"ok", "result"}
