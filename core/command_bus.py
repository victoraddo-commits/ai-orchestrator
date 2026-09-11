"""KAI Command Bus — the single dispatch point for user intents.

Every command from Telegram, the Command Center, an agent, or a subprocess
flows through `bus.dispatch(...)`. Dispatch:

  1. resolves the command in the handler registry
  2. runs the request through AgentGuard for policy + risk classification
  3. logs an audit event with the guard verdict
  4. executes the handler (or refuses)

This module was previously imported nowhere. As of 2026-09-11 it is used by
`core.kai_control_commands` (every /status /network /vpn /missions /approvals
/health command from Telegram) so that AgentGuard checks and audit events
actually fire on real user intents.
"""
from __future__ import annotations

import datetime
import logging
from typing import Any, Callable

from core.agentguard.guard import (
    ActionRequest,
    ActionType,
    AgentGuard,
    Decision,
    RiskLevel,
)
from core.memory import load, save

logger = logging.getLogger(__name__)


class CommandBus:
    """In-process command dispatcher with AgentGuard authorization."""

    def __init__(self, guard: AgentGuard | None = None):
        # Registry: command_name -> {handler_fn, action_type, risk_hint}
        self._registry: dict[str, dict[str, Any]] = {}
        self._guard = guard or AgentGuard()

    def register_handler(
        self,
        command_name: str,
        handler_fn: Callable[[dict[str, Any]], Any],
        action_type: ActionType,
        risk_hint: RiskLevel = RiskLevel.LOW,
    ) -> None:
        """Register a command handler.

        `action_type` says what kind of action this is (READ/WRITE/NETWORK/…).
        `risk_hint` is the default risk classification AgentGuard applies if
        no policy overrides it.
        """
        self._registry[command_name] = {
            "handler": handler_fn,
            "action_type": action_type,
            "risk_hint": risk_hint,
        }

    def has(self, command_name: str) -> bool:
        return command_name in self._registry

    def list_commands(self) -> list[str]:
        return list(self._registry.keys())

    def dispatch(
        self,
        command_name: str,
        params: dict[str, Any] | None = None,
        source: str = "unknown",
        user: str = "anonymous",
    ) -> dict[str, Any]:
        """Dispatch a command through authorization + execution.

        Returns `{status: "success", data: ..., risk: "...", decision: "..."}`
        on success or `{status: "error", message: ..., ...}` on refusal.
        """
        params = params or {}

        entry = self._registry.get(command_name)
        if entry is None:
            msg = f"unknown command: {command_name}"
            self._audit(command_name, source, user, status="fail", reason=msg)
            return {"status": "error", "message": msg}

        req = ActionRequest(
            agent_id=source,
            user_id=user,
            action_type=entry["action_type"],
            resource=command_name,
            details=str(params)[:512],
            reason=None,
            metadata={"risk_hint": entry["risk_hint"].value, "params": params},
        )

        try:
            verdict = self._guard.check_action(req)
        except Exception as e:
            logger.warning("AgentGuard error, denying %s: %s", command_name, e)
            self._audit(command_name, source, user, status="fail",
                        reason=f"guard error: {e}")
            return {"status": "error", "message": "authorization system error"}

        if verdict.decision == Decision.DENY:
            self._audit(command_name, source, user, status="denied",
                        reason=verdict.message, decision=verdict.decision.value,
                        risk=verdict.risk_level.value)
            return {
                "status": "error",
                "message": verdict.message or "denied by policy",
                "risk": verdict.risk_level.value,
                "decision": verdict.decision.value,
            }

        if verdict.decision == Decision.REQUIRE_APPROVAL:
            self._audit(command_name, source, user, status="approval_required",
                        reason=verdict.message, decision=verdict.decision.value,
                        risk=verdict.risk_level.value)
            return {
                "status": "error",
                "message": verdict.message or "requires approval",
                "risk": verdict.risk_level.value,
                "decision": verdict.decision.value,
                "approval_request_id": verdict.approval_request_id,
            }

        # ALLOW → execute
        try:
            result = entry["handler"](params)
        except Exception as e:
            logger.exception("command %s handler raised", command_name)
            self._audit(command_name, source, user, status="fail",
                        reason=f"execution error: {e}",
                        decision=verdict.decision.value,
                        risk=verdict.risk_level.value)
            return {
                "status": "error",
                "message": f"execution failed: {e}",
                "risk": verdict.risk_level.value,
                "decision": verdict.decision.value,
            }

        self._audit(command_name, source, user, status="success",
                    decision=verdict.decision.value,
                    risk=verdict.risk_level.value)
        return {
            "status": "success",
            "data": result,
            "risk": verdict.risk_level.value,
            "decision": verdict.decision.value,
        }

    def _audit(
        self,
        command_name: str,
        source: str,
        user: str,
        status: str,
        *,
        reason: str | None = None,
        decision: str | None = None,
        risk: str | None = None,
    ) -> None:
        entry: dict[str, Any] = {
            "command": command_name,
            "source": source,
            "user": user,
            "status": status,
            "timestamp": datetime.datetime.utcnow().isoformat(),
        }
        if reason:
            entry["reason"] = reason
        if decision:
            entry["decision"] = decision
        if risk:
            entry["risk"] = risk
        try:
            data = load("command_bus_audit") or {"schema_version": 1, "records": []}
            records = data.setdefault("records", [])
            records.append(entry)
            # Cap at last 5000 to avoid unbounded growth.
            if len(records) > 5000:
                data["records"] = records[-5000:]
            save("command_bus_audit", data)
        except Exception as e:
            # Never fail dispatch because the audit log couldn't write.
            logger.warning("command bus audit failed: %s", e)


# ── Module-level singleton — the single bus every entry point uses ─────────
_BUS: CommandBus | None = None


def get_bus() -> CommandBus:
    """Return the process-wide CommandBus singleton (lazy)."""
    global _BUS
    if _BUS is None:
        _BUS = CommandBus()
        _register_core_commands(_BUS)
    return _BUS


def _register_core_commands(bus: CommandBus) -> None:
    """Register the built-in read-only Telegram/CC control commands.

    These are all READ actions with LOW risk — they call FastAPI endpoints
    and return the payload. Handlers live in `core.kai_control_commands`.
    """
    from core import kai_control_commands as kcc

    def _wrap(text: str):
        # Return a handler that treats `params` as-is and calls the underlying
        # formatter, so `dispatch` gets a clean {status/data} shape.
        def _h(params):
            return kcc.handle_control_command(text)
        return _h

    read_low = (ActionType.READ, RiskLevel.LOW)
    for cmd in ("/status", "/network", "/vpn", "/missions", "/approvals",
                "/health", "/help"):
        bus.register_handler(cmd, _wrap(cmd), *read_low)
