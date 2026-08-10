"""Kai Device Registry — FastAPI router.

Mounts all device endpoints on the main FastAPI app (port 8000).

Part of: Kai Mobile Command Node — Sub-project 1: Device Registration & Auth.
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel

from core.device_registry import (
    register_device,
    get_device,
    list_devices,
    revoke_device,
    delete_device,
    update_heartbeat,
    ack_commands,
    ack_notifications,
    get_notification_config,
    update_notification_config,
    find_device_by_token,
    DeviceNotFoundError,
    DuplicateDeviceError,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["devices"])

# ---------------------------------------------------------------------------
# Pydantic schemas for request/response
# ---------------------------------------------------------------------------


class RegisterDeviceRequest(BaseModel):
    device_id: str
    device_name: str
    platform: str = "android"
    platform_version: str
    manufacturer: str = "Samsung"
    model: str
    one_ui_version: Optional[str] = None
    security_patch: Optional[str] = None
    vpn_ip: Optional[str] = None
    capabilities: list[str] = []
    assigned_worker: Optional[str] = None


class HeartbeatRequest(BaseModel):
    battery_pct: Optional[int] = None
    charging: bool = False
    vpn_ip: Optional[str] = None
    network_type: str = "unknown"
    agent_version: Optional[str] = None
    notification_status: str = "unknown"
    ack_ids: list[str] = []
    ack_notification_ids: list[str] = []


# ---------------------------------------------------------------------------
# Auth dependencies
# ---------------------------------------------------------------------------

# Imported at function level to avoid circular imports at module level


async def _require_device_token(
    x_kai_session: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
) -> str:
    """Resolve a device bearer token from headers.

    Checks Authorization header for Bearer token, then falls back to
    X-Kai-Session header.

    Returns device_id on success, raises 401 on failure.
    """

    token = None

    # Try Authorization: Bearer header first
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()

    # Fall back to session header
    if not token and x_kai_session:
        token = x_kai_session.strip()

    if not token:
        raise HTTPException(status_code=401, detail="Missing authentication token")

    device = find_device_by_token(token)
    if device is None:
        raise HTTPException(status_code=401, detail="Invalid or revoked device token")

    return device["device_id"]


from core.bridge_auth import require_bridge_token


def _verify_device_owns_path(authenticated_device: str, device_id_from_path: str):
    """Check that the authenticated device matches the path parameter."""
    if authenticated_device != device_id_from_path:
        raise HTTPException(status_code=403, detail="Device token does not match requested device")


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post("/kai/devices/register")
async def api_register_device(
    body: RegisterDeviceRequest,
    _operator: str = Depends(require_bridge_token),
):
    """Register a new Kai device.  Operator-only.

    Returns the device record with the raw bearer token.
    The token is only returned ONCE — store it securely.
    """

    try:
        record = register_device(
            device_id=body.device_id,
            device_name=body.device_name,
            platform=body.platform,
            platform_version=body.platform_version,
            manufacturer=body.manufacturer,
            model=body.model,
            one_ui_version=body.one_ui_version,
            security_patch=body.security_patch,
            vpn_ip=body.vpn_ip,
            capabilities=body.capabilities,
            assigned_worker=body.assigned_worker,
            registered_by=_operator,
        )
    except DuplicateDeviceError as e:
        raise HTTPException(status_code=409, detail=str(e))

    logger.info("Device registered: %s by %s", body.device_id, _operator)
    return record


@router.get("/kai/devices")
async def api_list_devices(status: Optional[str] = None):
    """List all registered devices.  Read-only — no auth required (matches
    existing convention where GET endpoints are unrestricted)."""

    return {"devices": list_devices(status=status)}


@router.get("/kai/devices/{device_id}")
async def api_get_device(device_id: str):
    """Get a single device record.  Read-only — no auth required."""

    record = get_device(device_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Device {device_id!r} not found")
    return record


@router.post("/kai/devices/{device_id}/heartbeat")
async def api_device_heartbeat(
    device_id: str,
    body: HeartbeatRequest,
    authenticated_device: str = Depends(_require_device_token),
):
    """Device heartbeat — updates last_seen and returns pending commands.

    Authenticated by device bearer token.  The token must match device_id
    in the path (a device can only heartbeat for itself).
    """

    _verify_device_owns_path(authenticated_device, device_id)

    # Process acknowledgements BEFORE building heartbeat response so
    # the pending lists reflect what's actually pending.
    if body.ack_ids:
        acked = ack_commands(device_id, body.ack_ids)
        if acked > 0:
            logger.debug("Device %s acknowledged %d commands", device_id, acked)

    if body.ack_notification_ids:
        acked_notifs = ack_notifications(device_id, body.ack_notification_ids)
        if acked_notifs > 0:
            logger.debug("Device %s acknowledged %d notifications", device_id, acked_notifs)

    try:
        result = update_heartbeat(device_id, heartbeat_data={
            "battery_pct": body.battery_pct,
            "charging": body.charging,
            "vpn_ip": body.vpn_ip,
            "network_type": body.network_type,
            "agent_version": body.agent_version,
            "notification_status": body.notification_status,
        })
    except DeviceNotFoundError:
        raise HTTPException(status_code=404, detail=f"Device {device_id!r} not found")

    return result


@router.post("/kai/devices/{device_id}/revoke")
async def api_revoke_device(
    device_id: str,
    _operator: str = Depends(require_bridge_token),
):
    """Revoke a device.  Operator-only.  Irreversible."""

    try:
        result = revoke_device(device_id)
    except DeviceNotFoundError:
        raise HTTPException(status_code=404, detail=f"Device {device_id!r} not found")

    logger.info("Device revoked: %s by %s", device_id, _operator)
    return result


@router.delete("/kai/devices/{device_id}")
async def api_delete_device(
    device_id: str,
    _operator: str = Depends(require_bridge_token),
):
    """Delete a device record.  Operator-only."""

    try:
        result = delete_device(device_id)
    except DeviceNotFoundError:
        raise HTTPException(status_code=404, detail=f"Device {device_id!r} not found")

    logger.info("Device deleted: %s by %s", device_id, _operator)
    return result


# ---------------------------------------------------------------------------
# Notification preferences
# ---------------------------------------------------------------------------


class NotificationConfigRequest(BaseModel):
    """Per-device notification preferences.  All fields optional — only
    provided keys are updated (deep-merge for nested dicts)."""
    enabled: Optional[bool] = None
    per_severity: Optional[dict[str, bool]] = None
    per_module: Optional[dict[str, bool]] = None
    per_source: Optional[dict[str, bool]] = None


@router.get("/kai/devices/{device_id}/notification-config")
async def api_get_notification_config(device_id: str):
    """Get per-device notification preferences.  Returns defaults if unset.

    Anyone can read — no auth required (matches existing convention).
    """
    config = get_notification_config(device_id)
    if config is None:
        raise HTTPException(status_code=404, detail=f"Device {device_id!r} not found")
    return {"device_id": device_id, "notification_config": config}


@router.put("/kai/devices/{device_id}/notification-config")
async def api_update_notification_config(
    device_id: str,
    body: NotificationConfigRequest,
    _operator: str = Depends(require_bridge_token),
):
    """Update per-device notification preferences.  Operator-only.

    Provides only the keys you want to change — nested dicts are deep-merged.
    Example: {"per_severity": {"informational": false}} turns off info alerts
    while keeping critical/important enabled.
    """
    try:
        config = update_notification_config(
            device_id,
            {k: v for k, v in body.dict().items() if v is not None},
        )
    except DeviceNotFoundError:
        raise HTTPException(status_code=404, detail=f"Device {device_id!r} not found")

    return {"device_id": device_id, "notification_config": config}
