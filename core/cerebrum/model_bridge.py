"""Model bridge — bridges legacy model functions into the pluggable ModelRegistry."""
from typing import List

from .simulation.model_registry import ModelRegistry, SimulationModel, get_model_registry
from .simulation.schemas import SimulationOutcome
from . import models as legacy_models


def bridge_legacy_models(registry=None) -> List[str]:
    registry = registry or get_model_registry()

    def make_infra_runner(fn):
        def run_fn(action, state, scenario):
            result = fn(
                {"type": scenario.scenario_type, "label": scenario.scenario_type, "id": scenario.name},
                state.entities if hasattr(state, "entities") else (state if isinstance(state, dict) else state.__dict__),
                {"action_type": action.action_type, "parameters": action.parameters,
                 "target": action.target_entity or action.parameters.get("service_name", "")},
            )
            return SimulationOutcome(
                scenario_name=scenario.name,
                success=result["success_probability"] > 0.5,
                risk_score=1.0 - result["success_probability"],
                cost_estimate=result.get("estimated_downtime_seconds", 10) * 0.5,
                time_estimate_seconds=result.get("estimated_downtime_seconds", 10),
                warnings=result.get("risk_factors", []),
            )
        return run_fn

    def make_deploy_runner(fn):
        def run_fn(action, state, scenario):
            state_dict = {}
            if hasattr(state, "entities"):
                state_dict = state.entities if isinstance(state.entities, dict) else {}
            elif isinstance(state, dict):
                state_dict = state
            result = fn(
                {"type": scenario.scenario_type, "label": scenario.scenario_type, "id": scenario.name},
                {"builds": state_dict.get("builds", [])},
                {"action_type": action.action_type, "parameters": action.parameters,
                 "target": action.target_entity or action.parameters.get("application_name", "")},
            )
            return SimulationOutcome(
                scenario_name=scenario.name,
                success=result["success_probability"] > 0.5,
                risk_score=1.0 - result["success_probability"],
                cost_estimate=result.get("estimated_downtime_seconds", 10) * 0.5,
                time_estimate_seconds=result.get("estimated_downtime_seconds", 10),
                warnings=result.get("risk_factors", []),
            )
        return run_fn

    infra_fn = legacy_models.get_model_fn("deterministic_infra")
    deploy_fn = legacy_models.get_model_fn("deterministic_deploy")

    registered = []

    if infra_fn:
        model = SimulationModel(
            name="deterministic_infra",
            model_type="deterministic",
            description="Deterministic infrastructure risk model",
            run_fn=make_infra_runner(infra_fn),
            domain="infrastructure",
            domain_specific=True,
        )
        registry.register(model)
        registered.append("deterministic_infra")

    if deploy_fn:
        model = SimulationModel(
            name="deterministic_deploy",
            model_type="deterministic",
            description="Deterministic deployment risk model",
            run_fn=make_deploy_runner(deploy_fn),
            domain="application",
            domain_specific=True,
            calibrated=True,
        )
        registry.register(model)
        registered.append("deterministic_deploy")

    return registered
