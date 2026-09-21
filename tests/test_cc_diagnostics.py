"""Tests for the Command Center diagnostics aggregate (``/api/diagnostics``).

Roadmap gap: ``/api/diagnostics`` returned 404 even though the CC needs one
operator-gated roll-up of observability + circuit breakers + telemetry +
provider health. These tests pin the contract and the auth gate.
"""

import pytest
from fastapi.testclient import TestClient

IDENT = {"X-Kai-User": "tester", "X-Kai-User-Id": "t1"}


@pytest.fixture
def client():
    from core.api import app
    return TestClient(app)


def test_diagnostics_requires_operator(client):
    r = client.get("/api/diagnostics")
    assert r.status_code == 401


def test_diagnostics_aggregates_all_sections(client, monkeypatch):
    import core.ai.circuit_breaker as cb

    monkeypatch.setattr(cb, "list_all_breakers", lambda: [
        {"provider": "gemini", "state": "closed", "consecutive_failures": 0},
    ])

    r = client.get("/api/diagnostics", headers=IDENT)
    assert r.status_code == 200
    body = r.json()
    assert body["schema"] == "diagnostics/1"
    assert body["observability"]["schema"] == "observability/1"
    assert body["telemetry"]["schema"] == 1
    assert isinstance(body["providers"], dict)
    assert body["circuit_breakers"][0]["provider"] == "gemini"
    assert "generated_at" in body


def test_diagnostics_enriches_open_breaker_with_cooldown(client, monkeypatch):
    import core.ai.circuit_breaker as cb

    monkeypatch.setattr(cb, "list_all_breakers", lambda: [
        {"provider": "groq", "state": "open", "consecutive_failures": 3,
         "tripped_at": "2026-09-21T00:00:00+00:00", "cooldown_seconds": 300},
    ])

    r = client.get("/api/diagnostics", headers=IDENT)
    assert r.status_code == 200
    breaker = r.json()["circuit_breakers"][0]
    assert breaker["provider"] == "groq"
    assert "cooldown_remaining_seconds" in breaker


def test_diagnostics_identity_headers_accepted(client):
    # Auth-proxy identity headers are the third accepted operator credential.
    r = client.get("/api/diagnostics", headers={
        "X-Kai-User": "proxy@kai", "X-Kai-User-Id": "proxy"})
    assert r.status_code == 200
