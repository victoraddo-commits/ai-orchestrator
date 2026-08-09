"""Cerebrum Simulation Engine — K4 phase: simulation-driven action planning.

Provides:
- Action schema validation (action_schema)
- Scenario generation (scenarios)
- World state snapshots (state_manager)
- Model registry (models, simulation/model_registry)
- Execution engine (simulation/execution_engine)
- Risk analysis and aggregation (risk_analyzer)
- Feedback loop (feedback)
- Explanation layer (explanation)
- Simulation-then-execute pipeline (action_connector)
- Legacy model bridge (model_bridge)
"""

from .simulation import (
    ActionProposal, WorldState, Scenario, SimulationConfig,
    SimulationOutcome, AggregatedResult,
    ModelRegistry, SimulationModel,
    SimulationExecutionEngine, create_execution_engine,
    SimulationStateManager, run_simulation, simulate_before_action,
    record_actual_outcome, list_simulations, get_simulation,
)

from .models import register_model, get_model, list_models
from .action_schema import (
    get_schema, list_action_types,
    actions_for_domain, new_action, validate_action, ACTION_SCHEMAS,
)
from .command_center import CommandCenter, get_command_center

__all__ = [
    "ActionProposal", "WorldState", "Scenario", "SimulationConfig",
    "SimulationOutcome", "AggregatedResult",
    "ModelRegistry", "SimulationModel",
    "SimulationExecutionEngine", "create_execution_engine",
    "SimulationStateManager",
    "register_model", "get_model", "list_models",
    "get_schema", "list_action_types",
    "actions_for_domain", "new_action", "validate_action", "ACTION_SCHEMAS",
    "run_simulation", "simulate_before_action", "record_actual_outcome",
    "list_simulations", "get_simulation",
    "CommandCenter", "get_command_center",
]
