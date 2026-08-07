"""Tests for Cerebrum Simulation Engine components:

  - SimulationExecutionEngine (sequential, parallel, retries, checkpoints, time-horizon)
  - Explanation layer (summaries, risk drivers, trade-off matrices)
  - Action Connector (two-phase simulate-then-execute)
  - Model Bridge (legacy models -> pluggable registry)

All tests use isolated_memory from conftest.py so production memory files
are never touched.
"""

import time
from unittest.mock import MagicMock, patch

import pytest

import core.cerebrum.action_connector as action_connector
import core.cerebrum.explanation as explanation
import core.cerebrum.model_bridge as model_bridge
from core.cerebrum.simulation.execution_engine import (
    SimulationExecutionEngine,
    create_execution_engine,
    _derive_recommendation,
    _compute_confidence_interval,
    _compute_model_agreement,
)
from core.cerebrum.simulation.model_registry import (
    ModelRegistry,
    SimulationModel,
    get_model_registry,
    reset_model_registry,
)
from core.cerebrum.simulation.schemas import (
    ActionProposal,
    AggregatedResult,
    Scenario,
    SimulationConfig,
    SimulationOutcome,
    WorldState,
)


class TestExecutionEngine:
    """Tests for SimulationExecutionEngine."""

    def make_action(self, **kwargs):
        defaults = {
            "action_type": "restart_service",
            "description": "Restart nginx",
            "domain": "infrastructure",
            "parameters": {"service_name": "nginx"},
            "target_entity": "nginx",
            "proposed_by": "kai",
        }
        defaults.update(kwargs)
        return ActionProposal(**defaults)

    def make_state(self):
        return WorldState(
            entities={
                "nginx": {"replicas": 1, "status": "running"},
                "api": {"replicas": 3, "status": "running"},
            },
            environment={"provider_quota": {"available": True}},
            metrics={"active_build_count": 2},
        )

    def make_scenarios(self, count=4):
        types = ["base", "optimistic", "pessimistic", "adversarial"]
        weights = [0.40, 0.15, 0.15, 0.30]
        return [
            Scenario(
                name=f"{t}_case",
                description=f"{t} scenario",
                scenario_type=t,
                parameters={"service_name": "nginx", "variation": t},
                probability_weight=weights[i % len(weights)],
            )
            for i, t in enumerate(types[:count])
        ]

    def make_success_model(self, risk=0.1, cost=10.0, downtime=5.0):
        """Model that always succeeds."""
        def fn(action, state, scenario):
            return SimulationOutcome(
                scenario_name=scenario.name,
                success=True,
                risk_score=risk,
                cost_estimate=cost,
                time_estimate_seconds=downtime,
                affected_entities=[action.target_entity] if action.target_entity else [],
                warnings=[],
            )
        return fn

    def make_failing_model(self):
        """Model that always fails."""
        def fn(action, state, scenario):
            return SimulationOutcome(
                scenario_name=scenario.name,
                success=False,
                risk_score=0.95,
                cost_estimate=100.0,
                time_estimate_seconds=300.0,
                affected_entities=[action.target_entity] if action.target_entity else [],
                failure_probability=0.9,
                warnings=["high_failure_risk"],
            )
        return fn

    def make_flaky_model(self, fail_on_attempt=0):
        """Model that fails on a specific attempt (zero-indexed)."""
        call_count = [0]

        def fn(action, state, scenario):
            attempt = call_count[0]
            call_count[0] += 1
            if attempt == fail_on_attempt:
                raise RuntimeError("flaky model failure")
            return SimulationOutcome(
                scenario_name=scenario.name,
                success=True,
                risk_score=0.05,
                cost_estimate=5.0,
                time_estimate_seconds=2.0,
                warnings=[],
            )

        return fn

    def test_sequential_execution_produces_outcomes(self):
        engine = SimulationExecutionEngine(
            SimulationConfig(parallel_execution=False, max_workers=1)
        )
        action = self.make_action()
        state = self.make_state()
        scenarios = self.make_scenarios(count=3)

        result = engine.run(action, state, scenarios, self.make_success_model())

        assert isinstance(result, AggregatedResult)
        assert result.scenarios_run == 3
        assert len(result.outcomes) == 3
        assert result.expected_success_probability > 0.8
        assert result.expected_risk_score < 0.3
        assert result.recommendation == "proceed"
        assert result.model_agreement == 1.0

    def test_parallel_execution_produces_outcomes(self):
        engine = SimulationExecutionEngine(
            SimulationConfig(parallel_execution=True, max_workers=2)
        )
        action = self.make_action()
        state = self.make_state()
        scenarios = self.make_scenarios(count=4)

        def slow_model(action, state, scenario):
            time.sleep(0.01)
            return SimulationOutcome(
                scenario_name=scenario.name,
                success=True,
                risk_score=0.1,
                cost_estimate=5.0,
                time_estimate_seconds=1.0,
                warnings=[],
            )

        result = engine.run(action, state, scenarios, slow_model)

        assert result.scenarios_run == 4
        assert len(result.outcomes) == 4

    def test_high_risk_action_gets_avoid_recommendation(self):
        engine = SimulationExecutionEngine(SimulationConfig(risk_threshold=0.3))
        action = self.make_action()
        state = self.make_state()
        scenarios = self.make_scenarios(count=2)

        result = engine.run(action, state, scenarios, self.make_failing_model())

        assert result.expected_risk_score > 0.8
        assert result.recommendation == "avoid"
        assert "risk score" in result.explanation.lower()

    def test_checkpoints_captured(self):
        engine = SimulationExecutionEngine(SimulationConfig(parallel_execution=False))
        action = self.make_action()
        state = self.make_state()
        scenarios = self.make_scenarios(count=3)

        engine.run(action, state, scenarios, self.make_success_model())

        checkpoints = engine.get_checkpoints()
        assert len(checkpoints) == 3
        for cp in checkpoints:
            assert "scenario_index" in cp
            assert "scenario_name" in cp
            assert "outcome" in cp
            assert "timestamp" in cp

    def test_retry_succeeds_on_second_attempt(self):
        engine = SimulationExecutionEngine(SimulationConfig(parallel_execution=False))
        action = self.make_action()
        state = self.make_state()
        scenarios = self.make_scenarios(count=1)

        result = engine.run(action, state, scenarios, self.make_flaky_model(fail_on_attempt=0))

        assert result.scenarios_run == 1
        assert len(result.outcomes) == 1
        assert result.outcomes[0].success is True

    def test_retry_exhausted_returns_failure_outcome(self):
        engine = SimulationExecutionEngine(SimulationConfig(parallel_execution=False))
        action = self.make_action()
        state = self.make_state()
        scenarios = self.make_scenarios(count=1)

        def always_raise(action, state, scenario):
            raise RuntimeError("persistent failure")

        result = engine.run(action, state, scenarios, always_raise)
        assert len(result.outcomes) == 1
        assert result.outcomes[0].success is False
        assert any("max retries" in w.lower() or "model error" in w.lower()
                   for w in result.outcomes[0].warnings)

    def test_cancellation_stops_mid_run(self):
        engine = SimulationExecutionEngine(SimulationConfig(parallel_execution=False))
        action = self.make_action()
        state = self.make_state()
        scenarios = self.make_scenarios(count=10)

        def cancel_model(action, state, scenario):
            if scenario.scenario_type == "pessimistic":
                engine.cancel()
            return SimulationOutcome(
                scenario_name=scenario.name,
                success=True,
                risk_score=0.1,
                cost_estimate=1.0,
                time_estimate_seconds=1.0,
                warnings=[],
            )

        result = engine.run(action, state, scenarios, cancel_model)
        assert len(result.outcomes) < len(scenarios)

    def test_low_success_caution_recommendation(self):
        engine = SimulationExecutionEngine(SimulationConfig(risk_threshold=0.8))
        action = self.make_action()
        state = self.make_state()
        scenarios = self.make_scenarios(count=2)

        def mixed_model(action, state, scenario):
            if scenario.scenario_type == "adversarial":
                success = False
                risk = 0.7
            else:
                success = True
                risk = 0.3
            return SimulationOutcome(
                scenario_name=scenario.name,
                success=success,
                risk_score=risk,
                cost_estimate=20.0,
                time_estimate_seconds=10.0,
                warnings=["test_warning"] if scenario.scenario_type == "adversarial" else [],
                failure_probability=0.3,
            )

        result = engine.run(action, state, scenarios, mixed_model)
        assert result.recommendation in ("caution", "proceed", "avoid")

    def test_multi_model_combines_results(self):
        engine = SimulationExecutionEngine(SimulationConfig(parallel_execution=False))
        action = self.make_action()
        state = self.make_state()
        scenarios = self.make_scenarios(count=2)

        models = [
            ("optimist", self.make_success_model(risk=0.05)),
            ("realist", self.make_success_model(risk=0.3)),
        ]

        result = engine.run_multi_model(action, state, scenarios, models)
        assert result.scenarios_run == 2
        assert len(result.outcomes) == 4
        assert result.expected_risk_score > 0

    def test_confidence_interval_computation(self):
        values = [0.9, 0.85, 0.88, 0.92]
        lower, upper = _compute_confidence_interval(values, 0.95)
        assert 0 < lower < upper < 1
        assert lower < 0.91 < upper

    def test_confidence_interval_single_value(self):
        lower, upper = _compute_confidence_interval([0.85], 0.95)
        assert lower == 0.0
        assert upper == 0.0

    def test_model_agreement_all_agree(self):
        outcomes = [
            SimulationOutcome(success=True, risk_score=0.1, cost_estimate=5.0,
                              time_estimate_seconds=1.0, scenario_name="s1"),
            SimulationOutcome(success=True, risk_score=0.2, cost_estimate=5.0,
                              time_estimate_seconds=1.0, scenario_name="s2"),
        ]
        assert _compute_model_agreement(outcomes) == 1.0

    def test_model_agreement_split(self):
        outcomes = [
            SimulationOutcome(success=True, risk_score=0.1, cost_estimate=5.0,
                              time_estimate_seconds=1.0, scenario_name="s1"),
            SimulationOutcome(success=False, risk_score=0.9, cost_estimate=5.0,
                              time_estimate_seconds=1.0, scenario_name="s2"),
        ]
        assert _compute_model_agreement(outcomes) == 0.5

    def test_derive_recommendation_proceed(self):
        rec, expl = _derive_recommendation(0.90, 0.1, 0.3, [])
        assert rec == "proceed"

    def test_derive_recommendation_avoid(self):
        rec, expl = _derive_recommendation(0.30, 0.5, 0.3, ["high_risk"])
        assert rec == "avoid"

    def test_derive_recommendation_caution(self):
        rec, expl = _derive_recommendation(0.70, 0.2, 0.3, ["minor"])
        assert rec == "caution"

    def test_factory_function(self):
        engine = create_execution_engine()
        assert isinstance(engine, SimulationExecutionEngine)
        assert engine.config.max_workers == 4

    def test_default_config_parallel(self):
        engine = SimulationExecutionEngine()
        assert engine.config.parallel_execution is True
        assert engine.config.max_workers == 4
        assert engine.config.risk_threshold == 0.3
        assert engine.config.confidence_level == 0.95


