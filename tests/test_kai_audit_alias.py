"""Tests for the /kai/audit alias and the two new audit sources.

Roadmap gaps:
  * ``GET /kai/audit`` returned 404 while ``/audit`` was 200.
  * ``command_bus_audit.json`` (written by the Command Bus) and
    ``execution_audit.json`` were never merged into the audit feed.
"""

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    from core.api import app
    return TestClient(app)


def _patch(monkeypatch, mapping):
    import core.api as api

    def fake_load(filename, **kw):
        return mapping.get(filename, {"records": []})

    monkeypatch.setattr(api, "load", fake_load)


def _command_bus_record(status="success", command="/pause", user="operator"):
    return {
        "command": command, "source": "telegram", "user": user,
        "status": status, "timestamp": "2026-09-21T10:00:00",
        "reason": "ok", "decision": "allow", "risk": "low",
    }


def _execution_record(result="success"):
    return {
        "incident": "i9", "service": "pulse", "action": "restart_container",
        "result": result, "timestamp": "2026-09-21T11:00:00",
    }


def test_kai_audit_alias_exists(client, monkeypatch):
    _patch(monkeypatch, {})
    r = client.get("/kai/audit")
    assert r.status_code == 200
    body = r.json()
    assert set(["total", "returned", "entries"]).issubset(body)


def test_command_bus_audit_is_merged(client, monkeypatch):
    _patch(monkeypatch, {"command_bus_audit.json": {"records": [_command_bus_record()]}})
    r = client.get("/kai/audit?source=command_bus_audit")
    assert r.status_code == 200
    entries = r.json()["entries"]
    assert len(entries) == 1
    assert entries[0]["source"] == "command_bus"
    assert entries[0]["action"] == "command.success"
    assert entries[0]["actor"] == "operator"


def test_execution_audit_is_merged(client, monkeypatch):
    _patch(monkeypatch, {"execution_audit.json": {"records": [_execution_record()]}})
    r = client.get("/kai/audit?source=execution_audit")
    assert r.status_code == 200
    entries = r.json()["entries"]
    assert len(entries) == 1
    assert entries[0]["source"] == "execution"
    assert entries[0]["action"] == "execution.restart_container"
    assert entries[0]["status"] == "success"


def test_entries_carry_severity_and_filter(client, monkeypatch):
    _patch(monkeypatch, {
        "command_bus_audit.json": {"records": [
            _command_bus_record(status="denied"),
            _command_bus_record(status="success", command="/status"),
        ]},
    })
    r = client.get("/kai/audit?severity=error")
    assert r.status_code == 200
    entries = r.json()["entries"]
    assert entries, "expected at least one error entry"
    assert all(e["severity"] == "error" for e in entries)
    assert entries[0]["source"] == "command_bus"


def test_audit_still_works_unchanged(client, monkeypatch):
    _patch(monkeypatch, {})
    r = client.get("/audit")
    assert r.status_code == 200
    assert "entries" in r.json()
