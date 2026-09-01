"""Kai Notification Service — push notification engine.

Part of: Kai Mobile Command Node — Sub-project 3: Push Notification System.

Delivers infrastructure alerts, build state changes, and health warnings to:
- Telegram (critical/important — immediate push)
- Device heartbeat responses (all severities — queued for next heartbeat)
- Notification history (memory/notifications.json — persistent, queryable)

Architecture:
  Orchestrator cycle → NotificationManager.enqueue() → {
    Telegram (critical/important),
    device_registry._pending_commands (all severities),
    memory/notifications.json (all, persistent),
  }

Deduplication prevents alert storms: same (title, source) pair within
DEDUP_WINDOW_SECONDS is suppressed.  History is capped at MAX_NOTIFICATIONS.
"""

import hashlib
import json
import logging
import time
from datetime import datetime, timezone
from typing import Optional

from core.memory import load, update

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

MEMORY_FILE = "notifications.json"
MAX_NOTIFICATIONS = 500
DEDUP_WINDOW_SECONDS = 600  # 10 minutes — same alert suppressed in this window

# Severity levels
CRITICAL = "critical"        # Telegram + heartbeat — service down, security breach
IMPORTANT = "important"      # Heartbeat only — degradation, pending action needed
INFORMATIONAL = "informational"  # Heartbeat only — state change, FYI

SEVERITY_ORDER = {CRITICAL: 0, IMPORTANT: 1, INFORMATIONAL: 2}

# Telegram emoji per severity
_SEVERITY_EMOJI = {
    CRITICAL: "🚨",
    IMPORTANT: "⚠️",
    INFORMATIONAL: "ℹ️",
}

# Sources that always go to Telegram regardless of severity
_TELEGRAM_ALWAYS_SOURCES = frozenset({
    "vpn_failover",
    "docker_watchdog",
    "build_failure",
    "budget_alert",
})

# Default notification actions by module
_MODULE_ACTIONS: dict[str, list[dict]] = {
    "health": [
        {"label": "VIEW", "action": "open_panel", "target": "/command-center/health"},
        {"label": "ACKNOWLEDGE", "action": "ack", "target": None},
    ],
    "build": [
        {"label": "VIEW", "action": "open_panel", "target": "/command-center/builds"},
        {"label": "OPEN LOG", "action": "open_panel", "target": "/command-center/logs"},
        {"label": "RETRY", "action": "retry_build", "target": None},
        {"label": "ACKNOWLEDGE", "action": "ack", "target": None},
    ],
    "vpn": [
        {"label": "VIEW", "action": "open_panel", "target": "/command-center/network"},
        {"label": "ACKNOWLEDGE", "action": "ack", "target": None},
    ],
    "provider": [
        {"label": "VIEW", "action": "open_panel", "target": "/command-center/providers"},
        {"label": "ACKNOWLEDGE", "action": "ack", "target": None},
    ],
    "security": [
        {"label": "VIEW", "action": "open_panel", "target": "/command-center/security"},
        {"label": "ACKNOWLEDGE", "action": "ack", "target": None},
    ],
    "worker": [
        {"label": "VIEW", "action": "open_panel", "target": "/command-center/workers"},
        {"label": "OPEN WORKER", "action": "open_worker", "target": None},
        {"label": "ACKNOWLEDGE", "action": "ack", "target": None},
    ],
    "system": [
        {"label": "VIEW", "action": "open_panel", "target": "/command-center/system"},
        {"label": "ACKNOWLEDGE", "action": "ack", "target": None},
    ],
}

# Default actions when module doesn't have specific ones
_DEFAULT_ACTIONS = [
    {"label": "VIEW", "action": "open_panel", "target": "/command-center"},
    {"label": "ACKNOWLEDGE", "action": "ack", "target": None},
]