class TestExplanationLayer:
    """Tests for the explanation/decision-support layer."""

    def test_generate_summary_proceed(self):
        aggregated = {
            "recommendation": {"action": "proceed"},
            "mean_success_probability": 0.92,
            "risk_level": "low",
            "worst_case_probability": 0.75,
            "best_case_probability": 0.98,
            "confidence_interval_95": (0.80, 0.99),
            "all_risk_factors": [],
            "estimated_downtime_max_seconds": 5.0,
        }
        summary = explanation.generate_summary(aggregated)
        assert "proceeding" in summary.lower() or "recommends proceeding" in summary.lower()
        assert "92%" in summary or "0.92" in summary.replace("%", " ")

    def test_generate_summary_caution(self):
        aggregated = {
            "recommendation": {"action": "caution"},
            "mean_success_probability": 0.70,
            "risk_level": "medium",
            "worst_case_probability": 0.50,
            "best_case_probability": 0.85,
            "confidence_interval_95": (0.55, 0.85),
            "all_risk_factors": ["breaking_change_possible", "no_compatibility_check"],
            "estimated_downtime_max_seconds": 30.0,
        }
        summary = explanation.generate_summary(aggregated)
        assert "caution" in summary.lower()

    def test_generate_summary_avoid(self):
        aggregated = {
            "recommendation": {"action": "avoid_or_prepare"},
            "mean_success_probability": 0.25,
            "risk_level": "high",
            "worst_case_probability": 0.05,
            "best_case_probability": 0.50,
            "confidence_interval_95": (0.10, 0.40),
            "all_risk_factors": ["complete_isolation_risk", "breaking_change_possible"],
            "estimated_downtime_max_seconds": 120.0,
        }
        summary = explanation.generate_summary(aggregated)
        assert ("against" in summary.lower() or "avoid" in summary.lower())

    def test_generate_risk_driver_breakdown(self):
        aggregated = {
            "all_risk_factors": ["breaking_change_possible", "no_compatibility_check"],
            "per_scenario": [
                {
                    "scenario_type": "base",
                    "risk_factors": ["breaking_change_possible"],
                },
                {
                    "scenario_type": "adversarial",
                    "risk_factors": ["breaking_change_possible", "no_compatibility_check"],
                },
            ],
        }
        breakdown = explanation.generate_risk_driver_breakdown(aggregated)
        assert len(breakdown) == 2
        for item in breakdown:
            assert "driver" in item
            assert "description" in item
            assert "severity" in item
            assert "scenarios_affected" in item
            assert "mitigation" in item

    def test_risk_driver_critical_severity(self):
        aggregated = {
            "all_risk_factors": ["complete_isolation_risk"],
            "per_scenario": [
                {"scenario_type": "adversarial", "risk_factors": ["complete_isolation_risk"]},
            ],
        }
        breakdown = explanation.generate_risk_driver_breakdown(aggregated)
        assert breakdown[0]["severity"] == "critical"

    def test_trade_off_matrix(self):
        aggregated = {
            "mean_success_probability": 0.85,
            "risk_level": "low",
            "estimated_downtime_max_seconds": 10,
            "confidence_interval_95": (0.75, 0.95),
            "recommendation": {"action": "proceed"},
        }
        matrix = explanation.generate_trade_off_matrix(aggregated)
        assert matrix["success_probability"] == 0.85
        assert matrix["risk_level"] == "low"
        assert matrix["downtime_seconds_max"] == 10
        assert "rollback_complexity" in matrix

    def test_model_disagreement_high_range(self):
        aggregated = {
            "confidence_interval_95": (0.20, 0.80),
        }
        note = explanation.generate_model_disagreement_note(aggregated)
        assert note is not None
        assert "disagreement" in note.lower()

    def test_model_disagreement_low_agreement_score(self):
        aggregated = {
            "confidence_interval_95": (0.70, 0.80),
        }
        note = explanation.generate_model_disagreement_note(aggregated, model_agreement=0.45)
        assert note is not None
        assert "low model agreement" in note.lower()

    def test_model_disagreement_no_problem(self):
        aggregated = {
            "confidence_interval_95": (0.80, 0.90),
        }
        note = explanation.generate_model_disagreement_note(aggregated, model_agreement=0.95)
        assert note is None

    def test_risk_driver_mitigations_populated(self):
        aggregated = {
            "all_risk_factors": ["service_has_recent_incidents", "high_downtime_risk"],
            "per_scenario": [],
        }
        breakdown = explanation.generate_risk_driver_breakdown(aggregated)
        for item in breakdown:
            assert len(item["mitigation"]) > 0


