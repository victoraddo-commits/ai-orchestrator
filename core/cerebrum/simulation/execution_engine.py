"""Simulation execution engine — runs scenarios across registered models."""
import math
import uuid
import time as _time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable, Dict, List, Optional, Tuple

from .schemas import (
    ActionProposal, WorldState, Scenario, SimulationOutcome,
    SimulationConfig, AggregatedResult,
)


def _compute_confidence_interval(values: List[float], confidence: float = 0.95) -> Tuple[float, float]:
    if len(values) <= 1:
        return (0.0, 0.0)
    mean = sum(values) / len(values)
    if len(values) < 30:
        # Use range-based approximation for small samples
        margin = max(values) - min(values)
        return (max(0.0, mean - margin * 0.5), min(1.0, mean + margin * 0.5))
    # Normal approximation
    variance = sum((v - mean) ** 2 for v in values) / (len(values) - 1)
    std_err = math.sqrt(variance / len(values))
    z = 1.96  # 95% confidence
    return (max(0.0, mean - z * std_err), min(1.0, mean + z * std_err))


def _compute_model_agreement(outcomes: List[SimulationOutcome]) -> float:
    if not outcomes:
        return 0.0
    successes = sum(1 for o in outcomes if o.success)
    return max(successes, len(outcomes) - successes) / len(outcomes)


def _derive_recommendation(success_prob: float, risk_score: float,
                           threshold: float, warnings: List[str]) -> Tuple[str, str]:
    if risk_score > threshold and success_prob < 0.40:
        return ("avoid", "risk score exceeds threshold with low success probability")
    elif risk_score > threshold:
        return ("caution", "risk score exceeds threshold")
    elif success_prob > 0.85:
        return ("proceed", "high success probability")
    elif success_prob > 0.60:
        return ("caution", "moderate success probability")
    else:
        return ("avoid", f"low success probability ({success_prob:.2f})")


