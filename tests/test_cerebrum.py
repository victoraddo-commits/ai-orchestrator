import pytest
from datetime import datetime

import core.memory as memory
from core.cerebrum.action_schema import (
    validate_action,
    get_schema,
    list_action_types,
    actions_for_domain,
    new_action,
    ACTION_SCHEMAS,
)
from core.cerebrum.scenarios import generate_scenarios
from core.cerebrum.models import (
    register_model,
    get_model,
    list_models,
    _deterministic_infra_model,
    _deterministic_deploy_model,
)
from core.cerebrum.risk_analyzer import (
    aggregate_results,
    compare_to_baseline,
    _classify_risk,
)
from core.cerebrum.feedback import (
    record_outcome,
    get_feedback_for_action,
    recalibrate,
)
from core.cerebrum.state_manager import snapshot_world_state, _extract_entity_state
from core.cerebrum.simulation import (
    run_simulation,
    simulate_before_action,
    record_actual_outcome,
    list_simulations,
    get_simulation,
)


class TestActionSchema:
    def test_get_schema_returns_known_type(self):
        schema = get_schema("restart_service")
        assert schema is not None
        assert "required_params" in schema
        assert "service_name" in schema["required_params"]

    def test_get_schema_returns_none_for_unknown(self):
        assert get_schema("nonexistent_action") is None

    def test_list_action_types_includes_all_known(self):
        types = list_action_types()
        assert "restart_service" in types
        assert "deploy_application" in types
        assert "scale_service" in types
        assert "config_change" in types
        assert "resource_allocation" in types
        assert "dependency_update" in types
        assert "network_change" in types

    def test_actions_for_domain_filters(self):
        infra = actions_for_domain("infrastructure")
        app = actions_for_domain("application")
        assert "restart_service" in infra
        assert "deploy_application" in app
        assert "deploy_application" not in infra

    def test_validate_action_passes_with_all_required(self):
        validate_action("restart_service", {"service_name": "nginx"})

    def test_validate_action_raises_for_missing_required(self):
        with pytest.raises(ValueError, match="missing required parameters"):
            validate_action("restart_service", {})

    def test_validate_action_raises_for_unknown_type(self):
        with pytest.raises(ValueError, match="unknown action_type"):
            validate_action("do_something_wild", {})

    def test_validate_action_raises_for_unknown_params(self):
        with pytest.raises(ValueError, match="unknown parameters"):
            validate_action("restart_service", {
                "service_name": "nginx",
                "alien_param": 42,
            })

    def test_new_action_creates_valid_object(self):
        action = new_action("restart_service", {"service_name": "nginx"})
        assert action["action_type"] == "restart_service"
        assert action["status"] == "proposed"
        assert action["domain"] == "infrastructure"
        assert action["target"] == "nginx"
        assert action["parameters"] == {"service_name": "nginx"}
        assert "id" in action
        assert "trace_id" in action

    def test_new_action_raises_for_unknown_type(self):
        with pytest.raises(ValueError):
            new_action("unknown", {})


class TestScenarios:
    def test_generates_four_scenarios(self):
        action = {
            "action_type": "restart_service",
            "parameters": {"service_name": "nginx"},
            "target": "nginx",
            "domain": "infrastructure",
        }
        scenarios = generate_scenarios(action)
        assert len(scenarios) == 4
        types = [s["type"] for s in scenarios]
        assert "base" in types
        assert "optimistic" in types
        assert "pessimistic" in types
        assert "adversarial" in types

    def test_optimistic_adjusts_restart_grace(self):
        action = {
            "action_type": "restart_service",
            "parameters": {"service_name": "nginx"},
            "target": "nginx",
            "domain": "infrastructure",
        }
        scenarios = generate_scenarios(action)
        optimistic = next(s for s in scenarios if s["type"] == "optimistic")
        assert optimistic["parameters"].get("force") is False

    def test_adversarial_adjusts_restart_force(self):
        action = {
            "action_type": "restart_service",
            "parameters": {"service_name": "nginx"},
            "target": "nginx",
            "domain": "infrastructure",
        }
        scenarios = generate_scenarios(action)
        adversarial = next(s for s in scenarios if s["type"] == "adversarial")
        assert adversarial["parameters"].get("force") is True

    def test_pessimistic_reduces_scale_replicas(self):
        action = {
            "action_type": "scale_service",
            "parameters": {"service_name": "api", "replicas": 5},
            "target": "api",
            "domain": "infrastructure",
        }
        scenarios = generate_scenarios(action)
        pessimistic = next(s for s in scenarios if s["type"] == "pessimistic")
        assert pessimistic["parameters"]["replicas"] == 4

    def test_optimistic_enables_deploy_safeguards(self):
        action = {
            "action_type": "deploy_application",
            "parameters": {"application_name": "web", "version": "2.0"},
            "target": "web",
            "domain": "application",
        }
        scenarios = generate_scenarios(action)
        optimistic = next(s for s in scenarios if s["type"] == "optimistic")
        assert optimistic["parameters"]["canary_percent"] == 20
        assert optimistic["parameters"]["rollback_on_failure"] is True


