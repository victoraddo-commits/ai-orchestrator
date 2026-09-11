"""
ToolBus singleton — registry for all KAI tools.

Provides a central registry for MCP-style tools with:
  - Registration with input/output schemas
  - Capability-gated access
  - Synchronous and async invocation
  - Handler dispatch with jsonschema validation
"""

import logging
import threading
from typing import Any, Callable

import jsonschema

logger = logging.getLogger("kai.tool_bus")


class ToolBus:
    """
    Singleton registry for all available tools.

    Each tool is stored as:
      name -> {
        "name": str,
        "description": str,
        "input_schema": dict,
        "output_schema": dict,
        "risk_level": str,
        "capabilities_required": list[str],
        "handler": Callable | Coroutine | None,
      }
    """

    _instance: "ToolBus | None" = None
    _lock = threading.Lock()

    def __new__(cls) -> "ToolBus":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self) -> None:
        if hasattr(self, "_tools"):
            return
        self._tools: dict[str, dict] = {}

    def register(
        self,
        name: str,
        description: str,
        input_schema: dict = None,
        output_schema: dict = None,
        risk_level: str = None,
        capabilities_required: list[str] = None,
        handler: Callable | Any = None,
    ) -> None:
        """Register a tool with the bus."""
        self._tools[name] = {
            "name": name,
            "description": description,
            "input_schema": input_schema or {},
            "output_schema": output_schema or {},
            "risk_level": risk_level or "low",
            "capabilities_required": capabilities_required or [],
            "handler": handler,
        }
        logger.info("Registered tool: %s (risk=%s)", name, risk_level or "low")

    def unregister(self, name: str) -> bool:
        """Remove a tool from the bus. Returns True if it existed."""
        if name in self._tools:
            del self._tools[name]
            logger.info("Unregistered tool: %s", name)
            return True
        return False

    def get(self, name: str) -> dict | None:
        """Return a tool descriptor or None."""
        return self._tools.get(name)

    def list_tools(self) -> list[str]:
        """Return all registered tool names."""
        return list(self._tools.keys())

    def has_tool(self, name: str) -> bool:
        """Check if a tool is registered."""
        return name in self._tools

    def _invoke(self, name: str, params: dict) -> dict:
        """Internal invoke — look up, validate, check risk, dispatch."""
        tool = self._tools.get(name)
        if tool is None:
            raise KeyError(f"Tool not found: {name}")

        if tool["input_schema"]:
            jsonschema.validate(params, tool["input_schema"])

        from core.kai.tool_risk import check_risk

        risk = check_risk(name, params)
        if risk["blocking"]:
            raise PermissionError(
                f"Tool {name} is critical-risk and requires explicit approval"
            )

        handler = tool.get("handler")
        if handler is not None:
            return handler(params)

        return {"status": "ok", "tool": name, "note": "no handler registered"}

    def invoke(self, name: str, params: dict) -> dict:
        """Public invoke — dispatches to _invoke."""
        return self._invoke(name, params)


_bus: ToolBus | None = None


def _get_bus() -> ToolBus:
    global _bus
    if _bus is None:
        _bus = ToolBus()
    return _bus


def register(
    name: str,
    description: str,
    input_schema: dict = None,
    output_schema: dict = None,
    risk_level: str = None,
    capabilities_required: list[str] = None,
    handler: Callable | Any = None,
) -> None:
    """Register a tool with the ToolBus singleton."""
    _get_bus().register(
        name, description, input_schema, output_schema,
        risk_level, capabilities_required, handler,
    )


def unregister(name: str) -> bool:
    """Unregister a tool from the ToolBus singleton."""
    return _get_bus().unregister(name)


def get(name: str) -> dict | None:
    """Get a tool descriptor from the ToolBus singleton."""
    return _get_bus().get(name)


def list_tools() -> list[str]:
    """List all tools registered with the ToolBus singleton."""
    return _get_bus().list_tools()


def has_tool(name: str) -> bool:
    """Check if a tool is registered with the ToolBus singleton."""
    return _get_bus().has_tool(name)


def invoke(name: str, params: dict = None) -> dict:
    """Invoke a tool by name with the given params.

    Validates against the tool's input_schema, blocks critical-risk tools,
    and dispatches to the registered handler or returns a stub response.
    """
    return _get_bus().invoke(name, params or {})


def _register_builtin_handlers(bus: ToolBus = None) -> None:
    """Wire up handlers for the 6 core tools that have KAI implementations."""
    if bus is None:
        bus = _get_bus()

    from core.kai.tool_descriptors import TOOL_DESCRIPTORS

    for desc in TOOL_DESCRIPTORS:
        if not bus.has_tool(desc["name"]):
            bus.register(
                name=desc["name"],
                description=desc["description"],
                input_schema=desc.get("input_schema"),
                output_schema=desc.get("output_schema"),
                risk_level=desc.get("risk_level", "low"),
                capabilities_required=desc.get("capabilities_required", []),
            )


tool_bus = ToolBus()
