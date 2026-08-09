# Kai Mobile Device Registry — Design Spec

**Date**: 2026-08-09
**Status**: Approved
**Part of**: Kai Mobile Command Node integration (Sub-project 1 of 6)
**Depends on**: Nothing — this is the foundational layer

---

## Scope

Add a Device Registry to the Kai AI Orchestrator so the Samsung Galaxy S23 Ultra
(`KAI-MOBILE-S23ULTRA`) — and future devices — can register as permanent,
authenticated Kai nodes with secure long-lived tokens.

This spec covers:
- Device record model and storage
- Device token generation and lifecycle
- Heartbeat protocol with pending-command delivery
- API endpoints for registration, status, revocation
- Integration with the existing authz/capability system
- A new `"device"` role with baseline capabilities

Out of scope (separate specs):
- Push notification infrastructure
- Mobile-responsive Command Center UI
- Health worker assignment
- WireGuard configuration

---

## Architecture

The Device Registry follows the exact same pattern as the existing App Registry
(`core/app_registry.py`):

- Pydantic models for type safety
- JSON file storage in `memory/device_registry.json`
- Atomic writes via temp file + `os.replace()`
- `fcntl.flock` for multi-process safety
- Schema versioning with migration support
- `.bak` backups on every write

### Files

| Action | File | Purpose |
|--------|------|---------|
| **CREATE** | `core/device_registry.py` | Storage layer, token generation, lookup |
| **CREATE** | `core/device_registry_routes.py` | FastAPI router with all device endpoints |
| **MODIFY** | `core/authz.py` | Add `"device"` role + resolve `kai_device_` tokens |
| **MODIFY** | `core/api.py` | Include device routes router |

---

## Data Model

### DeviceRecord

```python
class DeviceStatus(str, enum.Enum):
    registered = "registered"    # Created, not yet authorized
    authorized = "authorized"    # Active, can authenticate
    revoked = "revoked"          # Token invalidated

class DeviceRecord(BaseModel):
    device_id: str               # "KAI-MOBILE-S23ULTRA"
    device_name: str             # Human-readable display name
    token_hash: str              # bcrypt hash of the bearer token
    platform: str                # "android"
    platform_version: str        # "16"
    manufacturer: str            # "Samsung"
    model: str                   # "Galaxy S23 Ultra"
    one_ui_version: str | None   # "8.0"
    security_patch: str | None   # "2026-08-01"
    vpn_ip: str | None           # WireGuard IP
    capabilities: list[str]      # Granted capability strings
    status: DeviceStatus
    assigned_worker: str | None  # e.g. "KAI-SYSTEM-HEALTH-WORKER"
    last_heartbeat: str | None   # ISO timestamp
    heartbeat_data: dict         # Latest heartbeat payload (battery, network, etc.)
    created_at: str
    updated_at: str
    registered_by: str           # Operator who registered it
```

### DeviceRegistryFile (persistence envelope)

```python
class DeviceRegistryFile(BaseModel):
    schema_version: int = 1
    records: list[DeviceRecord]
```

---

## Token Lifecycle

### Generation (server-side, on registration)

```
POST /kai/devices/register  (operator-authenticated)
Body: { device_id, platform, model, ... }

→ Server generates 256-bit random token (64 hex chars)
→ Stores bcrypt(token_hash) in registry
→ Returns { device_id, token } ONCE
→ Raw token is NEVER stored on server, never logged
```

### Format

Device bearer tokens use the prefix `kai_device_` so the auth middleware
can distinguish them from JWT and legacy session tokens:

```
kai_device_a1b2c3d4e5f6... (64 hex chars after prefix)
```

### Storage on Android

The token should be stored in Android Keystore (not SharedPreferences). The
mobile agent reads it once at startup and holds it in memory. The Keystore
entry is tied to the Kai mobile agent's package signature.

### Auth Middleware Integration

In `authz.py`, `_resolve_session()` gains a third resolution path:

```python
def _resolve_session(token: str) -> dict | None:
    # 1. Try JWT (existing)
    claims = verify_jwt(token)
    if claims is not None:
        return {...}

    # 2. Try legacy in-memory session (existing)
    session = _sessions.get(token)
    if session is not None:
        return {...}

    # 3. Try device token (NEW)
    if token.startswith("kai_device_"):
        device = find_device_by_token(token)
        if device and device.status == DeviceStatus.authorized:
            return {
                "username": device.device_id,
                "role": "device",
                "created": device.created_at,
            }

    return None
```

### Revocation

- `POST /kai/devices/{device_id}/revoke` → sets `status = "revoked"`
- All in-flight requests with that token immediately return 401
- Heartbeat starts failing → phone UI shows "Device revoked"
- The token cannot be un-revoked (register a new device instead)

---

## Role & Capabilities

### New Role: `"device"`

A third role alongside `operator` and `viewer`:

