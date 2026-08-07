"""Risk analysis and aggregation for Cerebrum Simulation Engine."""
from typing import Any, Dict, List


def aggregate_results(scenario_results: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not scenario_results:
        return {
            "mean_success_probability": 0.0,
            "risk_level": "unknown",
            "per_scenario": [],
            "all_risk_factors": [],
            "worst_case_probability": 0.0,
            "best_case_probability": 0.0,
            "estimated_downtime_max_seconds": 0,
            "recommendation": {"action": "unknown", "confidence": "unknown"},
        }

    success_values = []
    downtimes = []
    all_risk_factors = []
    per_scenario = []

    for sr in scenario_results:
        outcome = sr.get("outcome", {})
        sp = outcome.get("success_probability", 0.5)
        success_values.append(sp)
        downtimes.append(outcome.get("estimated_downtime_seconds", 0))
        all_risk_factors.extend(outcome.get("risk_factors", []))
        per_scenario.append(dict(sr))

    mean_sp = sum(success_values) / len(success_values)
    worst = min(success_values)
    best = max(success_values)
    max_downtime = max(downtimes) if downtimes else 0

    unique_risks = list(dict.fromkeys(all_risk_factors))
    risk_count = len(unique_risks)

    risk_level = _classify_risk(mean_sp, risk_count / max(len(scenario_results), 1), unique_risks)

    if risk_level == "low":
        recommendation = {"action": "proceed", "confidence": "high"}
    elif risk_level == "medium":
        recommendation = {"action": "caution", "confidence": "medium"}
    elif risk_level == "high":
        recommendation = {"action": "avoid_or_prepare", "confidence": "medium"}
    else:
        recommendation = {"action": "avoid_or_prepare", "confidence": "low"}

    return {
        "mean_success_probability": round(mean_sp, 2),
        "risk_level": risk_level,
        "per_scenario": per_scenario,
        "all_risk_factors": unique_risks,
        "worst_case_probability": round(worst, 2),
        "best_case_probability": round(best, 2),
        "estimated_downtime_max_seconds": max_downtime,
        "recommendation": recommendation,
        "confidence_interval_95": (max(0, mean_sp - 0.15), min(1, mean_sp + 0.15)),
    }


def compare_to_baseline(aggregated: Dict[str, Any], baseline: Dict[str, Any]) -> Dict[str, Any]:
    delta = aggregated.get("mean_success_probability", 0) - baseline.get("mean_success_probability", 0)

    if aggregated.get("risk_level") in ("high", "critical") and baseline.get("risk_level") not in ("high", "critical"):
        verdict = "high_risk_vs_baseline"
    elif delta > 0.0:
        verdict = "beneficial_vs_baseline"
    elif delta < -0.2:
        verdict = "high_risk_vs_baseline"
    else:
        verdict = "comparable_to_baseline"

    return {
        "verdict": verdict,
        "delta_vs_baseline": round(delta, 4),
        "baseline_success": baseline.get("mean_success_probability", 1.0),
        "aggregated_success": aggregated.get("mean_success_probability", 0.0),
    }


def _classify_risk(success_probability: float, risk_density: float,
                   risk_factors: List[str]) -> str:
    if len(risk_factors) >= 4 and success_probability < 0.40:
        return "high"
    if len(risk_factors) >= 3:
        return "medium"
    if success_probability > 0.85:
        return "low"
    if success_probability > 0.60:
        return "medium"
    return "high"
