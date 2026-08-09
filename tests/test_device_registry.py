"""Kai Mobile Device Registry tests.

Part of: Kai Mobile Command Node — Sub-project 1: Device Registration & Auth.
"""

import json
import os
import pytest
from fastapi.testclient import TestClient

from core.api import app
from core.device_registry import (
    register_device,
    get_device,
    list_devices,
    revoke_device,
    delete_device,
    find_device_by_token,
    update_heartbeat,
    inject_command,
    ack_commands,
    DuplicateDeviceError,
    DeviceNotFoundError,
    CURRENT_SCHEMA_VERSION,
    DEVICE_TOKEN_PREFIX,
    _now_iso,
)

client = TestClient(app)


# ── Auth helpers (match existing test patterns: tests/test_api.py, tests/test_telegram_bridge.py) ─

def _operator_headers() -> dict:
    """Return Authorization header with the real bridge API token."""
    import core.api as api_module
    return {"Authorization": f"Bearer {api_module._load_api_token()}"}


def _device_headers(token: str) -> dict:
    """Return Authorization header for device bearer token."""
    return {"Authorization": f"Bearer {token}"}


def _register(device_id="TEST-S23ULTRA", **overrides):
    """Register through the API.  Returns the parsed JSON response."""
    payload = {
        "device_id": device_id,
        "device_name": "Test Galaxy S23",
        "platform": "android",
        "platform_version": "16",
        "manufacturer": "Samsung",
        "model": "Galaxy S23 Ultra",
        **overrides,
    }
    return client.post(
        "/kai/devices/register",
        json=payload,
        headers=_operator_headers(),
    )


# ── Token generation & format ───────────────────────────────────────────────


def test_token_has_correct_prefix():
    """Generated tokens must start with kai_device_ prefix."""
    rec = _register().json()
    assert rec["token"].startswith(DEVICE_TOKEN_PREFIX)
    raw_part = rec["token"][len(DEVICE_TOKEN_PREFIX):]
    assert len(raw_part) == 64
    assert all(c in "0123456789abcdef" for c in raw_part)


def test_token_is_returned_only_once():
    """The raw token is on registration response but never in GET/list."""
    _register("ONCE-TOKEN")
    resp = client.get("/kai/devices/ONCE-TOKEN")
    assert resp.status_code == 200
    assert "token" not in resp.json()
    assert "token_hash" not in resp.json()


# ── Registration ───────────────────────────────────────────────────────────


def test_register_device_requires_operator_auth():
    """Unauthenticated registration must return 401."""
    resp = client.post("/kai/devices/register", json={
        "device_id": "NO-AUTH-DEVICE",
        "device_name": "No Auth",
        "platform": "android",
        "platform_version": "16",
        "manufacturer": "Samsung",
        "model": "S23",
    })
    assert resp.status_code == 401


def test_register_device_returns_status_200():
    resp = _register()
    assert resp.status_code == 200
    data = resp.json()
    assert data["device_id"] == "TEST-S23ULTRA"
    assert data["status"] == "authorized"
    assert data["platform"] == "android"
    assert data["manufacturer"] == "Samsung"
    assert data["model"] == "Galaxy S23 Ultra"


def test_register_device_stores_all_fields():
    rec = _register(
        device_id="FULL-FIELDS",
        one_ui_version="8.0",
        security_patch="2026-08-01",
        vpn_ip="10.8.0.50",
    ).json()
    assert rec["one_ui_version"] == "8.0"
    assert rec["security_patch"] == "2026-08-01"
    assert rec["vpn_ip"] == "10.8.0.50"
    assert rec["registered_by"] == "cloudcli-plugin"  # from require_bridge_token


def test_duplicate_device_rejected():
    _register("DUPE-TEST")
    resp = _register("DUPE-TEST")
    assert resp.status_code == 409


def test_revoked_device_can_be_re_registered():
    """A revoked device_id can be registered again."""
    _register("REREG-TEST")
    client.post(
        "/kai/devices/REREG-TEST/revoke",
        headers=_operator_headers(),
    )

    resp = _register("REREG-TEST")
    assert resp.status_code == 200
    assert resp.json()["status"] == "authorized"


# ── List / Get ──────────────────────────────────────────────────────────────


def test_list_devices_empty():
    resp = client.get("/kai/devices")
    assert resp.status_code == 200
    assert "devices" in resp.json()


def test_list_devices_with_registered_device():
    _register("LIST-TEST-1")
    resp = client.get("/kai/devices")
    assert resp.status_code == 200
    devices = resp.json()["devices"]
    assert any(d["device_id"] == "LIST-TEST-1" for d in devices)