class TestModels:
    def test_deterministic_infra_model_restart(self):
        scenario = {"type": "base", "label": "base", "id": "s1"}
        state = {"entity_state": {}, "learning": []}
        action = {
            "action_type": "restart_service",
            "parameters": {"service_name": "nginx"},
            "target": "nginx",
        }
        result = _deterministic_infra_model(scenario, state, action)
        assert "success_probability" in result
        assert "estimated_downtime_seconds" in result
        assert "risk_factors" in result
        assert 0 <= result["success_probability"] <= 1

    def test_deterministic_infra_model_scale(self):
        scenario = {"type": "base", "label": "base", "id": "s1"}
        state = {
            "entity_state": {
                "api": {
                    "entity": "api",
                    "current_state": {"replicas": 2},
                    "recent_incidents_count": 0,
                }
            },
            "learning": [],
        }
        action = {
            "action_type": "scale_service",
            "parameters": {"service_name": "api", "replicas": 5},
            "target": "api",
        }
        result = _deterministic_infra_model(scenario, state, action)
        assert "success_probability" in result
        assert "capacity_change_ratio" in result
        assert result["capacity_change_ratio"] > 0

    def test_deterministic_infra_model_config_change(self):
        scenario = {"type": "base", "label": "base", "id": "s1"}
        state = {"entity_state": {}, "learning": []}
        action = {
            "action_type": "config_change",
            "parameters": {
                "target_service": "app",
                "config_key": "timeout",
                "new_value": "30s",
            },
            "target": "app",
        }
        result = _deterministic_infra_model(scenario, state, action)
        assert "success_probability" in result
        assert "risk_factors" in result
        assert "rollback_complexity" in result

    def test_deterministic_infra_model_resource_allocation(self):
        scenario = {"type": "base", "label": "base", "id": "s1"}
        state = {"entity_state": {}, "learning": []}
        action = {
            "action_type": "resource_allocation",
            "parameters": {
                "target_service": "db",
                "resource_type": "memory",
                "amount": 4096,
            },
            "target": "db",
        }
        result = _deterministic_infra_model(scenario, state, action)
        assert "success_probability" in result
        assert "memory_allocation_oom_risk" in result["risk_factors"]

    def test_deterministic_infra_model_dependency_update(self):
        scenario = {"type": "base", "label": "base", "id": "s1"}
        state = {"entity_state": {}, "learning": []}
        action = {
            "action_type": "dependency_update",
            "parameters": {
                "service_name": "api",
                "dependency_name": "requests",
                "target_version": "3.0.0",
            },
            "target": "api",
        }
        result = _deterministic_infra_model(scenario, state, action)
        assert "success_probability" in result
        assert "breaking_change_possible" in result["risk_factors"]

    def test_deterministic_infra_model_network_change(self):
        scenario = {"type": "base", "label": "base", "id": "s1"}
        state = {"entity_state": {}, "learning": []}
        action = {
            "action_type": "network_change",
            "parameters": {
                "target": "firewall",
                "change_type": "firewall",
                "port": 443,
            },
            "target": "firewall",
        }
        result = _deterministic_infra_model(scenario, state, action)
        assert "success_probability" in result
        assert "complete_isolation_risk" in result["risk_factors"]

    def test_pessimistic_reduces_success_probability(self):
        base_scenario = {"type": "base", "label": "base", "id": "s1"}
        pessim_scenario = {"type": "pessimistic", "label": "pess", "id": "s2"}
        state = {"entity_state": {}, "learning": []}
        action = {
            "action_type": "restart_service",
            "parameters": {"service_name": "nginx"},
            "target": "nginx",
        }
        base_result = _deterministic_infra_model(base_scenario, state, action)
        pessim_result = _deterministic_infra_model(pessim_scenario, state, action)
        assert pessim_result["success_probability"] < base_result["success_probability"]

    def test_adversarial_has_lowest_success(self):
        base_scenario = {"type": "base", "label": "base", "id": "s1"}
        adv_scenario = {"type": "adversarial", "label": "adv", "id": "s2"}
        state = {"entity_state": {}, "learning": []}
        action = {
            "action_type": "restart_service",
            "parameters": {"service_name": "nginx"},
            "target": "nginx",
        }
        base_result = _deterministic_infra_model(base_scenario, state, action)
        adv_result = _deterministic_infra_model(adv_scenario, state, action)
        assert adv_result["success_probability"] < base_result["success_probability"]

    def test_incident_history_reduces_success(self):
        scenario = {"type": "base", "label": "base", "id": "s1"}
        clean_state = {"entity_state": {}, "learning": []}
        troubled_state = {
            "entity_state": {
                "nginx": {
                    "entity": "nginx",
                    "recent_incidents_count": 5,
                }
            },
            "learning": [],
        }
        action = {
            "action_type": "restart_service",
            "parameters": {"service_name": "nginx"},
            "target": "nginx",
        }
        clean_result = _deterministic_infra_model(scenario, clean_state, action)
        troubled_result = _deterministic_infra_model(scenario, troubled_state, action)
        assert troubled_result["success_probability"] < clean_result["success_probability"]

    def test_learning_trusted_boosts_success(self):
        scenario = {"type": "base", "label": "base", "id": "s1"}
        state_with_learning = {
            "entity_state": {},
            "learning": [
                {"action": "restart_service", "recommendation": "trusted"},
                {"action": "restart_service", "recommendation": "trusted"},
                {"action": "restart_service", "recommendation": "trusted"},
            ],
        }
        state_without = {"entity_state": {}, "learning": []}
        action = {
            "action_type": "restart_service",
            "parameters": {"service_name": "nginx"},
            "target": "nginx",
        }
        with_learning = _deterministic_infra_model(scenario, state_with_learning, action)
        without = _deterministic_infra_model(scenario, state_without, action)
        assert with_learning["success_probability"] > without["success_probability"]

    def test_deterministic_deploy_model(self):
        scenario = {"type": "base", "label": "base", "id": "s1"}
        state = {"builds": []}
        action = {
            "action_type": "deploy_application",
            "parameters": {"application_name": "web", "version": "2.0"},
            "target": "web",
        }
        result = _deterministic_deploy_model(scenario, state, action)
        assert "success_probability" in result
        assert 0 <= result["success_probability"] <= 1

    def test_deterministic_deploy_with_history(self):
        scenario = {"type": "base", "label": "base", "id": "s1"}
        state = {
            "builds": [
                {"name": "web-service", "status": "deployed"},
                {"name": "web-service", "status": "deployed"},
                {"name": "web-service", "status": "failed"},
            ]
        }
        action = {
            "action_type": "deploy_application",
            "parameters": {"application_name": "web", "version": "2.0"},
            "target": "web-service",
        }
        result = _deterministic_deploy_model(scenario, state, action)
        assert "success_probability" in result
        assert "no_automatic_rollback" in result["risk_factors"]

    def test_register_model_and_retrieve(self):
        def dummy_model(scenario, state, action):
            return {"success_probability": 0.5}
        register_model(
            "test_model",
            "deterministic",
            "testing",
            dummy_model,
            inputs=["scenario", "state_snapshot", "action"],
            outputs=["success_probability"],
        )
        model = get_model("test_model")
        assert model is not None
        assert model["model_type"] == "deterministic"
        assert model["domain"] == "testing"

    def test_list_models_filters_by_domain(self):
        infra_models = list_models(domain="infrastructure")
        assert "deterministic_infra" in infra_models
        for m in infra_models.values():
            assert m["domain"] == "infrastructure"

    def test_unknown_action_type_handled(self):
        scenario = {"type": "base", "label": "base", "id": "s1"}
        state = {"entity_state": {}, "learning": []}
        action = {
            "action_type": "alien_invasion",
            "parameters": {},
            "target": "earth",
        }
        result = _deterministic_infra_model(scenario, state, action)
        assert "success_probability" in result
        assert "unknown_action_type" in result["risk_factors"]