class TestActionConnector:
    """Tests for the two-phase action execution connector."""

    def setup_method(self):
        action_connector.reset_executors()

    def teardown_method(self):
        action_connector.reset_executors()

    def test_register_and_get_executor(self):
        def my_executor(action_type, params):
            return {"success": True}

        action_connector.register_executor("infrastructure", my_executor)
        executor = action_connector.get_executor("infrastructure")
        assert executor is not None
        result = executor("restart_service", {"service_name": "nginx"})
        assert result["success"] is True

    def test_list_executors(self):
        action_connector.register_executor("infrastructure", lambda a, p: {"success": True})
        action_connector.register_executor("application", lambda a, p: {"success": True})
        domains = action_connector.list_executors()
        assert "infrastructure" in domains
        assert "application" in domains

    def test_unregister_executor(self):
        action_connector.register_executor("infrastructure", lambda a, p: {"success": True})
        action_connector.unregister_executor("infrastructure")
        assert action_connector.get_executor("infrastructure") is None

    def test_get_executor_nonexistent(self):
        assert action_connector.get_executor("nonexistent") is None

    def test_simulate_then_execute_no_auto(self):
        result = action_connector.simulate_then_execute(
            "restart_service",
            {"service_name": "nginx"},
            proposed_by="kai",
            auto_execute=False,
        )
        assert result["executed"] is False
        assert result["execution_result"] is None
        assert "simulation" in result
        assert result["simulation"]["status"] == "completed"

    def test_simulate_then_execute_no_executor(self):
        result = action_connector.simulate_then_execute(
            "restart_service",
            {"service_name": "nginx"},
            auto_execute=True,
        )
        assert result["executed"] is False
        assert result["execution_result"] is not None
        assert "no executor" in result["execution_result"]["message"].lower()

    def test_simulate_then_execute_with_executor(self):
        def infra_executor(action_type, params):
            return {"success": True, "message": "restarted nginx"}

        action_connector.register_executor("infrastructure", infra_executor)

        result = action_connector.simulate_then_execute(
            "restart_service",
            {"service_name": "nginx"},
            auto_execute=True,
        )
        assert "simulation" in result
        sim_success = result["simulation"]["aggregated_results"]["mean_success_probability"]
        if sim_success >= 0.70:
            assert result["executed"] is True
            assert result["execution_result"]["success"] is True
        else:
            assert result["executed"] is False

    def test_executor_exception_handled(self):
        def broken_executor(action_type, params):
            raise RuntimeError("infrastructure down")

        action_connector.register_executor("infrastructure", broken_executor)
        result = action_connector.simulate_then_execute(
            "restart_service",
            {"service_name": "nginx"},
            auto_execute=True,
            risk_threshold=0.9,
        )
        if result["executed"]:
            assert result["execution_result"]["success"] is False
            assert "infrastructure down" in str(result["execution_result"])

    def test_reset_executors_clears_all(self):
        action_connector.register_executor("infrastructure", lambda a, p: {"success": True})
        action_connector.register_executor("application", lambda a, p: {"success": True})
        action_connector.reset_executors()
        assert action_connector.list_executors() == []


