"""Data schemas for Cerebrum Simulation Engine."""
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
import uuid


@dataclass
class ActionProposal:
    action_type: str
    description: str = ""
    domain: str = "infrastructure"
    parameters: Dict[str, Any] = field(default_factory=dict)
    target_entity: Optional[str] = None
    proposed_by: str = "kai"
    trace_id: Optional[str] = None
    id: str = field(default_factory=lambda: str(uuid.uuid4()))


@dataclass
class WorldState:
    entities: Dict[str, Any] = field(default_factory=dict)
    environment: Dict[str, Any] = field(default_factory=dict)
    metrics: Dict[str, Any] = field(default_factory=dict)
    captured_at: Optional[str] = None


@dataclass
class Scenario:
    name: str
    description: str
    scenario_type: str = "base"
    parameters: Dict[str, Any] = field(default_factory=dict)
    probability_weight: float = 0.25


@dataclass
class SimulationOutcome:
    scenario_name: str = "unknown"
    success: bool = True
    risk_score: float = 0.0
    cost_estimate: float = 0.0
    time_estimate_seconds: float = 0.0
    affected_entities: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    failure_probability: Optional[float] = None
    model_name: Optional[str] = None

    def to_dict(self):
        return {
            "scenario_name": self.scenario_name, "success": self.success,
            "risk_score": self.risk_score, "cost_estimate": self.cost_estimate,
            "time_estimate_seconds": self.time_estimate_seconds,
            "affected_entities": self.affected_entities, "warnings": self.warnings,
        }


@dataclass
class SimulationConfig:
    parallel_execution: bool = True
    max_workers: int = 4
    risk_threshold: float = 0.3
    confidence_level: float = 0.95
    timeout_per_model_seconds: int = 30
    cancel_on_high_risk: bool = False
    capture_checkpoints: bool = True
    mode: str = "parallel"
    max_retries: int = 2
    models: List[str] = field(default_factory=list)


@dataclass
class AggregatedResult:
    scenarios_run: int = 0
    outcomes: List[SimulationOutcome] = field(default_factory=list)
    expected_success_probability: float = 0.0
    expected_risk_score: float = 0.0
    recommendation: str = "caution"
    model_agreement: float = 0.0
    explanation: str = ""
    confidence_interval_95: Optional[tuple] = None
    simulation_id: Optional[str] = None
    captured_at: Optional[str] = None
    checkpoints: List[Dict[str, Any]] = field(default_factory=list)
    cancelled: bool = False

    def to_dict(self):
        return {
            "scenarios_run": self.scenarios_run,
            "outcomes": [o.to_dict() for o in self.outcomes],
            "expected_success_probability": self.expected_success_probability,
            "expected_risk_score": self.expected_risk_score,
            "recommendation": self.recommendation,
            "model_agreement": self.model_agreement,
            "explanation": self.explanation,
            "simulation_id": self.simulation_id,
            "cancelled": self.cancelled,
        }
