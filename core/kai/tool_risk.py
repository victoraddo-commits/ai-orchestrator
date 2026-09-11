"""
Risk classification for tool invocation.

Provides check_risk() and get_risk_for_tools() to classify tools by risk level,
determine whether human approval is required, and identify blocking (critical) tools.

High-risk patterns in secret_path for vault.request are detected and bump the risk
to critical (already critical — enforced at the vault boundary).
"""

import re
from typing import Any

from core.kai.tool_descriptors import TOOL_DESCRIPTORS

_TOOL_LOOKUP: dict[str, dict] = {d["name"]: d for d in TOOL_DESCRIPTORS}

_HIGH_RISK_PATTERNS = [
    re.compile(r"api[_-]?key", re.IGNORECASE),
    re.compile(r"password", re.IGNORECASE),
    re.compile(r"token", re.IGNORECASE),
    re.compile(r"private[_-]?key", re.IGNORECASE),
    re.compile(r"secret[_-]?key", re.IGNORECASE),
]


def _is_high_risk_secret_path(secret_path: str) -> bool:
    """Return True if secret_path matches any high-risk pattern."""
    return any(p.search(secret_path) for p in _HIGH_RISK_PATTERNS)


def check_risk(tool_name: str, params: Any = None) -> dict:
    """
    Return risk classification for a tool.

    Args:
        tool_name: The name of the tool (e.g. "vault.request").
        params: Optional dict of parameters passed to the tool.
                For "vault.request", the "secret_path" key is checked for
                high-risk patterns.

    Returns:
        A dict with keys:
          - risk_level: "low" | "medium" | "high" | "critical"
          - requires_approval: True if risk_level is "high" or "critical"
          - blocking: True if risk_level is "critical"
    """
    params = params or {}

    descriptor = _TOOL_LOOKUP.get(tool_name)
    if descriptor is None:
        risk_level = "low"
    else:
        risk_level = descriptor.get("risk_level", "low")

    if tool_name == "vault.request" and "secret_path" in params:
        if _is_high_risk_secret_path(params["secret_path"]):
            risk_level = "critical"

    HIGH_RISK_LEVELS = {"high", "critical"}
    CRITICAL_LEVEL = {"critical"}

    return {
        "risk_level": risk_level,
        "requires_approval": risk_level in HIGH_RISK_LEVELS,
        "blocking": risk_level in CRITICAL_LEVEL,
    }


def get_risk_for_tools(tools: list[str]) -> dict[str, dict]:
    """
    Return risk classification for multiple tools at once.

    Args:
        tools: List of tool names.

    Returns:
        A dict mapping each tool name to its risk classification dict
        (same format as check_risk).
    """
    return {name: check_risk(name) for name in tools}