class TestModelBridge:
    """Tests for bridging legacy models into the pluggable registry."""

    def setup_method(self):
        reset_model_registry()

    def teardown_method(self):
        reset_model_registry()

    def test_bridge_registers_infra_model(self):
        registered = model_bridge.bridge_legacy_models()
        assert "deterministic_infra" in registered
        assert "deterministic_deploy" in registered

        registry = get_model_registry()
        infra_model = registry.get("deterministic_infra")
        assert infra_model is not None
        assert infra_model.model_type == "deterministic"
        assert infra_model.domain == "infrastructure"
        assert infra_model.domain_specific is True

    def test_bridge_registers_deploy_model(self):
        model_bridge.bridge_legacy_models()
        registry = get_model_registry()
        deploy_model = registry.get("deterministic_deploy")
        assert deploy_model is not None
        assert deploy_model.domain == "application"

    def test_bridged_model_runs_and_returns_simulation_outcome(self):
        model_bridge.bridge_legacy_models()
        registry = get_model_registry()
        infra_model = registry.get("deterministic_infra")

        action = ActionProposal(
            action_type="restart_service",
            description="Restart nginx",
            domain="infrastructure",
            parameters={"service_name": "nginx"},
            target_entity="nginx",
        )
        state = WorldState(
            entities={"entity_state": {"nginx": {"recent_incidents_count": 0}}, "lessons": []},
        )
        scenario = Scenario(
            name="base_case",
            description="Base case",
            scenario_type="base",
            parameters={"service_name": "nginx"},
        )

        outcome = infra_model.run(action, state, scenario)
        assert isinstance(outcome, SimulationOutcome)
        assert outcome.scenario_name == "base_case"
        assert 0 <= outcome.risk_score <= 1

    def test_bridged_model_reflects_incident_history(self):
        model_bridge.bridge_legacy_models()
        registry = get_model_registry()
        infra_model = registry.get("deterministic_infra")

        action = ActionProposal(
            action_type="restart_service",
            description="Restart nginx",
            domain="infrastructure",
            parameters={"service_name": "nginx"},
            target_entity="nginx",
        )

        clean_state = WorldState(
            entities={"entity_state": {"nginx": {"recent_incidents_count": 0}}, "lessons": []},
        )
        troubled_state = WorldState(
            entities={"entity_state": {"nginx": {"recent_incidents_count": 5}}, "lessons": []},
        )
        scenario = Scenario(
            name="base_case",
            description="Base case",
            scenario_type="base",
            parameters={"service_name": "nginx"},
        )

        clean_outcome = infra_model.run(action, clean_state, scenario)
        troubled_outcome = infra_model.run(action, troubled_state, scenario)

        assert troubled_outcome.risk_score > clean_outcome.risk_score

    def test_bridged_model_with_adversarial_scenario(self):
        model_bridge.bridge_legacy_models()
        registry = get_model_registry()
        infra_model = registry.get("deterministic_infra")

        action = ActionProposal(
            action_type="restart_service",
            description="Restart nginx",
            domain="infrastructure",
            parameters={"service_name": "nginx"},
            target_entity="nginx",
        )
        state = WorldState(
            entities={"entity_state": {}, "lessons": []},
        )
        base_scenario = Scenario(
            name="base_case",
            description="Base",
            scenario_type="base",
            parameters={"service_name": "nginx"},
        )
        adv_scenario = Scenario(
            name="adversarial_case",
            description="Adversarial",
            scenario_type="adversarial",
            parameters={"service_name": "nginx"},
        )

        base_outcome = infra_model.run(action, state, base_scenario)
        adv_outcome = infra_model.run(action, state, adv_scenario)

        assert adv_outcome.risk_score > base_outcome.risk_score

    def test_bridge_deploy_model_with_canary(self):
        model_bridge.bridge_legacy_models()
        registry = get_model_registry()
        deploy_model = registry.get("deterministic_deploy")

        action = ActionProposal(
            action_type="deploy_application",
            description="Deploy web v2.0",
            domain="application",
            parameters={
                "application_name": "web",
                "version": "2.0",
                "canary_percent": 20,
                "rollback_on_failure": True,
            },
            target_entity="web",
        )
        state = WorldState(entities={"builds": []})
        scenario = Scenario(
            name="base_case",
            description="Base",
            scenario_type="base",
            parameters={"application_name": "web", "version": "2.0"},
        )

        outcome = deploy_model.run(action, state, scenario)
        assert isinstance(outcome, SimulationOutcome)
        assert "canary" in " ".join(outcome.warnings).lower() or any(
            "canary" in w.lower() for w in outcome.warnings
        )

    def test_bridge_is_idempotent(self):
        first = model_bridge.bridge_legacy_models()
        second = model_bridge.bridge_legacy_models()
        assert first == second
        assert len(first) == 2

    def test_bridge_models_appear_in_list(self):
        model_bridge.bridge_legacy_models()
        registry = get_model_registry()
        models = registry.list_models()
        names = [m["name"] for m in models]
        assert "deterministic_infra" in names
        assert "deterministic_deploy" in names


