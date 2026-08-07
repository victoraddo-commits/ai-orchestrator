"""Model registry for the Cerebrum Simulation Engine."""
from typing import Any, Callable, Dict, List, Optional

from .schemas import ActionProposal, WorldState, Scenario, SimulationOutcome


class SimulationModel:
    def __init__(self, name: str, model_type: str = "deterministic", description: str = "",
                 run_fn: Optional[Callable] = None,
                 version: str = "1.0.0", domain: str = "infrastructure",
                 domain_specific: bool = True,
                 assumptions: Optional[List[str]] = None,
                 limitations: Optional[List[str]] = None,
                 calibrated: bool = False,
                 inputs: Optional[List[str]] = None,
                 outputs: Optional[List[str]] = None,
                 metadata: Optional[Dict[str, Any]] = None):
        self.name = name
        self.model_type = model_type
        self.description = description
        self._run_fn = run_fn
        self.version = version
        self.domain = domain
        self.domain_specific = domain_specific
        self.assumptions = assumptions or []
        self.limitations = limitations or []
        self.calibrated = calibrated
        self.is_enabled = True
        self.model_id = name
        self.inputs = inputs or ["scenario", "state_snapshot", "action"]
        self.outputs = outputs or ["success_probability"]
        self.metadata = metadata or {}
        self.config: Dict[str, Any] = {}

    def run(self, action: ActionProposal, state: WorldState,
            scenario: Scenario) -> SimulationOutcome:
        if self._run_fn is None:
            return SimulationOutcome(
                scenario_name=scenario.name,
                success=False, risk_score=1.0,
                warnings=["No run function configured"],
            )
        return self._run_fn(action, state, scenario)

    def simulate(self, action: Any, state: Any, scenario: Any) -> SimulationOutcome:
        return self.run(action, state, scenario)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name, "type": self.model_type, "description": self.description,
            "version": self.version, "domain": self.domain,
            "domain_specific": self.domain_specific,
            "assumptions": self.assumptions, "limitations": self.limitations,
            "calibrated": self.calibrated, "is_enabled": self.is_enabled,
            "inputs": self.inputs, "outputs": self.outputs,
        }


class ModelRegistry:
    def __init__(self):
        self._models: Dict[str, SimulationModel] = {}

    def register(self, model: SimulationModel):
        self._models[model.name] = model

    def get(self, name: str) -> Optional[SimulationModel]:
        return self._models.get(name)

    def unregister(self, name: str):
        self._models.pop(name, None)

    def list(self) -> List[str]:
        return sorted(self._models.keys())

    def list_models(self) -> List[Dict[str, Any]]:
        return [m.to_dict() for m in self._models.values()]

    def get_models_for_domain(self, domain: str) -> List[SimulationModel]:
        return [m for m in self._models.values()
                if not m.domain_specific or m.domain == domain]

    def get_enabled_models(self, names: List[str]) -> List[SimulationModel]:
        return [self._models[n] for n in names if n in self._models and self._models[n].is_enabled]

    def run_all(self, action: ActionProposal, state: WorldState,
                scenario: Scenario) -> List[SimulationOutcome]:
        outcomes = []
        for model in self._models.values():
            try:
                outcome = model.run(action, state, scenario)
                outcomes.append(outcome)
            except Exception as e:
                outcomes.append(SimulationOutcome(
                    scenario_name=scenario.name,
                    success=False, risk_score=1.0,
                    warnings=[f"{model.name}: {e}"],
                ))
        return outcomes

    def set_config(self, config: Dict[str, Any]):
        pass

    def __len__(self) -> int:
        return len(self._models)

    def __contains__(self, name: str) -> bool:
        return name in self._models


_global_registry: Optional[ModelRegistry] = None


def get_model_registry() -> ModelRegistry:
    global _global_registry
    if _global_registry is None:
        _global_registry = ModelRegistry()
    return _global_registry


def reset_model_registry():
    global _global_registry
    _global_registry = ModelRegistry()
