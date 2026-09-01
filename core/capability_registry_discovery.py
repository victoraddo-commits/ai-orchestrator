"""
capability_registry_discovery — auto-detection of capability assignments for services.

Provides three public functions:

- get_explicit_mapping()          → {service_id: capability_id} from kai-capability-mapping.json
- detect_capability_for_service()→ (capability_id, auto_detected)
- link_services_to_capabilities() → wires ServiceRegistry services into CapabilityRegistry
"""
import json
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

MAPPING_FILE = Path("/project/uploads/kai-capability-mapping.json")

# Name-pattern → capability_id  (checked case-insensitively against service id AND name)
NAME_CAPABILITY_MAP = {
    "telegram":    "telegram-bots",
    "kai-notify":  "notifications",
    "kai-vault":   "secret-management",
    "kai-legal":   "legal-brain",
    "kai-money":   "trading-engine",
    "kai-audit":   "audit",
    "proxdash":    "infra-monitoring",
    "it-manager":  "hr-tools",
    "juris-kai":   "legal-brain",
}

# Port → capability_id  (first matching port wins when port is a list)
PORT_CAPABILITY_MAP = {
    8094: "notifications",
    8120: "secret-management",
    8443: "telegram-bots",
    8092: "hr-tools",
    8093: "audit",
    8095: "trading-engine",
}


# ----------------------------------------------------------------------------------------
# 1. Explicit mapping
# ----------------------------------------------------------------------------------------

def get_explicit_mapping() -> dict[str, str]:
    """Load the explicit capability mapping file.

    Returns ``{}`` if the file is missing or fails to load.
    Keys that start with ``_`` are silently skipped.
    """
    if not MAPPING_FILE.exists():
        return {}
    try:
        with open(MAPPING_FILE) as f:
            raw: dict = json.load(f)
        return {k: v for k, v in raw.items() if not k.startswith("_")}
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Failed to load explicit capability mapping %s: %s", MAPPING_FILE, exc)
        return {}


# ----------------------------------------------------------------------------------------
# 2. Detection logic
# ----------------------------------------------------------------------------------------

def _port_to_capability(port: int) -> Optional[str]:
    """Return the capability_id for a single port number, or None."""
    return PORT_CAPABILITY_MAP.get(port)


def _name_to_capability(service_id: str, name: str) -> Optional[str]:
    """Return the capability_id for the first matching name pattern, or None."""
    combined = f"{service_id.lower()} {name.lower()}"
    for pattern, cap_id in NAME_CAPABILITY_MAP.items():
        if pattern in combined:
            return cap_id
    return None


def detect_capability_for_service(service: dict) -> tuple[str, bool]:
    """Detect which capability a service belongs to.

    Priority order
    --------------
    1. Explicit mapping  — looked up by service ``id``; returns (cap_id, False)
    2. Name patterns    — checked case-insensitively against id AND name; returns (cap_id, True)
    3. Port number      — ``port`` field, int or list, first match wins; returns (cap_id, True)
    4. Fallback         — ``"unknown"``, True

    Parameters
    ----------
    service : dict
        Service record with at least an ``id`` key.

    Returns
    -------
    tuple[str, bool]
        (capability_id, auto_detected)
    """
    service_id = service.get("id", "")
    name = service.get("name", "")

    # Priority 1 — explicit mapping
    explicit = get_explicit_mapping()
    if service_id in explicit:
        return (explicit[service_id], False)

    # Priority 2 — name patterns
    cap_id = _name_to_capability(service_id, name)
    if cap_id:
        return (cap_id, True)

    # Priority 3 — port number
    port_or_ports = service.get("port")
    if port_or_ports is not None:
        if isinstance(port_or_ports, list):
            for p in port_or_ports:
                cap_id = _port_to_capability(p)
                if cap_id:
                    return (cap_id, True)
        else:
            cap_id = _port_to_capability(port_or_ports)
            if cap_id:
                return (cap_id, True)

    # Priority 4 — fallback
    return ("unknown", True)


# ----------------------------------------------------------------------------------------
# 3. Wiring services into the CapabilityRegistry
# ----------------------------------------------------------------------------------------

def link_services_to_capabilities(registry) -> None:
    """Wire every Service Registry service into the CapabilityRegistry.

    For each service:
    - Determines its capability via ``detect_capability_for_service``.
    - Creates the capability record if it does not yet exist.
    - Appends an implementation entry (role=primary if explicit mapping,
      secondary if auto-detected) unless the service is already linked.
    - Calls ``registry.save()`` at the end if anything changed.
    """
    from core.service_registry import ServiceRegistry

    svc_reg = ServiceRegistry.get_instance()
    services = svc_reg.list_services()

    changed = False

    for sid, svc in services.items():
        cap_id, auto_detected = detect_capability_for_service(svc)

        # Ensure the capability record exists
        if cap_id not in registry._capabilities:
            registry._capabilities[cap_id] = {
                "capability_id":   cap_id,
                "name":            cap_id.replace("-", " ").title(),
                "canonical_owner": svc.get("owner", "unknown"),
                "priority":        "P2",
                "status":          "unknown",
                "version":         "1.0",
                "description":     "",
                "implementations": [],
                "permissions_required":  [],
                "required_identity":     "",
                "data_source":            "",
                "depends_on":             [],
                "consumed_by":            [],
                "consumed_by_override":   [],
                "health_history":         [],
            }
            changed = True

        # Link the service if not already linked
        impls = registry._capabilities[cap_id].setdefault("implementations", [])
        if not any(i.get("service_id") == sid for i in impls):
            role = "secondary" if auto_detected else "primary"
            impls.append({
                "service_id":    sid,
                "role":          role,
                "health":        svc.get("status", "unknown"),
                "auto_detected": auto_detected,
                "override":      not auto_detected,
            })
            changed = True

    if changed:
        registry.save()
