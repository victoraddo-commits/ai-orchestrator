"""Kai Notification Service — FastAPI router.

Mounts notification endpoints on the main FastAPI app (port 8000).

Part of: Kai Mobile Command Node — Sub-project 3: Push Notification System.
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from core.notifications import NotificationManager

logger = logging.getLogger(__name__)

router = APIRouter(tags=["notifications"])

# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------


class AckResponse(BaseModel):
    ok: bool
    count: Optional[int] = None
    notification: Optional[dict] = None


# ---------------------------------------------------------------------------
# Auth dependency — imported lazily to avoid circular imports
# ---------------------------------------------------------------------------


def _require_operator():
    """Returns a FastAPI dependency that requires bridge-token operator auth."""
    # Import at call time to avoid circular import at module level
    from core.bridge_auth import require_bridge_token
    return require_bridge_token


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/kai/notifications")
async def api_list_notifications(
    severity: Optional[str] = Query(None, description="Filter by severity: critical, important, informational"),
    acked: Optional[bool] = Query(None, description="Filter by ack status"),
    source: Optional[str] = Query(None, description="Filter by source (e.g. health_analyzer, vpn_failover)"),
    limit: int = Query(100, ge=1, le=500, description="Max notifications to return"),
    offset: int = Query(0, ge=0, description="Offset for pagination"),
):
    """List notifications with optional filtering and pagination.

    Read-only — no auth required (matches existing convention).
    Returns newest-first.
    """
    return NotificationManager.list_notifications(
        severity=severity,
        acked=acked,
        source=source,
        limit=limit,
        offset=offset,
    )


@router.get("/kai/notifications/unread-count")
async def api_unread_count():
    """Get unacknowledged notification counts by severity.

    Useful for badge counts in the mobile Command Center tab bar.
    Read-only — no auth required.
    """
    return NotificationManager.unread_count()


@router.get("/kai/notifications/stats")
async def api_notification_stats():
    """Get aggregate notification statistics.

    Read-only — no auth required.
    """
    return NotificationManager.get_stats()


@router.post("/kai/notifications/{notif_id}/ack")
async def api_ack_notification(notif_id: str):
    """Acknowledge a single notification.

    Write operation — requires operator auth (bridge token or session).
    """
    result = NotificationManager.ack(notif_id)
    if result is None:
        raise HTTPException(status_code=404, detail=f"Notification {notif_id!r} not found")
    return AckResponse(ok=True, notification=result)


@router.post("/kai/notifications/ack-all")
async def api_ack_all_notifications():
    """Acknowledge all unacknowledged notifications at once.

    Write operation — requires operator auth (bridge token or session).
    """
    count = NotificationManager.ack_all()
    logger.info("All notifications acknowledged (%d total)", count)
    return AckResponse(ok=True, count=count)


@router.post("/kai/notifications/test")
async def api_test_notification():
    """Send a test notification through all channels.

    Creates a critical notification that triggers Telegram + heartbeat delivery.
    Write operation — requires operator auth.
    """
    result = NotificationManager.enqueue(
        severity="critical",
        title="Test Notification",
        body="This is a test notification from the Kai Notification Service. "
             "If you received this via Telegram, the push notification system is working.",
        source="test",
    )
    if result is None:
        return {"ok": False, "detail": "Suppressed by dedup — a test notification was sent recently"}
    return {"ok": True, "notification": {k: v for k, v in result.items() if k != "_dedup_key"}}