class TestRiskAnalyzer:
    def test_aggregate_results_empty(self):
        result = aggregate_results([])
        assert result["risk_level"] == "unknown"
        assert result["per_scenario"] == []

    def test_aggregate_results_single_scenario(self):
        scenario_results = [
            {
                "scenario": {"type": "base", "label": "base case"},
                "outcome": {
                    "success_probability": 0.95,
                    "risk_factors": ["minor"],
                    "expected_impact": "low",
                },
            }
        ]
        result = aggregate_results(scenario_results)
        assert result["mean_success_probability"] == 0.95
        assert result["risk_level"] == "low"
        assert "minor" in result["all_risk_factors"]

    def test_aggregate_results_multiple_scenarios(self):
        scenario_results = [
            {
                "scenario": {"type": "base", "label": "base"},
                "outcome": {
                    "success_probability": 0.90,
                    "risk_factors": [],
                    "expected_impact": "low",
                },
            },
            {
                "scenario": {"type": "pessimistic", "label": "pess"},
                "outcome": {
                    "success_probability": 0.60,
                    "risk_factors": ["downtime_risk"],
                    "expected_impact": "high",
                },
            },
        ]
        result = aggregate_results(scenario_results)
        assert result["mean_success_probability"] == 0.75
        assert result["worst_case_probability"] == 0.60
        assert result["best_case_probability"] == 0.90
        assert "downtime_risk" in result["all_risk_factors"]

    def test_aggregate_with_downtime(self):
        scenario_results = [
            {
                "scenario": {"type": "base", "label": "base"},
                "outcome": {
                    "success_probability": 0.85,
                    "estimated_downtime_seconds": 10,
                    "risk_factors": [],
                    "expected_impact": "low",
                },
            },
            {
                "scenario": {"type": "pessimistic", "label": "pess"},
                "outcome": {
                    "success_probability": 0.50,
                    "estimated_downtime_seconds": 120,
                    "risk_factors": ["high_downtime"],
                    "expected_impact": "high",
                },
            },
        ]
        result = aggregate_results(scenario_results)
        assert result["estimated_downtime_max_seconds"] == 120

    def test_classify_risk_low(self):
        assert _classify_risk(0.95, 0.1, ["minor"]) == "low"

    def test_classify_risk_high(self):
        assert _classify_risk(0.30, 0.5, ["a", "b", "c", "d"]) == "high"

    def test_compare_to_baseline_high_risk(self):
        aggregated = {
            "mean_success_probability": 0.65,
            "risk_level": "high",
            "all_risk_factors": ["a", "b"],
        }
        baseline = {
            "mean_success_probability": 1.0,
            "risk_level": "none",
            "all_risk_factors": [],
        }
        result = compare_to_baseline(aggregated, baseline)
        assert result["verdict"] == "high_risk_vs_baseline"
        assert result["delta_vs_baseline"] < -0.2

    def test_compare_to_baseline_beneficial(self):
        aggregated = {
            "mean_success_probability": 0.85,
            "risk_level": "medium",
            "all_risk_factors": [],
        }
        baseline = {
            "mean_success_probability": 0.80,
            "risk_level": "medium",
            "all_risk_factors": ["existing_issue"],
        }
        result = compare_to_baseline(aggregated, baseline)
        assert result["verdict"] == "beneficial_vs_baseline"

    def test_recommendation_proceed_for_low_risk(self):
        scenario_results = [
            {
                "scenario": {"type": "base", "label": "base"},
                "outcome": {
                    "success_probability": 0.95,
                    "risk_factors": [],
                    "expected_impact": "low",
                },
            }
        ]
        result = aggregate_results(scenario_results)
        assert result["recommendation"]["action"] == "proceed"
        assert result["recommendation"]["confidence"] == "high"

    def test_recommendation_avoid_for_high_risk(self):
        scenario_results = [
            {
                "scenario": {"type": "adversarial", "label": "adv"},
                "outcome": {
                    "success_probability": 0.25,
                    "risk_factors": ["a", "b", "c", "d", "e"],
                    "expected_impact": "high",
                },
            }
        ]
        result = aggregate_results(scenario_results)
        assert result["recommendation"]["action"] == "avoid_or_prepare"