```python
ROLE_CAPABILITIES = {
    "operator": set(CAPABILITIES.keys()),
    "viewer":  set(),  # read-only
    "device": {
        "kai.chat.send",       # Chat with Kai
        # All GET endpoints are already unrestricted
        # Device-specific endpoints (heartbeat) use
        # device token resolution, not capability checks
    },
}
```

The device role gets baseline capabilities. Operator-granted capabilities
(proxmox, worker management, build actions) are stored per-device in
`DeviceRecord.capabilities` and checked at runtime.

For now, no per-device capability elevation is needed — the phone is a
control surface for the operator, who authenticates separately for
privileged operations via the existing session auth.

---

## API Endpoints

All mounted under the existing FastAPI app (port 8000).

### `POST /kai/devices/register`

- **Auth**: Operator session or bridge token
- **Body**: `{ device_id, platform, platform_version, manufacturer, model, ... }`
- **Returns**: `{ device_id, token, created_at }`
- **Side effects**: Creates record with status `authorized`, generates token

### `GET /kai/devices`

- **Auth**: Operator session
- **Returns**: `{ devices: [...DeviceRecord...] }`

### `GET /kai/devices/{device_id}`

- **Auth**: Operator session OR the device itself (via device token)
- **Returns**: Full DeviceRecord (without token_hash)

### `POST /kai/devices/{device_id}/heartbeat`

- **Auth**: Device token (must match device_id in path)
- **Body**: `{ battery_pct, charging, vpn_ip, network_type, agent_version, notification_status }`
- **Returns**:
```json
{
  "ok": true,
  "server_time": "2026-...",
  "pending_commands": [
    {"id": "cmd_001", "action": "show_alert", "payload": {...}}
  ],
  "health_summary": {
    "overall": "healthy",
    "alerts_count": 2
  }
}
```
- **Side effects**: Updates `last_heartbeat` and `heartbeat_data`

### `POST /kai/devices/{device_id}/revoke`

- **Auth**: Operator session
- **Returns**: `{ ok: true, device_id, previous_status }`

### `DELETE /kai/devices/{device_id}`

- **Auth**: Operator session
- **Returns**: `{ ok: true, device_id }`
- **Side effects**: Removes record entirely (not just revoke)

---

## Pending Commands (via Heartbeat)

Rather than requiring a persistent WebSocket (hard on mobile), the heartbeat
response carries a `pending_commands` array. Kai can inject commands for the
phone to execute:

```json
{
  "id": "cmd_001",
  "action": "show_alert",
  "payload": {
    "title": "Worker Failure",
    "body": "Worker qwen4_coding is unreachable",
    "severity": "critical",
    "module": "ai-workforce"
  }
}
```

The phone acknowledges commands on the next heartbeat (includes `ack_ids`).
Kai retries unacknowledged commands for up to 5 heartbeat cycles (5 minutes)
before dropping them.

This is a lightweight command channel — it does NOT replace a proper push
notification system (that's Sub-project 3).

---

## Health Summary (via Heartbeat)

The heartbeat response includes a compact health summary so the phone can
display system status at a glance without making extra API calls:

```json
{
  "overall": "healthy",      // "healthy" | "degraded" | "critical"
  "alerts_count": 2,
  "components": {
    "proxmox": "healthy",
    "kai_core": "healthy",
    "workers": "degraded",
    "providers": "healthy",
    "vpn": "down"
  }
}
```

Generated from existing health data — the Device Registry doesn't own health
monitoring, it just surfaces it.

---

## Design Decisions

1. **Long-lived tokens, not JWTs**: Phones stay registered for months. JWTs
   would require constant reissue. Device tokens are permanent until revoked.

2. **Operator registers devices**: The phone doesn't self-register. An operator
   creates the device record from Command Center, generates the token, and
   transfers it to the phone (via Telegram, QR code, or manual entry).

3. **Pull-based commands via heartbeat**: No WebSocket. Heartbeat does double
   duty as status update AND command delivery channel.

4. **No device-to-device communication**: Each device has its own independent
   relationship with Kai. Devices don't know about each other.

5. **Follows App Registry pattern exactly**: Same file format, same locking,
   same backup strategy. No new infrastructure patterns.

---

## Testing

- Unit tests for DeviceRegistry CRUD operations
- Token generation and verification
- Heartbeat update and pending command lifecycle
- Auth middleware resolution of device tokens
- Revocation flow (token rejected after revoke)
- Operator-only gates on registration and revocation

---

## Acceptance Criteria

- [ ] Device can be registered via API (operator auth)
- [ ] Registration returns a valid device token
- [ ] Device token authenticates against protected endpoints
- [ ] Heartbeat updates last_seen timestamp
- [ ] Heartbeat response includes pending commands + health summary
- [ ] Revoked device token is rejected
- [ ] Device appears in device list in Command Center
- [ ] Existing auth (JWT, bridge token, legacy sessions) is unaffected
- [ ] New role `"device"` exists and resolves correctly
- [ ] All existing tests continue to pass
