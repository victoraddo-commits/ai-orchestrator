"""Tests for the KAI app API extensions (2026-08-26 mobile v3.1).

Covers /kai/app/spend (cost tracker), and the emergency stop/resume/status
endpoints. Device auth is monkeypatched — pairing/token mechanics are
covered by device_registry tests.
"""

import pytest
from fastapi.testclient import TestClient

from core.api import app

client = TestClient(app)


@pytest.fixture
def paired_device(monkeypatch):
    # find_device_by_token is imported lazily inside _require_device, so the
    # patch target is core.device_registry itself.
    import core.device_registry as dr
    monkeypatch.setattr(
        dr, "find_device_by_token", lambda token: "dev-test" if token == "tok-test" else None
    )
    return {"Authorization": "Bearer tok-test"}


def test_app_spend_returns_cost_summary_shape(paired_device, isolated_memory):
    from core.ai.ai_router import record_usage

    record_usage("openrouter", task_type="classification", description="spend test",
                 success=True, duration_ms=50,
                 usage={"prompt_tokens": 1_000_000, "completion_tokens": 100_000})

    resp = client.get("/kai/app/spend", headers=paired_device)
    assert resp.status_code == 200
    body = resp.json()
    assert body["calls_estimated"] >= 1
    assert "total_cost" in body and "by_provider" in body


def test_app_spend_rejects_bad_days(paired_device):
    resp = client.get("/kai/app/spend?days=9999", headers=paired_device)
    assert resp.status_code == 200  # clamped, not rejected


def test_emergency_status_defaults_to_running(paired_device, isolated_memory):
    resp = client.get("/kai/app/emergency/status", headers=paired_device)
    assert resp.status_code == 200
    assert resp.json()["stopped"] is False


def test_emergency_stop_then_resume_roundtrip(paired_device, isolated_memory, tmp_path, monkeypatch):
    # Redirect both kill-switch + scheduler pause files into tmp so the live
    # scheduler is never actually paused by a test.
    import core.kai_emergency as ke
    import core.scheduler as sched
    kill_path = tmp_path / "kill_switch.json"
    pause_path = tmp_path / "scheduler_paused.json"
    monkeypatch.setattr(ke, "KILL_SWITCH_PATH", kill_path)
    monkeypatch.setattr(sched, "SCHEDULER_PAUSE_FILE", pause_path)
    # audit file also lives in memory dir — isolated via AI_ORCHESTRATOR_MEMORY_DIR.

    resp = client.post("/kai/app/emergency/stop", headers=paired_device,
                       json={"reason": "test stop"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True and body["tool_switch"] is True

    status = client.get("/kai/app/emergency/status", headers=paired_device).json()
    assert status["stopped"] is True and status["scheduler_paused"] is True
    assert status["by"] == "mobile:dev-test"

    resume = client.post("/kai/app/emergency/resume", headers=paired_device)
    assert resume.status_code == 200
    status2 = client.get("/kai/app/emergency/status", headers=paired_device).json()
    assert status2["stopped"] is False and status2["scheduler_paused"] is False


def test_app_endpoints_require_device_token():
    for path in ("/kai/app/spend", "/kai/app/emergency/status"):
        resp = client.get(path)
        assert resp.status_code == 401


def test_terminal_endpoint_returns_credential(paired_device, isolated_memory, monkeypatch, tmp_path):
    cred_file = tmp_path / "kai-terminal-cred"
    cred_file.write_text("kai:abc123\n")
    import builtins
    real_open = builtins.open

    def fake_open(path, *a, **kw):
        if str(path) == "/etc/default/kai-terminal-cred":
            return open(cred_file, *a, **kw)
        return real_open(path, *a, **kw)

    monkeypatch.setattr(builtins, "open", fake_open)
    resp = client.get("/kai/app/terminal", headers=paired_device)
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True and body["port"] == 8001
    assert body["credential"] == "kai:abc123"


def test_terminal_endpoint_requires_token():
    assert client.get("/kai/app/terminal").status_code == 401


def test_terminal_endpoint_503_when_unconfigured(paired_device, isolated_memory, monkeypatch):
    import os
    monkeypatch.setattr(os.path, "exists", lambda p: False if "kai-terminal-cred" in str(p) else os.path.exists(p))
    resp = client.get("/kai/app/terminal", headers=paired_device)
    assert resp.status_code == 503