# Map source strings to modules for automatic routing
_SOURCE_MODULE_MAP = {
    "health_analyzer": "health",
    "health_worker": "health",
    "vpn_failover": "vpn",
    "docker_watchdog": "system",
    "build_failure": "build",
    "budget_alert": "system",
    "provider_health": "provider",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _make_dedup_key(title: str, source: str) -> str:
    """Produce a stable dedup key for a notification."""
    raw = f"{title}|{source}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _load_notifications() -> list[dict]:
    """Return the current notification list from memory.

    memory_manager.read() unwraps schema_version, returning the raw payload.
    On first call (file doesn't exist), returns [].

    The payload may be a list (normal case) or a dict (if a previous version
    stored a wrapped dict).  We handle both gracefully.
    """
    data = load(MEMORY_FILE)
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        # Legacy: could be {"records": [...]} if stored before this module
        # understood the memory_manager's auto-wrap behaviour
        return data.get("records", [])
    return []


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


class NotificationManager:
    """Stateless notification engine — all state lives in memory/notifications.json."""

    # ---------------------------------------------------------------------------
    # Event bus integration
    # ---------------------------------------------------------------------------

    @staticmethod
    def get_instance() -> "NotificationManager":
        """Return the shared NotificationManager instance."""
        return NotificationManager()

    def register_event_subscriptions(self):
        """Subscribe to event bus topics. Call once after bus.start()."""
        from core.kai_event_bus import event_bus
        event_bus.subscribe("service.down.*", self._handle_service_event)
        event_bus.subscribe("capability.health.*", self._handle_capability_event)
        event_bus.subscribe("build.failed", self._handle_build_event)
        event_bus.subscribe("remediation.*", self._handle_remediation_event)
        logger.info("Registered event bus subscriptions")

    def _handle_service_event(self, topic: str, envelope: dict):
        payload = envelope["payload"]
        svc_id = payload.get("service_id", topic.split(".")[-1])
        self.enqueue(
            severity=CRITICAL,
            title=f"Service down: {svc_id}",
            body=payload.get("message", f"Service {svc_id} is down"),
            source="event_bus",
        )

    def _handle_capability_event(self, topic: str, envelope: dict):
        payload = envelope["payload"]
        cap_id = payload.get("capability_id", "unknown")
        new_status = payload.get("new_status", "unknown")
        if new_status == "degraded":
            self.enqueue(
                severity=IMPORTANT,
                title=f"Capability degraded: {cap_id}",
                body=f"{cap_id} is degraded",
                source="event_bus",
            )

    def _handle_build_event(self, topic: str, envelope: dict):
        payload = envelope["payload"]
        self.enqueue(
            severity=IMPORTANT,
            title=f"Build failed: {payload.get('build_id', 'unknown')}",
            body=payload.get("error", "Build failed"),
            source="event_bus",
        )

    def _handle_remediation_event(self, topic: str, envelope: dict):
        payload = envelope["payload"]
        self.enqueue(
            severity=CRITICAL,
            title=f"Remediation: {payload.get('remediation_id', topic)}",
            body=payload.get("message", str(payload)),
            source="event_bus",
        )

    # ---------------------------------------------------------------------------
    # Public API (enqueue et al.)
    # ---------------------------------------------------------------------------

    @staticmethod
    def enqueue(
        severity: str,
        title: str,
        body: str,
        source: str,
        device_id: Optional[str] = None,
        module: Optional[str] = None,
        actions: Optional[list[dict]] = None,
    ) -> Optional[dict]:
        """Create a notification and deliver it through the configured channels.

        Returns the created notification dict, or None if suppressed by dedup.

        Delivery routing:
        - critical → Telegram (immediate push) + heartbeat queue
        - important → heartbeat queue (Telegram only for _TELEGRAM_ALWAYS_SOURCES)
        - informational → heartbeat queue only

        If module is not provided, it's derived from source via _SOURCE_MODULE_MAP.
        If actions are not provided, defaults are picked from _MODULE_ACTIONS.
        """
        if severity not in SEVERITY_ORDER:
            raise ValueError(
                f"Unknown severity {severity!r}; use 'critical', 'important', or 'informational'"
            )

        dedup_key = _make_dedup_key(title, source)
        now = _now_iso()
        now_ts = time.time()

        # -------------------------------------------------------------------
        # Dedup check: suppress duplicates within DEDUP_WINDOW_SECONDS
        # -------------------------------------------------------------------
        existing = _load_notifications()
        for n in existing:
            if n.get("_dedup_key") != dedup_key:
                continue
            try:
                created_ts = datetime.fromisoformat(n["created_at"]).timestamp()
            except (ValueError, KeyError, OSError):
                created_ts = 0
            if now_ts - created_ts < DEDUP_WINDOW_SECONDS:
                logger.debug(
                    "notifications: suppressed duplicate %s from %s (last: %s)",
                    title, source, n["created_at"],
                )
                return None

        # -------------------------------------------------------------------
        # Determine module and actions if not explicitly provided
        # -------------------------------------------------------------------
        if module is None:
            module = _SOURCE_MODULE_MAP.get(source, "system")
        if actions is None:
            actions = _MODULE_ACTIONS.get(module, _DEFAULT_ACTIONS)

        # -------------------------------------------------------------------
        # Build notification record
        # -------------------------------------------------------------------
        import uuid

        notif_id = f"notif_{uuid.uuid4().hex[:12]}"
        record = {
            "id": notif_id,
            "severity": severity,
            "title": title,
            "body": body,
            "source": source,
            "module": module,
            "actions": actions,
            "device_id": device_id,
            "created_at": now,
            "acked": False,
            "acked_at": None,
            "_dedup_key": dedup_key,
        }

        # -------------------------------------------------------------------
        # Persist to memory (atomic update)
        # -------------------------------------------------------------------
        def _persist(state):
            # state is what memory_manager.read() returned (after _unwrap):
            # normally a list of records, or {} on first call.
            if isinstance(state, dict) and not isinstance(state, list):
                records = state.get("records", []) if "records" in state else []
            elif isinstance(state, list):
                records = state
            else:
                records = []
            records.append(record)

            # Prune oldest if over capacity
            while len(records) > MAX_NOTIFICATIONS:
                removed = records.pop(0)
                logger.debug("notifications: pruned %s (capacity %d)", removed["id"], MAX_NOTIFICATIONS)

            return records  # memory_manager auto-wraps with schema_version

        update(MEMORY_FILE, _persist)

        # -------------------------------------------------------------------
        # Deliver to device heartbeat queue (all severities)
        # -------------------------------------------------------------------
        _deliver_to_heartbeat(record, device_id)

        # -------------------------------------------------------------------
        # Deliver to Telegram (critical + important-from-key-sources)
        # -------------------------------------------------------------------
        should_telegram = (
            severity == CRITICAL
            or source in _TELEGRAM_ALWAYS_SOURCES
        )
        if should_telegram:
            _deliver_to_telegram(record)

        logger.info(
            "notifications: enqueued %s [%s] %s from %s %s",
            notif_id, severity, title, source,
            "(telegram)" if should_telegram else "",
        )
        return record

    @staticmethod
    def list_notifications(
        severity: Optional[str] = None,
        acked: Optional[bool] = None,
        source: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> dict:
        """List notifications with optional filters.  Returns {total, notifications}."""
        records = _load_notifications()

        if severity:
            records = [r for r in records if r.get("severity") == severity]
        if acked is not None:
            records = [r for r in records if r.get("acked") == acked]
        if source:
            records = [r for r in records if r.get("source") == source]

        # Newest first
        records = sorted(records, key=lambda r: r.get("created_at", ""), reverse=True)
        total = len(records)

        # Slice after sorting
        page = records[offset:offset + limit]

        # Strip internal dedup key from API responses
        clean = []
        for r in page:
            c = dict(r)
            c.pop("_dedup_key", None)
            clean.append(c)

        return {"total": total, "notifications": clean}

    @staticmethod
    def unread_count() -> dict:
        """Return counts of unacknowledged notifications by severity."""
        records = _load_notifications()
        unacked = [r for r in records if not r.get("acked")]
        counts = {"critical": 0, "important": 0, "informational": 0, "total": len(unacked)}
        for r in unacked:
            sev = r.get("severity", "informational")
            if sev in counts:
                counts[sev] += 1
        return counts

    @staticmethod
    def ack(notif_id: str) -> Optional[dict]:
        """Acknowledge a single notification.  Returns updated record or None."""
        found = None

        def _ack(state):
            nonlocal found
            if isinstance(state, list):
                records = state
            elif isinstance(state, dict):
                records = state.get("records", [])
            else:
                records = []
            for r in records:
                if r.get("id") == notif_id:
                    r["acked"] = True
                    r["acked_at"] = _now_iso()
                    found = dict(r)
                    found.pop("_dedup_key", None)
                    break
            return records

        update(MEMORY_FILE, _ack)

        if found:
            logger.info("notifications: acked %s", notif_id)
        return found

    @staticmethod
    def ack_all() -> int:
        """Acknowledge all unacked notifications.  Returns count acked."""
        count = 0

        def _ack_all(state):
            nonlocal count
            if isinstance(state, list):
                records = state
            elif isinstance(state, dict):
                records = state.get("records", [])
            else:
                records = []
            now = _now_iso()
            for r in records:
                if not r.get("acked"):
                    r["acked"] = True
                    r["acked_at"] = now
                    count += 1
            return records

        update(MEMORY_FILE, _ack_all)
        logger.info("notifications: acked all (%d)", count)
        return count

    @staticmethod
    def get_stats() -> dict:
        """Return aggregate stats: total, by severity, by source, unacked."""
        records = _load_notifications()
        by_severity = {CRITICAL: 0, IMPORTANT: 0, INFORMATIONAL: 0}
        by_source: dict[str, int] = {}
        unacked = 0

        for r in records:
            sev = r.get("severity", INFORMATIONAL)
            by_severity[sev] = by_severity.get(sev, 0) + 1
            src = r.get("source", "unknown")
            by_source[src] = by_source.get(src, 0) + 1
            if not r.get("acked"):
                unacked += 1

        return {
            "total": len(records),
            "unacked": unacked,
            "by_severity": by_severity,
            "by_source": by_source,
        }


# ---------------------------------------------------------------------------
# Delivery channels
# ---------------------------------------------------------------------------


def _deliver_to_telegram(record: dict):
    """Send notification to the operator via Telegram (critical / important)."""
    try:
        from core.telegram_bridge import send_message

        emoji = _SEVERITY_EMOJI.get(record["severity"], "📢")
        text = (
            f"{emoji} Kai Alert [{record['severity'].upper()}]\n"
            f"{record['title']}\n\n"
            f"{record['body']}\n\n"
            f"Source: {record['source']} | ID: {record['id']}"
        )
        send_message(text)
        logger.info("notifications: telegram sent %s", record["id"])
    except Exception as exc:
        logger.error("notifications: telegram delivery failed for %s: %s", record["id"], exc)


def _deliver_to_heartbeat(record: dict, device_id: Optional[str] = None):
    """Queue notification for delivery via device heartbeat response.

    Uses device_registry.queue_notification() to add to the dedicated
    _pending_notifications queue, which is returned verbatim in the
    heartbeat response as a separate "pending_notifications" array.

    If device_id is None, the notification goes to ALL authorized devices
    that have not opted out via notification preferences.
    """
    try:
        from core.device_registry import (
            queue_notification,
            _should_deliver_notification,
            list_devices,
        )

        # Build a heartbeat-ready dict (strip internal fields)
        notification = {
            "id": record["id"],
            "severity": record["severity"],
            "title": record["title"],
            "body": record["body"],
            "module": record.get("module", "system"),
            "actions": record.get("actions", []),
            "source": record["source"],
            "created_at": record["created_at"],
        }

        if device_id:
            # Deliver to a specific device
            if not _should_deliver_notification(device_id, notification):
                logger.debug(
                    "notifications: filtered %s for device %s (preferences)",
                    record["id"], device_id,
                )
                return
            queue_notification(device_id, notification)
            logger.debug("notifications: queued %s for device %s", record["id"], device_id)
        else:
            # Deliver to all authorized devices (respecting per-device prefs)
            devices = list_devices(status="authorized")
            delivered = 0
            for dev in devices:
                did = dev.get("device_id")
                if not did:
                    continue
                if not _should_deliver_notification(did, notification):
                    continue
                queue_notification(did, notification)
                delivered += 1
            logger.debug(
                "notifications: queued %s for %d/%d device(s)",
                record["id"], delivered, len(devices),
            )
    except Exception as exc:
        logger.error("notifications: heartbeat delivery failed for %s: %s", record["id"], exc)


# ---------------------------------------------------------------------------
# Convenience: batch enqueue from orchestrator findings
# ---------------------------------------------------------------------------


def enqueue_from_findings(findings: list[dict]) -> int:
    """Convert health findings into notifications.  Returns count enqueued.

    Maps finding severity to notification severity:
    - 'critical' finding → CRITICAL notification
    - 'warning' finding → IMPORTANT notification
    - 'info' finding → INFORMATIONAL notification
    """
    count = 0
    for f in findings:
        finding_sev = f.get("severity", "info")
        if finding_sev == "critical":
            notif_sev = CRITICAL
        elif finding_sev == "warning":
            notif_sev = IMPORTANT
        else:
            notif_sev = INFORMATIONAL

        service = f.get("service", "unknown")
        issue = f.get("issue", str(f))
        result = NotificationManager.enqueue(
            severity=notif_sev,
            title=f"Issue detected: {service}",
            body=issue,
            source="health_analyzer",
        )
        if result:
            count += 1
    return count


def enqueue_from_vpn_events(events: list[dict]) -> int:
    """Convert VPN failover events into notifications.  Returns count enqueued."""
    count = 0
    for evt in events:
        sev = evt.get("severity", "info")
        notif_sev = CRITICAL if sev == "critical" else IMPORTANT
        result = NotificationManager.enqueue(
            severity=notif_sev,
            title=f"VPN: {evt.get('type', 'event')}",
            body=evt.get("message", str(evt)),
            source="vpn_failover",
        )
        if result:
            count += 1
    return count


def enqueue_build_failure(build_name: str, build_id: str, reason: str) -> Optional[dict]:
    """Notify about a build failure."""
    return NotificationManager.enqueue(
        severity=IMPORTANT,
        title=f"Build failed: {build_name}",
        body=f"Build {build_id} failed: {reason}",
        source="build_failure",
    )
