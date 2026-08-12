"""Tests for 15C: Platform Audit Log — /audit endpoint.

Covers: merged feed from all 9 sources, filtering, CSV export,
date range, and auth gating.
"""

import json
import pytest
from io import StringIO
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def client():
    from core.api import app
    return TestClient(app)


@pytest.fixture
def dashboard_auth():
    """Return Basic auth header for the dashboard credentials."""
    import base64
    from core.api import _load_dashboard_credentials
    creds = _load_dashboard_credentials()
    encoded = base64.b64encode(f"{creds['username']}:{creds['password']}".encode()).decode()
    return {"Authorization": f"Basic {encoded}"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_build(build_id="b1", name="test-app", status="completed",
                timestamp="2026-08-01T12:00:00Z", operator="kai"):
    return {
        "build_id": build_id, "name": name, "status": status,
        "timestamp": timestamp, "generated_by": operator,
    }


def _make_approval(aid="a1", status="approved", created="2026-08-02T12:00:00Z"):
    return {
        "id": aid, "status": status, "created": created,
        "history": [{"timestamp": created, "operator": "admin", "status": status}],
        "action": "deploy", "trace_id": "t1", "service": "build",
    }


def _make_decision(did="d1", status="executed", created="2026-08-03T12:00:00Z"):
    return {
        "id": did, "status": status, "created": created,
        "history": [{"timestamp": created, "operator": "kai", "status": status}],
        "recommended_action": "restart service", "cause_probability": 0.85,
    }


def _make_incident(iid="i1", status="open", created="2026-08-04T12:00:00Z",
                   issue="disk full", severity="critical", service="proxmox"):
    return {
        "id": iid, "status": status, "created": created,
        "history": [{"timestamp": created, "operator": "system", "status": status}],
        "issue": issue, "severity": severity, "service": service,
    }


def _make_gateway(consumer="test-key", provider="local", status_code=200,
                  timestamp="2026-08-05T12:00:00Z"):
    return {
        "trace_id": "gw-1", "consumer": consumer, "model": "auto",
        "provider": provider, "duration_ms": 150, "status_code": status_code,
        "error": None, "timestamp": timestamp,
    }


def _make_secret(action="read", provider="gemini", success=True,
                  timestamp="2026-08-06T12:00:00Z"):
    return {
        "action": action, "provider": provider, "success": success,
        "detail": "api key accessed", "timestamp": timestamp,
    }


def _make_ai_usage(provider="qwen4_text", task_type="planning", success=True,
                   timestamp="2026-08-07T12:00:00Z"):
    return {
        "provider": provider, "task_type": task_type, "success": success,
        "duration_ms": 250, "error": None, "timestamp": timestamp,
        "description": "plan architecture", "cost": 0.001,
    }


def _make_remediation(action="restart_container", result="completed",
                      timestamp="2026-08-08T12:00:00Z"):
    return {
        "action": action, "result": result, "timestamp": timestamp,
        "incident": "inc-1",
    }


def _make_verification(status="pass", service="proxmox", remaining_findings=0,
                       timestamp="2026-08-09T12:00:00Z"):
    return {
        "status": status, "service": service,
        "remaining_findings": remaining_findings, "timestamp": timestamp,
    }


def _patch_all_sources(monkeypatch, **overrides):
    """Patch core.api.load to return test data for all 9 sources."""
    import core.api as api

    def fake_load(filename, **kw):
        sources = {
            "build_history.json": {"records": overrides.get("builds", [_make_build()])},
            "approval_queue.json": {"records": overrides.get("approvals", [_make_approval()])},
            "decisions.json": {"records": overrides.get("decisions", [_make_decision()])},
            "incidents.json": {"records": overrides.get("incidents", [_make_incident()])},
            "gateway_audit.json": {"records": overrides.get("gateway", [_make_gateway()])},
            "secret_access_audit.json": {"records": overrides.get("secrets", [_make_secret()])},
            "ai_usage_history.json": {"records": overrides.get("ai", [_make_ai_usage()])},
            "remediation_history.json": {"records": overrides.get("remediations", [_make_remediation()])},
            "verification_history.json": {"records": overrides.get("verifications", [_make_verification()])},
        }
        return sources.get(filename, {"records": []})

    monkeypatch.setattr(api, "load", fake_load)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestAuditAuth:
    """Auth gating: /audit requires dashboard login."""

    def test_401_without_auth(self, client):
        resp = client.get("/audit")
        assert resp.status_code == 401


class TestMergedFeed:
    """All 9 sources surface in the merged feed."""

    def test_all_sources_appear_in_feed(self, client, dashboard_auth, monkeypatch):
        _patch_all_sources(monkeypatch)

        resp = client.get("/audit", headers=dashboard_auth)
        assert resp.status_code == 200
        data = resp.json()
        sources = {e["source"] for e in data["entries"]}
        assert sources == {"build", "approval", "decision", "incident",
                           "gateway", "secret", "ai", "remediation", "verification"}

    def test_total_and_returned_match(self, client, dashboard_auth, monkeypatch):
        _patch_all_sources(monkeypatch)

        resp = client.get("/audit?limit=3", headers=dashboard_auth)
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] >= data["returned"]
        assert data["returned"] <= 3

    def test_every_entry_has_source_ip(self, client, dashboard_auth, monkeypatch):
        _patch_all_sources(monkeypatch)

        resp = client.get("/audit", headers=dashboard_auth)
        assert resp.status_code == 200
        for entry in resp.json()["entries"]:
            assert "source_ip" in entry


