"""Scenario generator — creates and configures scenarios for simulation runs."""
from typing import Any, Dict, List, Optional

from core.cerebrum import scenarios as scenario_lib
from .schemas import ActionProposal, WorldState, Scenario


def generate_scenarios_for_action(action: Dict[str, Any],
                                  world_state: WorldState,
                                  scenario_types: Optional[List[str]] = None) -> List[Scenario]:
    return scenario_lib.generate_scenarios(action, world_state.__dict__)