class SimulationExecutionEngine:
    def __init__(self, config: Optional[SimulationConfig] = None):
        self.config = config or SimulationConfig()
        self._checkpoints: List[Dict[str, Any]] = []
        self._cancelled: bool = False

    def cancel(self):
        """Cancel the current simulation run."""
        self._cancelled = True

    def run(self, action: ActionProposal, state: WorldState,
            scenarios: List[Scenario],
            model: Callable[[ActionProposal, WorldState, Scenario], SimulationOutcome]) -> AggregatedResult:
        self._checkpoints = []
        outcomes: List[SimulationOutcome] = []

        if self.config.parallel_execution and len(scenarios) > 1:
            with ThreadPoolExecutor(max_workers=min(self.config.max_workers, len(scenarios))) as ex:
                futures = {ex.submit(_run_with_retry, model, action, state, s,
                                     self.config.max_retries): i
                           for i, s in enumerate(scenarios)}
                for future in as_completed(futures):
                    idx = futures[future]
                    try:
                        outcome = future.result(timeout=30)
                    except Exception as e:
                        outcome = SimulationOutcome(
                            scenario_name=scenarios[idx].name,
                            success=False, risk_score=1.0,
                            warnings=[f"Execution error: {e}"],
                        )
                    outcomes.append(outcome)
                    self._record_checkpoint(idx, scenarios[idx], outcome)
            # Sort by original index
            orig_order = {futures[f]: i for i, f in enumerate(futures)}
            outcomes.sort(key=lambda o: orig_order.get(
                next((i for i, s in enumerate(scenarios) if s.name == o.scenario_name), 0), 0))
        else:
            for i, scenario in enumerate(scenarios):
                if self._cancelled:
                    break
                outcome = _run_with_retry(model, action, state, scenario, self.config.max_retries)
                outcomes.append(outcome)
                self._record_checkpoint(i, scenario, outcome)

        # Compute aggregate
        success_probs = []
        risk_scores = []
        all_warnings = []
        for o in outcomes:
            if o.success:
                success_probs.append(1.0 - o.risk_score)
            else:
                success_probs.append(max(0.0, 1.0 - o.risk_score))
            risk_scores.append(o.risk_score)
            all_warnings.extend(o.warnings)

        expected_success = sum(success_probs) / len(success_probs) if success_probs else 0.0
        expected_risk = sum(risk_scores) / len(risk_scores) if risk_scores else 0.0
        model_agreement = _compute_model_agreement(outcomes)

        recommendation, explanation = _derive_recommendation(
            expected_success, expected_risk, self.config.risk_threshold, all_warnings)

        return AggregatedResult(
            scenarios_run=len(scenarios),
            outcomes=outcomes,
            expected_success_probability=round(expected_success, 2),
            expected_risk_score=round(expected_risk, 2),
            recommendation=recommendation,
            model_agreement=round(model_agreement, 2),
            explanation=explanation,
            confidence_interval_95=_compute_confidence_interval(success_probs, self.config.confidence_level),
            simulation_id=str(uuid.uuid4()),
        )

    def run_multi_model(self, action: ActionProposal, state: WorldState,
                        scenarios: List[Scenario],
                        models: List[Tuple[str, Callable[[ActionProposal, WorldState, Scenario], SimulationOutcome]]]
                        ) -> AggregatedResult:
        all_outcomes = []
        for _, model_fn in models:
            for scenario in scenarios:
                try:
                    outcome = model_fn(action, state, scenario)
                    if outcome is not None:
                        all_outcomes.append(outcome)
                except Exception:
                    pass

        success_probs = [1.0 - o.risk_score if o.success else max(0.0, 1.0 - o.risk_score)
                         for o in all_outcomes] or [0.0]
        risk_scores = [o.risk_score for o in all_outcomes] or [0.0]
        all_warnings = []
        for o in all_outcomes:
            all_warnings.extend(o.warnings)

        expected_success = sum(success_probs) / len(success_probs)
        expected_risk = sum(risk_scores) / len(risk_scores)
        model_agreement = _compute_model_agreement(all_outcomes)
        recommendation, explanation = _derive_recommendation(
            expected_success, expected_risk, self.config.risk_threshold, all_warnings)

        return AggregatedResult(
            scenarios_run=len(scenarios),
            outcomes=all_outcomes,
            expected_success_probability=round(expected_success, 2),
            expected_risk_score=round(expected_risk, 2),
            recommendation=recommendation,
            model_agreement=round(model_agreement, 2),
            explanation=explanation,
            confidence_interval_95=_compute_confidence_interval(success_probs, self.config.confidence_level),
            simulation_id=str(uuid.uuid4()),
        )

    def get_checkpoints(self) -> List[Dict[str, Any]]:
        return list(self._checkpoints)

    def _record_checkpoint(self, idx: int, scenario: Scenario, outcome: SimulationOutcome):
        if self.config.capture_checkpoints:
            self._checkpoints.append({
                "scenario_index": idx,
                "scenario_name": scenario.name,
                "outcome": outcome.to_dict(),
                "timestamp": _time.time(),
            })


def _run_with_retry(model: Callable, action: ActionProposal, state: WorldState,
                    scenario: Scenario, max_retries: int) -> SimulationOutcome:
    for attempt in range(max_retries + 1):
        try:
            return model(action, state, scenario)
        except Exception as e:
            if attempt == max_retries:
                return SimulationOutcome(
                    scenario_name=scenario.name,
                    success=False, risk_score=1.0,
                    warnings=[f"Model error: max retries exceeded after {max_retries + 1} attempts — {e}"],
                )
    # Unreachable
    return SimulationOutcome(
        scenario_name=scenario.name, success=False, risk_score=1.0, warnings=["Model failure"])


def create_execution_engine(config: Optional[SimulationConfig] = None) -> SimulationExecutionEngine:
    return SimulationExecutionEngine(config)