class TestFiltering:
    """Filtering by actor, source, action, and date range."""

    def test_filter_by_source(self, client, dashboard_auth, monkeypatch):
        _patch_all_sources(monkeypatch)

        resp = client.get("/audit?source=build_history", headers=dashboard_auth)
        assert resp.status_code == 200
        sources = {e["source"] for e in resp.json()["entries"]}
        assert sources == {"build"}

    def test_filter_by_actor(self, client, dashboard_auth, monkeypatch):
        _patch_all_sources(monkeypatch)

        resp = client.get("/audit?actor=kai", headers=dashboard_auth)
        assert resp.status_code == 200
        for e in resp.json()["entries"]:
            # Should only match entries where actor contains "kai"
            assert "kai" in e.get("actor", "").lower()

    def test_filter_by_action_prefix(self, client, dashboard_auth, monkeypatch):
        _patch_all_sources(monkeypatch)

        resp = client.get("/audit?action=gateway", headers=dashboard_auth)
        assert resp.status_code == 200
        for e in resp.json()["entries"]:
            assert e["action"].startswith("gateway")

    def test_filter_by_date_range(self, client, dashboard_auth, monkeypatch):
        # Only incidents are in this date range
        _patch_all_sources(monkeypatch)

        resp = client.get(
            "/audit?date_from=2026-08-03&date_to=2026-08-05",
            headers=dashboard_auth,
        )
        assert resp.status_code == 200
        timestamps = [e["timestamp"] for e in resp.json()["entries"]]
        for ts in timestamps:
            assert "2026-08-03" <= ts[:10] <= "2026-08-05"


class TestCSVExport:
    """CSV format produces correct columns and content."""

    def test_csv_returns_text_csv(self, client, dashboard_auth, monkeypatch):
        _patch_all_sources(monkeypatch)

        resp = client.get("/audit?format=csv", headers=dashboard_auth)
        assert resp.status_code == 200
        assert "text/csv" in resp.headers.get("content-type", "")

    def test_csv_has_correct_columns(self, client, dashboard_auth, monkeypatch):
        _patch_all_sources(monkeypatch)

        resp = client.get("/audit?format=csv", headers=dashboard_auth)
        header = resp.text.splitlines()[0]
        columns = header.split(",")
        expected = ["timestamp", "source", "action", "actor", "summary", "status", "source_ip"]
        assert columns == expected

    def test_csv_has_correct_row_count(self, client, dashboard_auth, monkeypatch):
        _patch_all_sources(monkeypatch)

        resp = client.get("/audit?format=csv", headers=dashboard_auth)
        # Header + 9 data rows
        lines = resp.text.strip().splitlines()
        assert len(lines) == 10  # header + 9 sources


class TestSourceSpecifics:
    """Each normalizer produces correct shapes."""

    def test_gateway_success_action(self, client, dashboard_auth, monkeypatch):
        gw = _make_gateway(status_code=200)
        _patch_all_sources(monkeypatch, gateway=[gw])

        resp = client.get("/audit?source=gateway_audit", headers=dashboard_auth)
        assert resp.status_code == 200
        entry = resp.json()["entries"][0]
        assert entry["action"] == "gateway.success"

    def test_gateway_error_action(self, client, dashboard_auth, monkeypatch):
        gw = _make_gateway(status_code=502)
        _patch_all_sources(monkeypatch, gateway=[gw])

        resp = client.get("/audit?source=gateway_audit", headers=dashboard_auth)
        assert resp.status_code == 200
        entry = resp.json()["entries"][0]
        assert entry["action"] == "gateway.error"

    def test_ai_usage_success_status(self, client, dashboard_auth, monkeypatch):
        _patch_all_sources(monkeypatch, ai=[_make_ai_usage(success=True)])

        resp = client.get("/audit?source=ai_usage_history", headers=dashboard_auth)
        assert resp.status_code == 200
        entry = resp.json()["entries"][0]
        assert entry["status"] == "success"

    def test_ai_usage_failure_status(self, client, dashboard_auth, monkeypatch):
        _patch_all_sources(monkeypatch, ai=[_make_ai_usage(success=False)])

        resp = client.get("/audit?source=ai_usage_history", headers=dashboard_auth)
        assert resp.status_code == 200
        entry = resp.json()["entries"][0]
        assert entry["status"] == "error"

    def test_entries_sorted_newest_first(self, client, dashboard_auth, monkeypatch):
        _patch_all_sources(monkeypatch)

        resp = client.get("/audit", headers=dashboard_auth)
        timestamps = [e["timestamp"] for e in resp.json()["entries"]]
        assert timestamps == sorted(timestamps, reverse=True)
