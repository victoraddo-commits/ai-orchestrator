"""Action schema registry and validation for the Cerebrum Simulation Engine."""
import uuid
from typing import Any, Dict, List, Optional

ACTION_SCHEMAS: Dict[str, Dict[str, Any]] = {}


def get_schema(action_type: str) -> Optional[Dict[str, Any]]:
    return ACTION_SCHEMAS.get(action_type)


def list_action_types() -> List[str]:
    return sorted(ACTION_SCHEMAS.keys())


def actions_for_domain(domain: str) -> List[str]:
    return sorted(k for k, v in ACTION_SCHEMAS.items() if v.get("domain") == domain)


def validate_action(action_type: str, params: Dict[str, Any]):
    if action_type not in ACTION_SCHEMAS:
        raise ValueError(f"unknown action_type: {action_type}")

    schema = ACTION_SCHEMAS[action_type]
    required = schema.get("required_params", [])

    for rp in required:
        if rp not in params or params[rp] is None:
            raise ValueError(f"missing required parameters: {rp}")

    all_known = set(required)
    for known in schema.get("optional_params", []):
        all_known.add(known)
    for k in params:
        if k not in all_known:
            raise ValueError(f"unknown parameters: {k}")


def new_action(action_type: str, params: Dict[str, Any],
               proposed_by: str = "kai", trace_id: Optional[str] = None,
               domain: Optional[str] = None) -> Dict[str, Any]:
    if action_type not in ACTION_SCHEMAS:
        raise ValueError(f"Unknown action_type: {action_type}")

    schema = ACTION_SCHEMAS[action_type]
    ac_domain = domain or schema.get("domain", "infrastructure")

    # Derive target from common param names
    target = params.get("service_name") or params.get("target_service") or params.get(
        "target") or params.get("application_name", "")

    return {
        "action_type": action_type,
        "status": "proposed",
        "domain": ac_domain,
        "target": target,
        "parameters": params,
        "proposed_by": proposed_by,
        "id": str(uuid.uuid4()),
        "trace_id": trace_id or str(uuid.uuid4()),
    }


# ── Register all action schemas ──

ACTION_SCHEMAS["restart_service"] = {
    "domain": "infrastructure",
    "required_params": ["service_name"],
    "optional_params": [],
}

ACTION_SCHEMAS["scale_service"] = {
    "domain": "infrastructure",
    "required_params": ["service_name", "replicas"],
    "optional_params": [],
}

ACTION_SCHEMAS["config_change"] = {
    "domain": "infrastructure",
    "required_params": ["target_service", "config_key", "new_value"],
    "optional_params": [],
}

ACTION_SCHEMAS["resource_allocation"] = {
    "domain": "infrastructure",
    "required_params": ["target_service", "resource_type", "amount"],
    "optional_params": [],
}

ACTION_SCHEMAS["dependency_update"] = {
    "domain": "infrastructure",
    "required_params": ["service_name", "dependency_name", "target_version"],
    "optional_params": [],
}

ACTION_SCHEMAS["network_change"] = {
    "domain": "infrastructure",
    "required_params": ["target", "change_type", "port"],
    "optional_params": [],
}

ACTION_SCHEMAS["deploy_application"] = {
    "domain": "application",
    "required_params": ["application_name", "version"],
    "optional_params": ["canary_percent", "rollback_on_failure"],
}