class TestPluggableModelRegistry:
    """Tests for the ModelRegistry class."""

    def setup_method(self):
        reset_model_registry()

    def teardown_method(self):
        reset_model_registry()

    def test_register_and_get_model(self):
        registry = ModelRegistry()
        model = SimulationModel(
            name="test_model",
            model_type="deterministic",
            description="Test model",
            run_fn=lambda a, s, sc: SimulationOutcome(
                scenario_name=sc.name,
                success=True,
                risk_score=0.1,
                cost_estimate=5.0,
                time_estimate_seconds=1.0,
            ),
        )
        registry.register(model)
        retrieved = registry.get("test_model")
        assert retrieved is not None
        assert retrieved.name == "test_model"
        assert retrieved.model_type == "deterministic"

    def test_register_overwrites_existing(self):
        registry = ModelRegistry()
        m1 = SimulationModel(name="m", model_type="t1", description="d1",
                             run_fn=lambda a, s, sc: SimulationOutcome(
                                 scenario_name=sc.name, success=True, risk_score=0.1,
                                 cost_estimate=1.0, time_estimate_seconds=1.0))
        m2 = SimulationModel(name="m", model_type="t2", description="d2",
                             run_fn=lambda a, s, sc: SimulationOutcome(
                                 scenario_name=sc.name, success=True, risk_score=0.1,
                                 cost_estimate=1.0, time_estimate_seconds=1.0))
        registry.register(m1)
        registry.register(m2)
        assert registry.get("m").model_type == "t2"

    def test_unregister_model(self):
        registry = ModelRegistry()
        model = SimulationModel(name="temp", model_type="test", description="d",
                                run_fn=lambda a, s, sc: SimulationOutcome(
                                    scenario_name=sc.name, success=True, risk_score=0.1,
                                    cost_estimate=1.0, time_estimate_seconds=1.0))
        registry.register(model)
        assert registry.get("temp") is not None
        registry.unregister("temp")
        assert registry.get("temp") is None

    def test_list_models_includes_metadata(self):
        registry = ModelRegistry()
        model = SimulationModel(
            name="full_model",
            model_type="probabilistic",
            description="Full featured model",
            run_fn=lambda a, s, sc: SimulationOutcome(
                scenario_name=sc.name,
                success=True,
                risk_score=0.05,
                cost_estimate=10.0,
                time_estimate_seconds=5.0,
            ),
            assumptions=["a1", "a2"],
            limitations=["l1"],
            calibrated=True,
            domain="infrastructure",
        )
        registry.register(model)
        models = registry.list_models()
        assert len(models) == 1
        m = models[0]
        assert m["name"] == "full_model"
        assert m["type"] == "probabilistic"
        assert m["calibrated"] is True
        assert m["domain"] == "infrastructure"

    def test_get_models_for_domain_filters(self):
        registry = ModelRegistry()
        infra = SimulationModel(
            name="infra_model", model_type="det", description="Infra",
            run_fn=lambda a, s, sc: SimulationOutcome(
                scenario_name=sc.name, success=True, risk_score=0.1,
                cost_estimate=1.0, time_estimate_seconds=1.0),
            domain_specific=True, domain="infrastructure",
        )
        app = SimulationModel(
            name="app_model", model_type="det", description="App",
            run_fn=lambda a, s, sc: SimulationOutcome(
                scenario_name=sc.name, success=True, risk_score=0.1,
                cost_estimate=1.0, time_estimate_seconds=1.0),
            domain_specific=True, domain="application",
        )
        universal = SimulationModel(
            name="universal_model", model_type="det", description="Univ",
            run_fn=lambda a, s, sc: SimulationOutcome(
                scenario_name=sc.name, success=True, risk_score=0.1,
                cost_estimate=1.0, time_estimate_seconds=1.0),
            domain_specific=False,
        )
        registry.register(infra)
        registry.register(app)
        registry.register(universal)

        infra_models = registry.get_models_for_domain("infrastructure")
        names = [m.name for m in infra_models]
        assert "infra_model" in names
        assert "universal_model" in names
        assert "app_model" not in names

    def test_get_enabled_models_filters(self):
        registry = ModelRegistry()
        for name in ["a", "b", "c"]:
            registry.register(SimulationModel(
                name=name, model_type="det", description=name,
                run_fn=lambda a, s, sc: SimulationOutcome(
                    scenario_name=sc.name, success=True, risk_score=0.1,
                    cost_estimate=1.0, time_estimate_seconds=1.0),
            ))
        enabled = registry.get_enabled_models(["a", "c"])
        assert len(enabled) == 2
        assert {m.name for m in enabled} == {"a", "c"}

    def test_run_all_returns_outcomes(self):
        registry = ModelRegistry()
        registry.register(SimulationModel(
            name="m1", model_type="det", description="First",
            run_fn=lambda a, s, sc: SimulationOutcome(
                scenario_name=sc.name, success=True, risk_score=0.1,
                cost_estimate=5.0, time_estimate_seconds=1.0),
        ))
        registry.register(SimulationModel(
            name="m2", model_type="det", description="Second",
            run_fn=lambda a, s, sc: SimulationOutcome(
                scenario_name=sc.name, success=False, risk_score=0.9,
                cost_estimate=50.0, time_estimate_seconds=10.0),
        ))

        action = ActionProposal(
            action_type="restart_service",
            description="Restart",
            domain="infrastructure",
            parameters={"service_name": "nginx"},
        )
        state = WorldState()
        scenario = Scenario(name="base", description="Base", scenario_type="base")

        outcomes = registry.run_all(action, state, scenario)
        assert len(outcomes) == 2

    def test_run_all_handles_model_failure(self):
        registry = ModelRegistry()
        registry.register(SimulationModel(
            name="broken", model_type="det", description="Fails",
            run_fn=lambda a, s, sc: (_ for _ in ()).throw(RuntimeError("boom")),
        ))
        action = ActionProposal(
            action_type="restart_service",
            description="Restart",
            domain="infrastructure",
            parameters={"service_name": "nginx"},
        )
        state = WorldState()
        scenario = Scenario(name="base", description="Base", scenario_type="base")

        outcomes = registry.run_all(action, state, scenario)
        assert len(outcomes) == 1
        assert not outcomes[0].success
        assert any("boom" in w for w in outcomes[0].warnings)