def test_list_devices_filtered_by_status():
    _register("FILTER-ACTIVE")
    _register("FILTER-REVOKED")
    client.post(
        "/kai/devices/FILTER-REVOKED/revoke",
        headers=_operator_headers(),
    )

    resp = client.get("/kai/devices?status=authorized")
    authorized = resp.json()["devices"]
    assert all(d["status"] == "authorized" for d in authorized)
    assert any(d["device_id"] == "FILTER-ACTIVE" for d in authorized)
    assert not any(d["device_id"] == "FILTER-REVOKED" for d in authorized)


def test_get_device_returns_full_record():
    _register("GET-TEST")
    resp = client.get("/kai/devices/GET-TEST")
    assert resp.status_code == 200
    rec = resp.json()
    assert rec["device_id"] == "GET-TEST"
    assert rec["manufacturer"] == "Samsung"
    assert rec["model"] == "Galaxy S23 Ultra"


def test_get_nonexistent_device_returns_404():
    resp = client.get("/kai/devices/DOES-NOT-EXIST")
    assert resp.status_code == 404


# ── Heartbeat ───────────────────────────────────────────────────────────────


def test_heartbeat_requires_device_auth():
    resp = client.post("/kai/devices/TEST-S23ULTRA/heartbeat", json={
        "battery_pct": 85,
    })
    assert resp.status_code == 401


