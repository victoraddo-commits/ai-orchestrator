"""Explanation layer for Cerebrum Simulation Engine."""
from typing import Any, Dict, List, Optional

_RISK_DRIVER_CONFIG = {
    "breaking_change_possible": {"description": "Breaking change risk", "severity": "medium",
                                  "mitigation": "Test compatibility before deploying"},
    "no_compatibility_check": {"description": "No compatibility check available", "severity": "medium",
                                "mitigation": "Add compatibility testing"},
    "complete_isolation_risk": {"description": "Complete network isolation risk", "severity": "critical",
                                 "mitigation": "Ensure fallback connectivity paths"},
    "memory_allocation_oom_risk": {"description": "Out-of-memory risk from allocation", "severity": "high",
                                    "mitigation": "Gradually increase allocation"},
    "high_downtime_risk": {"description": "High downtime risk", "severity": "high",
                            "mitigation": "Schedule during maintenance window"},
    "service_has_recent_incidents": {"description": "Service has recent incidents", "severity": "medium",
                                       "mitigation": "Investigate before proceeding"},
    "config_drift_risk": {"description": "Configuration drift risk", "severity": "low",
                           "mitigation": "Back up current config before change"},
    "no_automatic_rollback": {"description": "No automatic rollback configured", "severity": "medium",
                               "mitigation": "Set up automatic rollback"},
    "unknown_action_type": {"description": "Unknown or novel action type", "severity": "low",
                             "mitigation": "Proceed with extra monitoring"},
    "minor": {"description": "Minor risk factor", "severity": "low",
              "mitigation": "Standard monitoring"},
    "canary_deploy": {"description": "Canary deployment active", "severity": "low",
                      "mitigation": "Monitor canary before full rollout"},
}


def generate_summary(aggregated: Dict[str, Any]) -> str:
    rec = aggregated.get("recommendation", {}).get("action", "unknown")
    success = aggregated.get("mean_success_probability", 0)

    if rec == "proceed":
        action = "recommends proceeding"
    elif rec == "avoid_or_prepare":
        action = "advises against proceeding"
    elif rec == "caution":
        action = "recommends caution"
    else:
        action = f"recommends {rec}"

    pct = f"{int(success * 100)}%"
    return f"Simulation {action} with {pct} success probability"


def generate_risk_driver_breakdown(aggregated: Dict[str, Any]) -> List[Dict[str, Any]]:
    drivers = []
    all_factors = aggregated.get("all_risk_factors", [])

    for factor in all_factors:
        config = _RISK_DRIVER_CONFIG.get(factor, {
            "description": factor.replace("_", " ").title(),
            "severity": "low",
            "mitigation": "Review and monitor",
        })
        drivers.append({
            "driver": factor,
            "description": config["description"],
            "severity": config["severity"],
            "scenarios_affected": [],
            "mitigation": config["mitigation"],
        })

    return drivers


def generate_trade_off_matrix(aggregated: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "success_probability": aggregated.get("mean_success_probability", 0.0),
        "risk_level": aggregated.get("risk_level", "unknown"),
        "downtime_seconds_max": aggregated.get("estimated_downtime_max_seconds", 0),
        "confidence_interval_95": aggregated.get("confidence_interval_95", (0.0, 0.0)),
        "rollback_complexity": "low",
        "recommendation": aggregated.get("recommendation", {}).get("action", "unknown"),
    }


def generate_model_disagreement_note(aggregated: Dict[str, Any],
                                     model_agreement: Optional[float] = None) -> Optional[str]:
    ci = aggregated.get("confidence_interval_95", (0.0, 0.0))
    ci_range = ci[1] - ci[0] if ci else 0

    if model_agreement is not None and model_agreement < 0.6:
        return f"Low model agreement ({model_agreement:.2f}) indicates disagreement"

    if ci_range > 0.5:
        return f"High model disagreement (CI range: {ci_range:.2f})"

    if model_agreement is not None and model_agreement < 0.6:
        return "Model disagreement detected"

    return None
