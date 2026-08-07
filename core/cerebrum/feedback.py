"""Feedback loop for Cerebrum Simulation Engine — records outcomes and recalibrates models."""
from typing import Any, Dict, List, Optional

_feedback_store: List[Dict[str, Any]] = []


def record_outcome(simulation_id: str, action: Dict[str, Any],
                   prediction: Optional[Dict[str, Any]] = None,
                   actual: Optional[Dict[str, Any]] = None,
                   notes: str = "") -> Dict[str, Any]:
    prediction_error = None
    if prediction and actual:
        pred_sp = prediction.get("mean_success_probability", 0.5)
        actual_success = actual.get("success", True)
        actual_val = 1.0 if actual_success else 0.0
        prediction_error = abs(pred_sp - actual_val)

    record = {
        "simulation_id": simulation_id,
        "action": dict(action),
        "prediction": dict(prediction) if prediction else {},
        "actual": dict(actual) if actual else {},
        "prediction_error": prediction_error,
        "notes": notes,
    }

    _feedback_store.append(record)
    return record


def get_feedback_for_action(action_type: str) -> List[Dict[str, Any]]:
    return [f for f in _feedback_store if f.get("action", {}).get("action_type") == action_type]


def recalibrate() -> Dict[str, Any]:
    if not _feedback_store:
        return {}

    by_action: Dict[str, List[Dict[str, Any]]] = {}
    for f in _feedback_store:
        at = f.get("action", {}).get("action_type", "unknown")
        if at not in by_action:
            by_action[at] = []
        by_action[at].append(f)

    result = {}
    for at, records in by_action.items():
        errors = [r["prediction_error"] for r in records if r["prediction_error"] is not None]
        result[at] = {
            "sample_count": len(records),
            "mean_prediction_error": round(sum(errors) / len(errors), 4) if errors else None,
        }

    return result


def reset_feedback_store():
    """Clear all feedback records (used in test isolation)."""
    _feedback_store.clear()
