# Kai Mobile Notifications

**Part of**: Kai Mobile Command Node — Sub-project 3: Push Notification Infrastructure
**Last updated**: 2026-08-10

## Overview

Kai's notification system delivers infrastructure alerts, build status updates,
and AI provider health warnings to registered mobile devices. Delivery is
pull-based (heartbeat response) with optional Web Push for real-time alerts.

## Architecture

```
Incident detected → NotificationManager.enqueue()
  ├─ Telegram (if critical + provider enabled)
  ├─ Memory store (always)
  └─ Device queue → next heartbeat response
       └─ Web Push (optional, if device supports)
```

## Notification Lifecycle

### 1. Creation

```python
NotificationManager.enqueue(
    severity="critical",      # critical | important | informational
    title="VPN peer offline",
    body="10.8.0.8 has been unreachable for 120s",
    source="health_analyzer",  # auto-routes to module
)
```

### 2. Module Routing

Source strings map to modules via `_SOURCE_MODULE_MAP`:

| Source | Module | Actions |
|--------|--------|---------|
| health_analyzer, health_worker | health | VIEW (health panel), ACKNOWLEDGE |
| vpn_failover | vpn | VIEW (vpn panel), TRIGGER FAILOVER, ACKNOWLEDGE |
| build_failure, build_success | build | VIEW, OPEN LOG, RETRY, ACKNOWLEDGE |
| provider_health, provider_error | provider | VIEW (providers), SWITCH PROVIDER, ACKNOWLEDGE |
| docker_watchdog, budget_alert | system | VIEW (system), ACKNOWLEDGE |
| security_alert | security | VIEW, LOCKDOWN, ACKNOWLEDGE |
| unknown (fallback) | system | VIEW, ACKNOWLEDGE |

### 3. Delivery Filtering

Each device has a notification config with three filter levels:

```json
{
  "enabled": true,
  "per_severity": {
    "critical": true,
    "important": true,
    "informational": true
  },
  "per_module": {
    "health": true,
    "build": false
  },
  "per_source": {
    "docker_watchdog": false
  }
}
```

`_should_deliver_notification(device_id, notif)` checks:
1. `enabled` flag
2. `per_severity[notif.severity]`
3. `per_module[notif.module]` (if set)
4. `per_source[notif.source]` (if set)

Config updates are deep-merged so partial updates preserve existing prefs.

### 4. Heartbeat Delivery

```
S23 Ultra heartbeat (30s) →
Server checks pending_notifications queue →
Filters through device preferences →
Returns filtered list in heartbeat response →
Device ACKs via ack_notification_ids in next heartbeat
```

### 5. Web Push (Optional)

If the device registers for Web Push:
1. Service worker receives push event
2. Notification shown with action buttons from module config
3. Click → navigates to relevant panel via postMessage
4. `requireInteraction: true` for critical alerts

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/kai/devices/{id}/notification-config` | Get current config |
| PUT | `/kai/devices/{id}/notification-config` | Update config (deep-merge) |
| POST | `/kai/devices/{id}/heartbeat` | Deliver pending notifications |

## Design Decisions

- **Pull, not push**: Heartbeat-based delivery means no persistent connection needed. Web Push is the optional real-time layer.
- **Delivery-once for notifications**: Unlike commands (retried up to 5×), notifications are delivered once and expire if not ACKed.
- **No delivery guarantees**: If the device is offline, notifications accumulate but aren't persisted across server restarts (by design — critical alerts also go to Telegram).
- **Per-device preferences**: Each device controls its own notification noise level independently.
- **Module-aware actions**: Action buttons are derived from the notification's module, not hardcoded in the client.
