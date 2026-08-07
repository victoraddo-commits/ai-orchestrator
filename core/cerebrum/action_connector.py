"""Action connector — simulate-then-execute pipeline for Cerebrum actions."""
from typing import Any, Callable, Dict, List, Optional

from .simulation import run_simulation

_executors: Dict[str, Callable] = {}


def register_executor(domain: str, executor: Callable):
    _executors[domain] = executor


def get_executor(domain: str) -> Optional[Callable]:
    return _executors.get(domain)


def unregister_executor(domain: str):
    _executors.pop(domain, None)


def list_executors() -> List[str]:
    return sorted(_executors.keys())


def reset_executors():
    _executors.clear()


def simulate_then_execute(action_type: str, params: Dict[str, Any],
                          proposed_by: str = "kai", auto_execute: bool = False,
                          risk_threshold: float = 0.3) -> Dict[str, Any]:
    from .simulation import run_simulation
    from .action_schema import get_schema

    # First simulate
    try:
        sim = run_simulation(action_type, params, proposed_by=proposed_by)
    except ValueError as e:
        return {
            "executed": False,
            "simulation": None,
            "execution_result": {"success": False, "message": str(e)},
        }

    result = {
        "simulation": sim,
        "executed": False,
        "execution_result": None,
    }

    if not auto_execute:
        return result

    # Check risk threshold
    success_prob = sim.get("aggregated_results", {}).get("mean_success_probability", 0)
    if success_prob < 1.0 - risk_threshold:
        return result

    # Execute
    schema = get_schema(action_type)
    domain = schema.get("domain", "infrastructure") if schema else "infrastructure"
    executor = _executors.get(domain)

    if executor is None:
        result["execution_result"] = {"success": False, "message": f"no executor registered for domain: {domain}"}
        return result

    try:
        exec_result = executor(action_type, params)
        result["executed"] = True
        result["execution_result"] = exec_result
    except Exception as e:
        result["executed"] = True
        result["execution_result"] = {"success": False, "message": str(e)}

    return result
