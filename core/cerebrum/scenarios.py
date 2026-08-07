"""Scenario generation for Cerebrum Simulation Engine."""
import copy
import uuid
from typing import Any, Dict, List, Optional

_BASE_PARAMETERS = {
    "service_name": "",
    "force": False,
    "grace_period_seconds": 10,
    "health_check_timeout": 30,
    "rollback_on_failure": True,
}


def generate_scenarios(action: Dict[str, Any],
                       world_state: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    at = action.get("action_type", "")
    params = dict(action.get("parameters", {}))
    target = action.get("target", "")
    domain = action.get("domain", "infrastructure")

    scenarios = []

    # Base
    base_params = copy.deepcopy(params)
    base_params.update({"force": False, "grace_period_seconds": 10})
    scenarios.append({
        "id": str(uuid.uuid4()),
        "type": "base",
        "label": "base",
        "parameters": base_params,
        "probability_weight": 0.40,
        "target": target,
        "domain": domain,
        "action_type": at,
    })

    # Optimistic
    opt_params = copy.deepcopy(params)
    opt_params["force"] = False
    opt_params["grace_period_seconds"] = 30
    if at == "scale_service":
        opt_params["replicas"] = min(params.get("replicas", 1) + 2, 10)
    elif at == "resource_allocation":
        opt_params["amount"] = params.get("amount", 0) * 1.5
    elif at == "deploy_application":
        opt_params["canary_percent"] = 20
        opt_params["rollback_on_failure"] = True
    scenarios.append({
        "id": str(uuid.uuid4()),
        "type": "optimistic",
        "label": "optimistic",
        "parameters": opt_params,
        "probability_weight": 0.15,
        "target": target,
        "domain": domain,
        "action_type": at,
    })

    # Pessimistic
    pess_params = copy.deepcopy(params)
    pess_params["force"] = False
    pess_params["grace_period_seconds"] = 5
    if at == "scale_service":
        pess_params["replicas"] = max(params.get("replicas", 1) - 1, 1)
    elif at == "resource_allocation":
        pess_params["amount"] = params.get("amount", 0) * 0.5
    elif at == "dependency_update":
        pess_params["target_version"] = params.get("target_version", "") + "-breaking"
    elif at == "network_change":
        pess_params["change_type"] = "firewall"
    scenarios.append({
        "id": str(uuid.uuid4()),
        "type": "pessimistic",
        "label": "pessimistic",
        "parameters": pess_params,
        "probability_weight": 0.15,
        "target": target,
        "domain": domain,
        "action_type": at,
    })

    # Adversarial
    adv_params = copy.deepcopy(params)
    adv_params["force"] = True
    adv_params["grace_period_seconds"] = 0
    if at == "resource_allocation":
        adv_params["amount"] = 0
    scenarios.append({
        "id": str(uuid.uuid4()),
        "type": "adversarial",
        "label": "adversarial",
        "parameters": adv_params,
        "probability_weight": 0.30,
        "target": target,
        "domain": domain,
        "action_type": at,
    })

    return scenarios
