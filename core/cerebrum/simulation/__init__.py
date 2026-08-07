"""Cerebrum Simulation Engine — package init."""
from .schemas import (
    ActionProposal, WorldState, Scenario, SimulationConfig,
    SimulationOutcome, AggregatedResult,
)
from .model_registry import ModelRegistry, SimulationModel, get_model_registry, reset_model_registry
from .execution_engine import SimulationExecutionEngine, create_execution_engine
from .state_manager import SimulationStateManager

__all__ = [
    "ActionProposal", "WorldState", "Scenario", "SimulationConfig",
    "SimulationOutcome", "AggregatedResult",
    "ModelRegistry", "SimulationModel", "get_model_registry", "reset_model_registry",
    "SimulationExecutionEngine", "create_execution_engine",
    "SimulationStateManager",
]

# ── Top-level simulation API (used by test_cerebrum.py) ──

import uuid
from typing import Optional
from .. import scenarios as scenario_lib
from .. import risk_analyzer
from ..models import get_model
from .execution_engine import SimulationExecutionEngine, SimulationConfig
from .schemas import ActionProposal, WorldState

_simulation_store: dict = {}


def run_simulation(action_type: str, params: dict, proposed_by: str = "kai",
                   trace_id: str = None) -> dict:
    """Run a full simulation for an action type with the given parameters."""
    from ..action_schema import get_schema, validate_action
    from ..models import _deterministic_infra_model as default_model

    schema = get_schema(action_type)
    if schema is None:
        raise ValueError(f"unknown action_type: {action_type}")

    validate_action(action_type, params)

    action = {
        "action_type": action_type,
        "parameters": params,
        "target": params.get("service_name") or params.get("target_service") or params.get(
            "target") or params.get("application_name", ""),
        "domain": schema.get("domain", "infrastructure"),
        "proposed_by": proposed_by,
        "trace_id": trace_id or str(uuid.uuid4()),
    }

    scenarios = scenario_lib.generate_scenarios(action)
    world_state = WorldState()

    # Build scenario results using default model
    scenario_results = []
    state_dict = {"entity_state": {}, "learning": [], "builds": []}
    for sc in scenarios:
        outcome = default_model(sc, state_dict, action)
        scenario_results.append({
            "scenario": {"type": sc["type"], "label": sc["label"]},
            "outcome": outcome,
        })

    aggregated = risk_analyzer.aggregate_results(scenario_results)

    baseline_comparison = risk_analyzer.compare_to_baseline(aggregated, {
        "mean_success_probability": 1.0,
        "risk_level": "none",
        "all_risk_factors": [],
    })

    sim_id = str(uuid.uuid4())
    sim = {
        "id": sim_id,
        "status": "completed",
        "action": action,
        "aggregated_results": aggregated,
        "recommendation": aggregated.get("recommendation", {}).get("action", "caution"),
        "baseline_comparison": baseline_comparison,
    }
    _simulation_store[sim_id] = sim
    return sim


def simulate_before_action(action: dict) -> dict:
    """Simulate before executing an action."""
    at = action.get("action_type", "")
    params = action.get("parameters", {})
    proposed_by = action.get("proposed_by", "kai")
    trace_id = action.get("trace_id")
    return run_simulation(at, params, proposed_by=proposed_by, trace_id=trace_id)


def record_actual_outcome(simulation_id: str, actual_success: Optional[bool] = None,
                          actual_downtime_seconds: float = 0, notes: str = "") -> dict:
    """Record the actual outcome of a simulated action."""
    sim = _simulation_store.get(simulation_id)
    if sim is None:
        raise ValueError(f"simulation {simulation_id} not found")

    actual = {"success": actual_success, "downtime_seconds": actual_downtime_seconds}
    prediction = sim.get("aggregated_results", {})

    pred_sp = prediction.get("mean_success_probability", 0.5)
    actual_val = 1.0 if actual_success else 0.0
    prediction_error = abs(pred_sp - actual_val)

    return {
        "simulation_id": simulation_id,
        "prediction_error": prediction_error,
        "prediction": prediction,
        "actual": actual,
        "notes": notes,
        "action": sim.get("action", {}),
    }


def list_simulations() -> list:
    """List all simulations."""
    return list(_simulation_store.values())


def get_simulation(simulation_id: str) -> Optional[dict]:
    """Get a specific simulation by ID."""
    return _simulation_store.get(simulation_id)
