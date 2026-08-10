# Kai Mobile Testing Guide

**Part of**: Kai Mobile Command Node — Sub-project 6: Integration & Testing
**Last updated**: 2026-08-10

## Test Coverage

### Test Files

| File | Tests | What it covers |
|------|-------|----------------|
| `tests/test_device_registry.py` | ~50 | Device auth, heartbeat, notification queue, config, VPN health |
| `tests/test_notifications.py` | ~30 | NotificationManager, module routing, action buttons, filtering |
| `tests/test_mobile_diagnose.py` | 41 | Self-diagnostics: 8 subsystem checks, CLI, API endpoint, command dispatch |

## Running Tests

### All tests
```bash
.venv/bin/python -m pytest
```

### Mobile-specific tests
```bash
.venv/bin/python -m pytest tests/test_device_registry.py tests/test_notifications.py tests/test_mobile_diagnose.py -v
```

### Single test
```bash
.venv/bin/python -m pytest tests/test_mobile_diagnose.py::TestApiEndpoint::test_endpoint_returns_200 -v
```

## Test Patterns

### 1. Device Registry Tests

Mock `list_devices()` to simulate device state:
```python
with patch("core.device_registry.list_devices", return_value=[...]):
    result = _check_device_registry()
    assert result["status"] == "PASS"
```

### 2. WireGuard Tests

Mock both `list_devices()` and `get_wg_status()`:
```python
devices = [{"device_id": "p1", "status": "authorized", "vpn_ip": "10.8.0.8"}]
wg = {"ok": True, "peers": [{"allowed_ips": ["10.8.0.8/32"], "handshake_age_sec": 30}]}

with patch("core.device_registry.list_devices", return_value=devices):
    with patch("core.wireguard_manager.get_wg_status", return_value=wg):
        result = _check_wireguard()
```

**Important**: The diagnose functions use lazy imports (`from X import Y` inside the function body). Patch the **source module** (`core.device_registry.list_devices`), not the import destination (`core.kai.mobile_diagnose.list_devices`).

### 3. API Integration Tests

Use FastAPI TestClient for endpoint tests:
```python
from core.api import app
from fastapi.testclient import TestClient

client = TestClient(app)
resp = client.get("/kai/mobile/diagnose")
assert resp.status_code == 200
```

### 4. Command Dispatch Tests

Test pattern matching directly:
```python
from core.kai.commands import dispatch
result = dispatch("Kai, mobile diagnose")
assert result["matched"] is True
assert "checks" in result["result"]
```

## Status Values

Diagnostic checks return one of three statuses:
- **PASS** — Subsystem is healthy and functioning normally
- **WARN** — Degraded but operational (e.g., device offline, provider missing)
- **FAIL** — Broken or unreachable (e.g., exception, missing auth, WG interface down)

## Manual Testing Checklist

### VPN Connectivity
- [ ] Phone WireGuard tunnel connects (check DD-WRT `wg show` for handshake)
- [ ] Kai API reachable at `https://10.8.0.4:8000/health` from phone browser
- [ ] Heartbeat endpoint returns 200 with valid JSON

### PWA Installation
- [ ] Navigate to `https://10.8.0.4:8000/command-center` in Chrome
- [ ] "Install" prompt appears (or "Add to Home Screen" in menu)
- [ ] PWA opens in standalone mode (no browser chrome)
- [ ] App icon appears on home screen
- [ ] Manifest loads at `/kai/manifest.json` (200)
- [ ] Service worker registers (check Chrome DevTools → Application)

### Notifications
- [ ] Trigger a test notification (via Kai chat: "Kai, mobile diagnose")
- [ ] Notification appears in heartbeat response
- [ ] Web Push works (if enabled)
- [ ] Preference filtering works (disable a module, verify no delivery)

### Kai Chat
- [ ] Text input sends and receives responses
- [ ] Voice input works (must be over HTTPS)
- [ ] Typing indicator shows during server processing
- [ ] Message history persists within session

### Self-Diagnostics
- [ ] `GET /kai/mobile/diagnose` returns 200 with all 8 checks
- [ ] `Kai, mobile diagnose` command works from chat
- [ ] CLI: `python -m core.kai.mobile_diagnose` prints formatted output

## Known Limitations

1. **Web Speech API** requires HTTPS (works over WireGuard tunnel since Kai API is plain HTTP on localhost — use Let's Encrypt for production)
2. **Service Worker** cache may serve stale shell on deploy — force-refresh (Ctrl+Shift+R) after updates
3. **TestClient** does not test WireGuard connectivity (uses mocked data)
4. **Voice input** only works in Chromium-based browsers (Chrome, Edge, Samsung Internet)
5. **Push notifications** require VAPID keys and a push service (not yet configured as of 2026-08-10)
