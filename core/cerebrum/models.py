"""Model registry for Cerebrum Simulation Engine."""
from typing import Any, Callable, Dict, List, Optional

_models: Dict[str, Dict[str, Any]] = {}
_model_fns: Dict[str, Callable] = {}


def register_model(name: str, model_type: str, domain: str, fn: Callable,
                   inputs: Optional[List[str]] = None,
                   outputs: Optional[List[str]] = None,
                   metadata: Optional[Dict[str, Any]] = None):
    _models[name] = {
        "name": name,
        "model_type": model_type,
        "domain": domain,
        "fn": fn,
        "inputs": inputs or ["scenario", "state_snapshot", "action"],
        "outputs": outputs or ["success_probability"],
        "metadata": metadata or {},
    }
    _model_fns[name] = fn


def get_model(name: str) -> Optional[Dict[str, Any]]:
    return _models.get(name)


def get_model_fn(name: str) -> Optional[Callable]:
    return _model_fns.get(name)


def unregister_model(name: str):
    _models.pop(name, None)
    _model_fns.pop(name, None)


def list_models(domain: Optional[str] = None) -> Dict[str, Dict[str, Any]]:
    if domain is None:
        return dict(_models)
    return {k: v for k, v in _models.items() if v.get("domain") == domain}


def _deterministic_infra_model(scenario: Dict[str, Any], state: Dict[str, Any],
                                action: Dict[str, Any]) -> Dict[str, Any]:
    at = action.get("action_type", "")
    params = action.get("parameters", {})
    target = action.get("target", "")
    stype = scenario.get("type", "base")
    entity_state = state.get("entity_state", {})
    learning = state.get("learning", [])

    success = 0.90

    if stype == "optimistic":
        success += 0.05
    elif stype == "pessimistic":
        success -= 0.20
    elif stype == "adversarial":
        success -= 0.40

    if target and target in entity_state:
        incidents = entity_state[target].get("recent_incidents_count", 0)
        success -= incidents * 0.05

    trusted_count = sum(1 for l in learning if l.get("action") == at and l.get("recommendation") == "trusted")
    success += trusted_count * 0.02

    risk_factors = []
    estimated_downtime = 5.0
    rollback_complexity = "low"

    if at == "restart_service":
        estimated_downtime = 10.0
    elif at == "scale_service":
        current = entity_state.get(target, {}).get("current_state", {}).get("replicas", 1)
        replicas = params.get("replicas", 1)
        estimated_downtime = abs(replicas - current) * 2.0
        success = min(success, 0.95 if replicas > current else success)
    elif at == "config_change":
        rollback_complexity = "medium"
        risk_factors.append("config_drift_risk")
        estimated_downtime = 2.0
    elif at == "resource_allocation":
        risk_factors.append("memory_allocation_oom_risk")
        estimated_downtime = 15.0
    elif at == "dependency_update":
        risk_factors.append("breaking_change_possible")
        estimated_downtime = 20.0
        success -= 0.10
    elif at == "network_change":
        risk_factors.append("complete_isolation_risk")
        estimated_downtime = 30.0
        success -= 0.15
    elif at == "deploy_application":
        estimated_downtime = 5.0
    else:
        risk_factors.append("unknown_action_type")

    success = max(0.05, min(0.99, success))
    capacity_change_ratio = float(params.get("replicas", 1)) / max(1, entity_state.get(
        target, {}).get("current_state", {}).get("replicas", 1))

    result = {
        "success_probability": success,
        "risk_factors": risk_factors,
        "estimated_downtime_seconds": estimated_downtime,
        "expected_impact": "high" if len(risk_factors) >= 3 else "medium" if risk_factors else "low",
    }

    if at in ("scale_service", "resource_allocation"):
        result["capacity_change_ratio"] = capacity_change_ratio
    if at == "config_change":
        result["rollback_complexity"] = rollback_complexity

    return result


def _deterministic_deploy_model(scenario: Dict[str, Any], state: Dict[str, Any],
                                 action: Dict[str, Any]) -> Dict[str, Any]:
    stype = scenario.get("type", "base")
    builds = state.get("builds", [])
    params = action.get("parameters", {})

    success = 0.85

    if stype == "optimistic":
        success += 0.08
    elif stype == "pessimistic":
        success -= 0.15
    elif stype == "adversarial":
        success -= 0.35

    risk_factors = []
    target = action.get("target", "")
    target_builds = [b for b in builds if b.get("name") == target]
    failed = [b for b in target_builds if b.get("status") == "failed"]

    if failed:
        risk_factors.append("no_automatic_rollback")
        success -= len(failed) * 0.05

    if params.get("canary_percent", 0) > 0:
        risk_factors.append("canary_deploy")

    success = max(0.05, min(0.99, success))

    return {
        "success_probability": success,
        "risk_factors": risk_factors,
        "estimated_downtime_seconds": 10.0,
        "expected_impact": "high" if risk_factors else "low",
    }


register_model("deterministic_infra", "deterministic", "infrastructure",
               _deterministic_infra_model,
               inputs=["scenario", "state_snapshot", "action"],
               outputs=["success_probability", "risk_factors"])

register_model("deterministic_deploy", "deterministic", "application",
               _deterministic_deploy_model,
               inputs=["scenario", "state_snapshot", "action"],
               outputs=["success_probability", "risk_factors"])
