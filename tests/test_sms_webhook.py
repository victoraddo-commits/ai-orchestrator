"""core.sms webhook server — token auth + ingest over HTTP (offline)."""

import pytest
from fastapi.testclient import TestClient

from core.sms import manager
from core.sms.server import create_app


@pytest.fixture(autouse=True)
def isolated_memory(monkeypatch, tmp_path):
    monkeypatch.setenv("AI_ORCHESTRATOR_MEMORY_DIR", str(tmp_path))
    return tmp_path


@pytest.fixture
def client():
    app = create_app(token="webhook-secret")
    return TestClient(app)


def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_webhook_rejects_missing_token(client):
    resp = client.post("/webhook/sms", json={"from": "12345", "to": "+233248077604", "body": "hi"})
    assert resp.status_code == 401


def test_webhook_rejects_wrong_token(client):
    resp = client.post("/webhook/sms",
                       json={"from": "12345", "to": "+233248077604", "body": "hi"},
                       headers={"Authorization": "Bearer nope"})
    assert resp.status_code == 401


def test_webhook_accepts_and_normalizes(client):
    resp = client.post("/webhook/sms",
                       json={"from": "12345", "to": "+233248077604",
                             "body": "Your verification code is 482913",
                             "timestamp": "2026-09-30T10:00:00Z"},
                       headers={"Authorization": "Bearer webhook-secret"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["otp_present"] is True
    assert body["classification"] == "otp"
    # the OTP value is never echoed back
    assert "482913" not in resp.text


def test_webhook_rejects_malformed_payload(client):
    resp = client.post("/webhook/sms", json={"body": "no sender"},
                       headers={"Authorization": "Bearer webhook-secret"})
    assert resp.status_code == 422