def test_heartbeat_updates_timestamp():
    rec = _register("HB-TIMESTAMP").json()
    token = rec["token"]

    resp = client.post(
        "/kai/devices/HB-TIMESTAMP/heartbeat",
        json={"battery_pct": 90, "network_type": "wifi"},
        headers=_device_headers(token),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert "server_time" in data

    stored = client.get("/kai/devices/HB-TIMESTAMP").json()
    assert stored["last_heartbeat"] is not None


def test_heartbeat_stores_battery_and_network():
    rec = _register("HB-DATA").json()
    token = rec["token"]

    client.post(
        "/kai/devices/HB-DATA/heartbeat",
        json={"battery_pct": 42, "charging": False, "network_type": "5g"},
        headers=_device_headers(token),
    )
    stored = client.get("/kai/devices/HB-DATA").json()
    assert stored["heartbeat_data"]["battery_pct"] == 42
    assert stored["heartbeat_data"]["charging"] is False
    assert stored["heartbeat_data"]["network_type"] == "5g"


def test_heartbeat_device_cannot_impersonate_another():
    """A device can only heartbeat for itself."""
    rec_a = _register("HB-DEV-A").json()
    rec_b = _register("HB-DEV-B").json()

    resp = client.post(
        "/kai/devices/HB-DEV-A/heartbeat",
        json={"battery_pct": 50},
        headers=_device_headers(rec_b["token"]),
    )
    assert resp.status_code == 403


def test_heartbeat_nonexistent_device():
    resp = client.post("/kai/devices/NO-SUCH-DEVICE/heartbeat", json={"battery_pct": 1})
    assert resp.status_code == 401  # Auth checked before existence


def test_heartbeat_returns_health_summary():
    rec = _register("HB-HEALTH").json()
    token = rec["token"]

    resp = client.post(
        "/kai/devices/HB-HEALTH/heartbeat",
        json={"battery_pct": 80},
        headers=_device_headers(token),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "health_summary" in data
    assert "overall" in data["health_summary"]


# ── Pending commands ───────────────────────────────────────────────────────


def test_heartbeat_returns_pending_commands():
    rec = _register("CMD-TEST").json()
    token = rec["token"]

    inject_command("CMD-TEST", "show_alert", {"title": "Disk Space Warning"})

    resp = client.post(
        "/kai/devices/CMD-TEST/heartbeat",
        json={"battery_pct": 75},
        headers=_device_headers(token),
    )
    assert resp.status_code == 200
    commands = resp.json()["pending_commands"]
    assert len(commands) == 1
    assert commands[0]["action"] == "show_alert"
    assert commands[0]["payload"]["title"] == "Disk Space Warning"


def test_ack_commands_removes_from_pending():
    rec = _register("ACK-TEST").json()
    token = rec["token"]

    cmd_id = inject_command("ACK-TEST", "show_alert", {"title": "Test"})

    # First heartbeat: command present
    resp1 = client.post(
        "/kai/devices/ACK-TEST/heartbeat",
        json={"battery_pct": 75},
        headers=_device_headers(token),
    )
    assert len(resp1.json()["pending_commands"]) == 1

    # Second heartbeat with ack: command removed
    resp2 = client.post(
        "/kai/devices/ACK-TEST/heartbeat",
        json={"battery_pct": 76, "ack_ids": [cmd_id]},
        headers=_device_headers(token),
    )
    assert len(resp2.json()["pending_commands"]) == 0


# ── Revocation ──────────────────────────────────────────────────────────────


def test_revoke_device_requires_operator_auth():
    _register("REVOKE-AUTH")
    resp = client.post("/kai/devices/REVOKE-AUTH/revoke")
    assert resp.status_code == 401


def test_revoke_device_sets_status():
    _register("REVOKE-ME")
    resp = client.post(
        "/kai/devices/REVOKE-ME/revoke",
        headers=_operator_headers(),
    )
    assert resp.status_code == 200
    assert resp.json()["device_id"] == "REVOKE-ME"

    stored = client.get("/kai/devices/REVOKE-ME").json()
    assert stored["status"] == "revoked"


def test_revoked_device_token_is_rejected():
    rec = _register("REVOKED-TOKEN").json()
    token = rec["token"]

    # Token works before revoke
    resp = client.post(
        "/kai/devices/REVOKED-TOKEN/heartbeat",
        json={"battery_pct": 50},
        headers=_device_headers(token),
    )
    assert resp.status_code == 200

    # Revoke
    client.post(
        "/kai/devices/REVOKED-TOKEN/revoke",
        headers=_operator_headers(),
    )

    # Same token now rejected
    resp = client.post(
        "/kai/devices/REVOKED-TOKEN/heartbeat",
        json={"battery_pct": 50},
        headers=_device_headers(token),
    )
    assert resp.status_code == 401


def test_revoke_nonexistent_device_returns_404():
    resp = client.post(
        "/kai/devices/NO-SUCH-DEV/revoke",
        headers=_operator_headers(),
    )
    assert resp.status_code == 404


# ── Delete ──────────────────────────────────────────────────────────────────


def test_delete_device_requires_operator_auth():
    _register("DELETE-AUTH")
    resp = client.delete("/kai/devices/DELETE-AUTH")
    assert resp.status_code == 401


def test_delete_device_removes_record():
    _register("DELETE-ME")
    resp = client.delete(
        "/kai/devices/DELETE-ME",
        headers=_operator_headers(),
    )
    assert resp.status_code == 200

    resp2 = client.get("/kai/devices/DELETE-ME")
    assert resp2.status_code == 404


# ── Auth middleware — device role ────────────────────────────────────────────


def test_device_role_is_registered():
    from core.authz import ROLE_CAPABILITIES
    assert "device" in ROLE_CAPABILITIES
    assert "kai.chat.send" in ROLE_CAPABILITIES["device"]


def test_device_token_resolves_to_device_id():
    """The auth middleware resolves a device token to the device's id."""
    rec = _register("AUTH-DEVICE").json()
    token = rec["token"]

    from core.authz import _resolve_session
    session = _resolve_session(token)
    assert session is not None
    assert session["username"] == "AUTH-DEVICE"
    assert session["role"] == "device"


def test_device_token_rejected_when_revoked():
    rec = _register("AUTH-REVOKED").json()
    token = rec["token"]

    client.post(
        "/kai/devices/AUTH-REVOKED/revoke",
        headers=_operator_headers(),
    )

    from core.authz import _resolve_session
    session = _resolve_session(token)
    assert session is None


def test_non_kai_device_prefix_skipped():
    """Tokens without kai_device_ prefix should not be checked as device tokens."""
    from core.authz import _resolve_session
    result = _resolve_session("completely-random-token-value")
    assert result is None


def test_bridge_token_operator_unaffected():
    """Existing bridge-token operators must still work."""
    from core.authz import check_capability
    assert check_capability("cloudcli-plugin", "builds.create") is True
    assert check_capability("dashboard-proxy", "approvals.approve") is True
    assert check_capability("cloudcli-plugin", "kai.chat.send") is True


def test_jwt_session_unaffected():
    """JWT sessions must still resolve — device auth doesn't break existing paths."""
    from core.authz import _resolve_session, _sessions

    _sessions["test-legacy-session"] = {
        "username": "test-op",
        "role": "operator",
        "created": _now_iso(),
    }
    session = _resolve_session("test-legacy-session")
    _sessions.pop("test-legacy-session", None)

    assert session is not None
    assert session["username"] == "test-op"
    assert session["role"] == "operator"


# ── module-level CRUD (detached from API, proof against isolated_memory) ───


def test_register_device_direct_stores_record():
    """Direct register_device() call stores a record on disk."""
    rec = register_device(
        device_id="DIRECT-TEST",
        device_name="Direct Register",
        platform="android",
        platform_version="16",
        manufacturer="Google",
        model="Pixel 9",
        registered_by="test",
    )
    assert rec["device_id"] == "DIRECT-TEST"
    assert rec["status"] == "authorized"
    assert "token" in rec

    stored = get_device("DIRECT-TEST")
    assert stored["device_name"] == "Direct Register"


def test_get_device_returns_none_for_missing():
    assert get_device("MISSING-DEVICE") is None


def test_list_devices_direct():
    register_device("L1", "L1 Name", "android", "14", "Samsung", "S22", registered_by="test")
    register_device("L2", "L2 Name", "android", "15", "Samsung", "S23", registered_by="test")

    all_devs = list_devices()
    assert len(all_devs) >= 2

    active = list_devices(status="authorized")
    assert all(d["status"] == "authorized" for d in active)


def test_find_device_by_token_direct():
    rec = register_device(
        "TOKEN-LOOKUP", "Token Test", "android", "16", "Samsung", "S23",
        registered_by="test",
    )
    found = find_device_by_token(rec["token"])
    assert found is not None
    assert found["device_id"] == "TOKEN-LOOKUP"


def test_find_device_by_token_rejects_non_prefixed():
    result = find_device_by_token("not-a-device-token-at-all")
    assert result is None


def test_update_heartbeat_direct():
    register_device(
        "HB-DIRECT", "HB Direct", "android", "16", "Samsung", "S23",
        registered_by="test",
    )
    result = update_heartbeat("HB-DIRECT", {"battery_pct": 88, "network_type": "5g"})
    assert result["ok"] is True
    assert "pending_commands" in result

    stored = get_device("HB-DIRECT")
    assert stored["last_heartbeat"] is not None
    assert stored["heartbeat_data"]["battery_pct"] == 88


def test_update_heartbeat_nonexistent_raises():
    with pytest.raises(DeviceNotFoundError):
        update_heartbeat("NO-DEVICE", {})


def test_inject_and_ack_direct():
    register_device(
        "CMD-DIRECT", "Cmd Direct", "android", "16", "Samsung", "S23",
        registered_by="test",
    )

    cmd_id = inject_command("CMD-DIRECT", "show_alert", {"title": "Wake Up"})
    assert cmd_id.startswith("cmd_")

    hb = update_heartbeat("CMD-DIRECT", {"battery_pct": 50})
    assert len(hb["pending_commands"]) == 1
    assert hb["pending_commands"][0]["action"] == "show_alert"

    removed = ack_commands("CMD-DIRECT", [cmd_id])
    assert removed == 1

    hb2 = update_heartbeat("CMD-DIRECT", {"battery_pct": 51})
    assert len(hb2["pending_commands"]) == 0


def test_revoke_direct():
    register_device(
        "REV-DIRECT", "Rev Direct", "android", "16", "Samsung", "S23",
        registered_by="test",
    )
    revoke_device("REV-DIRECT")
    stored = get_device("REV-DIRECT")
    assert stored["status"] == "revoked"


def test_revoke_nonexistent_direct():
    with pytest.raises(DeviceNotFoundError):
        revoke_device("NO-DEVICE")


def test_delete_direct():
    register_device(
        "DEL-DIRECT", "Del Direct", "android", "16", "Samsung", "S23",
        registered_by="test",
    )
    delete_device("DEL-DIRECT")
    assert get_device("DEL-DIRECT") is None


def test_delete_nonexistent_direct():
    with pytest.raises(DeviceNotFoundError):
        delete_device("NO-DEVICE")


# ── Edge cases ──────────────────────────────────────────────────────────────


def test_empty_registry_on_first_use():
    devices = list_devices()
    assert isinstance(devices, list)


def test_heartbeat_data_defaults():
    """Heartbeat with minimal data still works."""
    rec = _register("HB-MINIMAL").json()
    token = rec["token"]

    resp = client.post(
        "/kai/devices/HB-MINIMAL/heartbeat",
        json={},
        headers=_device_headers(token),
    )
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


def test_capabilities_passed_through():
    rec = _register(
        device_id="CAP-TEST",
        capabilities=["kai.chat.send"],
    ).json()
    assert rec["capabilities"] == ["kai.chat.send"]

    stored = client.get("/kai/devices/CAP-TEST").json()
    assert stored["capabilities"] == ["kai.chat.send"]


def test_register_optional_fields_null():
    rec = _register("OPT-NULL").json()
    assert rec["one_ui_version"] is None
    assert rec["security_patch"] is None
    assert rec["vpn_ip"] is None
    assert rec["assigned_worker"] is None
