"""
AgentGuard - AI Safety Layer for KAI 2.0

Runtime security gate for autonomous agent actions.
Every action flows through check_action() before execution.
"""

from .guard import (
    AgentGuard,
    ActionRequest,
    GuardResult,
    ActionType,
    RiskLevel,
    Decision,
)

__all__ = [
    "AgentGuard",
    "ActionRequest",
    "GuardResult",
    "ActionType",
    "RiskLevel",
    "Decision",
]