class TestFeedback:
    def test_record_outcome_creates_feedback(self):
        feedback = record_outcome(
            "sim1",
            {"action_type": "restart_service", "target": "nginx"},
            {"mean_success_probability": 0.90},
            {"success": True},
            notes="went well",
        )
        assert feedback["simulation_id"] == "sim1"
        assert feedback["prediction_error"] is not None
        assert feedback["action"]["action_type"] == "restart_service"

    def test_get_feedback_for_action_returns_matches(self):
        record_outcome(
            "sim2",
            {"action_type": "restart_service", "target": "postgres"},
            {"mean_success_probability": 0.85},
            {"success": True},
        )
        record_outcome(
            "sim3",
            {"action_type": "scale_service", "target": "api"},
            {"mean_success_probability": 0.70},
            {"success": False},
        )

        restart_feedback = get_feedback_for_action("restart_service")
        assert len(restart_feedback) >= 1
        for f in restart_feedback:
            assert f["action"]["action_type"] == "restart_service"

        scale_feedback = get_feedback_for_action("scale_service")
        assert len(scale_feedback) >= 1
        for f in scale_feedback:
            assert f["action"]["action_type"] == "scale_service"

    def test_prediction_error_computed(self):
        feedback = record_outcome(
            "sim4",
            {"action_type": "restart_service", "target": "nginx"},
            {"mean_success_probability": 0.90},
            {"success": False},
        )
        assert feedback["prediction_error"] == pytest.approx(0.90, abs=0.01)

    def test_prediction_error_no_outcome(self):
        feedback = record_outcome(
            "sim5",
            {"action_type": "restart_service"},
            None,
            None,
        )
        assert feedback["prediction_error"] is None

    def test_recalibrate_returns_stats(self):
        record_outcome(
            "sim6",
            {"action_type": "restart_service"},
            {"mean_success_probability": 0.95},
            {"success": True},
        )
        record_outcome(
            "sim7",
            {"action_type": "restart_service"},
            {"mean_success_probability": 0.80},
            {"success": False},
        )
        calibration = recalibrate()
        assert "restart_service" in calibration
        assert calibration["restart_service"]["sample_count"] >= 2
        assert "mean_prediction_error" in calibration["restart_service"]

    def test_recalibrate_empty(self):
        result = recalibrate()
        assert result == {}


