"""Service Registry API routes."""
import logging
from fastapi import APIRouter, HTTPException, Query
from typing import Optional
from core.authz import _require_write_capability
from core.service_registry import ServiceRegistry
from core.kai_event_bus import event_bus

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/kai/services", tags=["kai", "services"])

# Singleton registry instance
_registry: Optional[ServiceRegistry] = None


def get_registry() -> ServiceRegistry:
    global _registry
    if _registry is None:
        _registry = ServiceRegistry.get_instance()
    return _registry


def _ok(data, **meta):
    result = {"ok": True, "data": data}
    if meta:
        result["meta"] = meta
    return result


@router.get("")
def list_services(
    status: Optional[str] = Query(None),
    environment: Optional[str] = Query(None),
    source: Optional[str] = Query(None),
):
    """List all services, optionally filtered by status/environment/source."""
    reg = get_registry()
    services = reg.list_services()
    if status:
        services = {k: v for k, v in services.items() if v.get("status") == status}
    if environment:
        services = {k: v for k, v in services.items() if v.get("environment") == environment}
    if source:
        services = {k: v for k, v in services.items() if v.get("source") == source}
    return _ok(services, count=len(services))


@router.get("/health")
def all_health():
    """Summary of all service health statuses."""
    reg = get_registry()
    services = reg.list_services()
    summary = {}
    for sid, svc in services.items():
        summary[sid] = {
            "name": svc.get("name"),
            "status": svc.get("status"),
            "last_health_check": svc.get("last_health_check"),
            "last_health_result": svc.get("last_health_result"),
        }
    return _ok(summary)


@router.post("/discover")
def trigger_discovery():
    """Trigger a full auto-discovery scan across all probes."""
    reg = get_registry()
    results = reg.run_discovery()
    return _ok({
        "docker_containers": len(results.get("docker", [])),
        "systemd_services": len(results.get("systemd", [])),
        "port_probes": len(results.get("ports", [])),
        "proxmox_containers": len(results.get("proxmox", [])),
        "total_services": len(reg.list_services()),
    })


# NOTE: /{service_id}/dependencies (not /dependencies/{service_id}) — Starlette 1.3.1
# APIRouter prefix handling has a bug where multi-segment static paths under a
# prefixed router fail to match. Moving dependencies under /{service_id}/...
# makes it consistent with /{service_id}/health which works correctly.
@router.get("/{service_id}/dependencies")
def get_dependencies(service_id: str):
    """Dependency tree for a service (up + down).

    depends_on: services this service requires (from metadata/depends_on).
    dependents: services that list this service in their own depends_on.
    """
    reg = get_registry()
    if not reg.get_service(service_id):
        raise HTTPException(status_code=404, detail=f"Service '{service_id}' not found")
    svc = reg.get_service(service_id)
    depends_on = svc.get("metadata", {}).get("depends_on", [])
    # Compute dependents by scanning all services for mutual depends_on references
    dependents = []
    for other_id, other in reg.list_services().items():
        if other_id == service_id:
            continue
        other_deps = other.get("metadata", {}).get("depends_on", [])
        if service_id in other_deps:
            dependents.append(other_id)
    return _ok({
        "service_id": service_id,
        "depends_on": depends_on,
        "dependents": dependents,
    })


@router.get("/{service_id}/health")
def check_health(service_id: str):
    """Trigger a live health check on a specific service."""
    reg = get_registry()
    if not reg.get_service(service_id):
        raise HTTPException(status_code=404, detail=f"Service '{service_id}' not found")
    result = reg.check_service_health(service_id)
    return _ok(result)


@router.get("/{service_id}")
def get_service(service_id: str):
    """Service detail with recent health history (last 10 entries)."""
    reg = get_registry()
    svc = reg.get_service(service_id)
    if not svc:
        raise HTTPException(status_code=404, detail=f"Service '{service_id}' not found")
    health = [h for h in reg._health_history if h["service_id"] == service_id][-10:]
    result = dict(svc)
    result["health_history"] = health
    return _ok(result)


@router.post("", dependencies=[_require_write_capability("services.manage")])
def register_service(payload: dict):
    """Register a new service manually."""
    if "id" not in payload:
        raise HTTPException(status_code=400, detail="Field 'id' is required")
    reg = get_registry()
    payload["source"] = "manual"
    reg.upsert_service(payload)
    event_bus.publish("service.registered", {"service_id": payload["id"], "name": payload.get("name")}, source="service_registry")
    return _ok({"id": payload["id"], "registered": True})


@router.put("/{service_id}", dependencies=[_require_write_capability("services.manage")])
def update_service(service_id: str, payload: dict):
    """Update a service."""
    reg = get_registry()
    existing = reg.get_service(service_id)
    if not existing:
        raise HTTPException(status_code=404, detail=f"Service '{service_id}' not found")
    payload["id"] = service_id
    payload["source"] = existing.get("source", "manual")
    reg.upsert_service(payload)
    event_bus.publish("service.updated", {"service_id": service_id, "name": payload.get("name")}, source="service_registry")
    return _ok({"id": service_id, "updated": True})


@router.delete("/{service_id}", dependencies=[_require_write_capability("services.manage")])
def deregister_service(service_id: str):
    """Deregister a service."""
    reg = get_registry()
    if not reg.get_service(service_id):
        raise HTTPException(status_code=404, detail=f"Service '{service_id}' not found")
    reg.delete_service(service_id)
    event_bus.publish("service.deregistered", {"service_id": service_id}, source="service_registry")
    return _ok({"id": service_id, "deleted": True})
