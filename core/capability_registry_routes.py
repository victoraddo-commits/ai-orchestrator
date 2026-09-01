"""Capability Registry API routes."""
from __future__ import annotations

import logging
from fastapi import APIRouter, HTTPException, Query
from typing import Optional

from core.capability_registry import CapabilityRegistry

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/kai/capabilities", tags=["kai", "capabilities"])

# Note: write endpoints (POST, PUT, DELETE, /discover, /implementations) are not yet
# auth-gated — Phase 15A adds capability-based auth guards to all mutating routes.

_registry: Optional[CapabilityRegistry] = None


def get_registry() -> CapabilityRegistry:
    global _registry
    if _registry is None:
        _registry = CapabilityRegistry.get_instance()
    return _registry


def _ok(data, **meta):
    result = {"ok": True, "data": data}
    if meta:
        result["meta"] = meta
    return result


@router.get("")
def list_capabilities(
    status: Optional[str] = Query(None, description="Filter by status"),
    priority: Optional[str] = Query(None, description="Filter by priority"),
    owner: Optional[str] = Query(None, description="Filter by canonical_owner"),
):
    """List all capabilities, optionally filtered by status, priority, or owner."""
    reg = get_registry()
    caps = reg.list_capabilities(status=status, priority=priority, owner=owner)
    return _ok(caps, count=len(caps))


@router.post("/discover")
def trigger_discovery():
    """Trigger auto-discovery of capability assignments from ServiceRegistry."""
    reg = get_registry()
    reg.auto_discover()
    caps = reg.list_capabilities()
    return _ok({"discovered": True, "total_capabilities": len(caps)}, count=len(caps))


@router.get("/{cap_id}")
def get_capability(cap_id: str):
    """Get a single capability by ID, including its health_history. 404 if not found."""
    reg = get_registry()
    cap = reg.get_capability(cap_id)
    if cap is None:
        raise HTTPException(status_code=404, detail=f"Capability '{cap_id}' not found")
    return _ok(cap)


@router.get("/{cap_id}/health")
def check_health(cap_id: str):
    """Refresh health for a capability, save the updated state, and return the new status."""
    reg = get_registry()
    cap = reg.get_capability(cap_id)
    if cap is None:
        raise HTTPException(status_code=404, detail=f"Capability '{cap_id}' not found")
    reg.refresh_health(cap_id)
    reg.save()
    # refresh_health mutates cap in-place via the registry's _capabilities dict,
    # so the cap reference we already hold is still valid to return.
    return _ok(cap)


# TODO(phase-15a): add _require_write_capability guard
@router.post("")
def register_capability(body: dict):
    """Register a new capability. Requires 'capability_id' in body. Returns 400 if missing."""
    if "capability_id" not in body:
        raise HTTPException(status_code=400, detail="capability_id is required in body")
    cap_id = body["capability_id"]
    reg = get_registry()
    reg.upsert_capability(cap_id, body)
    reg.save()
    return _ok(reg.get_capability(cap_id))


# TODO(phase-15a): add _require_write_capability guard
@router.put("/{cap_id}")
def update_capability(cap_id: str, body: dict):
    """Update a capability. Body replaces merged record. 404 if not found."""
    reg = get_registry()
    existing = reg.get_capability(cap_id)
    if existing is None:
        raise HTTPException(status_code=404, detail=f"Capability '{cap_id}' not found")
    reg.upsert_capability(cap_id, body)
    reg.save()
    return _ok(reg.get_capability(cap_id))


# TODO(phase-15a): add _require_write_capability guard
@router.delete("/{cap_id}")
def deregister_capability(cap_id: str):
    """Remove a capability. 404 if not found."""
    reg = get_registry()
    if not reg.delete_capability(cap_id):
        raise HTTPException(status_code=404, detail=f"Capability '{cap_id}' not found")
    reg.save()
    return _ok({"deleted": True, "capability_id": cap_id})


# TODO(phase-15a): add _require_write_capability guard
@router.post("/{cap_id}/implementations")
def add_implementation(cap_id: str, body: dict):
    """Add a service implementation to a capability. Body: {service_id, role?}. 400 if missing service_id. 404 if cap not found."""
    if "service_id" not in body:
        raise HTTPException(status_code=400, detail="service_id is required in body")
    service_id = body["service_id"]
    role = body.get("role", "primary")
    reg = get_registry()
    if reg.get_capability(cap_id) is None:
        raise HTTPException(status_code=404, detail=f"Capability '{cap_id}' not found")
    success = reg.add_implementation(cap_id, service_id, role=role)
    if not success:
        raise HTTPException(status_code=400, detail=f"Invalid role '{role}' (must be primary or secondary)")
    reg.save()
    return _ok(reg.get_capability(cap_id))


# TODO(phase-15a): add _require_write_capability guard
@router.delete("/{cap_id}/implementations/{service_id}")
def remove_implementation(cap_id: str, service_id: str):
    """Remove a service implementation from a capability. 404 if not found."""
    reg = get_registry()
    if reg.get_capability(cap_id) is None:
        raise HTTPException(status_code=404, detail=f"Capability '{cap_id}' not found")
    if not reg.remove_implementation(cap_id, service_id):
        raise HTTPException(status_code=404, detail=f"Implementation '{service_id}' not found on capability '{cap_id}'")
    reg.save()
    return _ok(reg.get_capability(cap_id))


@router.get("/{cap_id}/dependents")
def get_dependents(cap_id: str):
    """Return consumed_by and consumed_by_override for a capability. 404 if not found."""
    reg = get_registry()
    cap = reg.get_capability(cap_id)
    if cap is None:
        raise HTTPException(status_code=404, detail=f"Capability '{cap_id}' not found")
    return _ok({
        "consumed_by": cap.get("consumed_by", []),
        "consumed_by_override": cap.get("consumed_by_override", []),
    })