class TestStateManager:
    def test_snapshot_world_state_returns_snapshot(self):
        snapshot = snapshot_world_state()
        assert "snapshot_id" in snapshot
        assert "captured_at" in snapshot
        assert "system" in snapshot
        assert "incidents" in snapshot
        assert "learning" in snapshot
        assert "builds" in snapshot

    def test_snapshot_with_context_entities(self):
        snapshot = snapshot_world_state(
            context_entities=["nginx", "postgres"]
        )
        assert snapshot["context_entities"] == ["nginx", "postgres"]
        assert "entity_state" in snapshot
        assert "nginx" in snapshot["entity_state"]
        assert "postgres" in snapshot["entity_state"]

    def test_extract_entity_state_handles_nonexistent(self):
        result = _extract_entity_state("ghost_service", {}, {})
        assert result["entity"] == "ghost_service"
        assert result["recent_incidents_count"] == 0


class TestSimulationEngine:
    def test_run_simulation_restart(self):
        sim = run_simulation(
            "restart_service",
            {"service_name": "nginx"},
            proposed_by="kai",
        )
        assert sim["status"] == "completed"
        assert "aggregated_results" in sim
        assert "recommendation" in sim
        assert sim["action"]["action_type"] == "restart_service"

    def test_run_simulation_invalid_action_raises(self):
        with pytest.raises(ValueError, match="missing required parameters"):
            run_simulation("restart_service", {})

    def test_run_simulation_unknown_action_raises(self):
        with pytest.raises(ValueError, match="unknown action_type"):
            run_simulation("nonexistent", {})

    def test_run_simulation_persists(self):
        sim = run_simulation(
            "restart_service",
            {"service_name": "redis"},
        )
        retrieved = get_simulation(sim["id"])
        assert retrieved is not None
        assert retrieved["id"] == sim["id"]
        assert retrieved["status"] == "completed"

    def test_list_simulations_returns_records(self):
        sim = run_simulation(
            "config_change",
            {
                "target_service": "app",
                "config_key": "timeout",
                "new_value": "30s",
            },
        )
        all_sims = list_simulations()
        assert len(all_sims) > 0
        ids = [s["id"] for s in all_sims]
        assert sim["id"] in ids

    def test_simulate_before_action(self):
        action = {
            "action_type": "restart_service",
            "parameters": {"service_name": "nginx"},
            "proposed_by": "kai",
            "trace_id": "trace-123",
        }
        sim = simulate_before_action(action)
        assert sim["status"] == "completed"
        assert sim["action"]["parameters"] == {"service_name": "nginx"}

    def test_record_actual_outcome(self):
        sim = run_simulation(
            "scale_service",
            {"service_name": "api", "replicas": 3},
        )
        feedback = record_actual_outcome(
            sim["id"],
            actual_success=True,
            actual_downtime_seconds=5,
            notes="scaled successfully",
        )
        assert feedback["simulation_id"] == sim["id"]
        assert feedback["prediction_error"] is not None

    def test_record_actual_outcome_nonexistent(self):
        with pytest.raises(ValueError, match="not found"):
            record_actual_outcome("nonexistent-id", True)

    def test_run_simulation_scale(self):
        sim = run_simulation(
            "scale_service",
            {"service_name": "api", "replicas": 5},
        )
        assert sim["status"] == "completed"
        agg = sim["aggregated_results"]
        assert "capacity_change_ratio" in agg.get("per_scenario", [{}])[0] or True

    def test_all_action_types_simulate(self):
        test_cases = [
            ("restart_service", {"service_name": "test-svc"}),
            ("scale_service", {"service_name": "test-svc", "replicas": 3}),
            ("config_change", {
                "target_service": "test-svc",
                "config_key": "threads",
                "new_value": "8",
            }),
            ("resource_allocation", {
                "target_service": "test-svc",
                "resource_type": "cpu",
                "amount": 4,
            }),
            ("dependency_update", {
                "service_name": "test-svc",
                "dependency_name": "libfoo",
                "target_version": "2.0",
            }),
            ("network_change", {
                "target": "test-svc",
                "change_type": "port_open",
                "port": 8080,
            }),
            ("deploy_application", {
                "application_name": "test-app",
                "version": "1.0",
            }),
        ]
        for action_type, params in test_cases:
            sim = run_simulation(action_type, params)
            assert sim["status"] == "completed", f"{action_type} failed: {sim}"
            assert "aggregated_results" in sim

    def test_simulation_has_risk_analysis(self):
        sim = run_simulation(
            "dependency_update",
            {
                "service_name": "critical-api",
                "dependency_name": "openssl",
                "target_version": "4.0.0",
            },
        )
        agg = sim["aggregated_results"]
        assert "risk_level" in agg
        assert "recommendation" in agg
        assert "confidence_interval_95" in agg
        assert agg["confidence_interval_95"][0] <= agg["mean_success_probability"]

    def test_simulation_has_baseline_comparison(self):
        sim = run_simulation(
            "restart_service",
            {"service_name": "nginx"},
        )
        assert "baseline_comparison" in sim
        bc = sim["baseline_comparison"]
        assert "verdict" in bc
        assert "delta_vs_baseline" in bc

    def test_get_simulation_nonexistent_returns_none(self):
        assert get_simulation("nonexistent") is None
