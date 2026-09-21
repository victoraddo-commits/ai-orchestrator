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
        details: str | None = None,
    ) -> dict[str, Any]:
        """Dispatch a command through authorization + execution.

        Returns `{status: "success", data: ..., risk: "...", decision: "..."}`
        on success or `{status: "error", message: ..., ...}` on refusal.

        `details` overrides the string AgentGuard sees for risk classification
        (defaults to ``str(params)[:512]``). Callers whose params carry large or
        free-form text — a worker prompt, an operator note — pass a short
        summary so benign text cannot trip the guard's substring heuristics.
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
            details=(str(params)[:512] if details is None else details),
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
                "error_type": type(e).__name__,
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
            # Canonical filename the /audit + /kai/audit aggregator merges
            # (core.api.AUDIT_SOURCES). Writing the extension-less name silently
            # kept bus events out of the Command Center audit feed.
            data = load("command_bus_audit.json") or {"schema_version": 1, "records": []}
            records = data.setdefault("records", [])
            records.append(entry)
            # Cap at last 5000 to avoid unbounded growth.
            if len(records) > 5000:
                data["records"] = records[-5000:]
            save("command_bus_audit.json", data)
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
        _register_control_commands(_BUS)
    return _BUS


def _register_core_commands(bus: CommandBus) -> None:
    """Register the built-in read-only Telegram/CC control commands.

    These are all READ actions with LOW risk — they call FastAPI endpoints
    and return the payload. Handlers live in `core.kai_control_commands`.
    """
    from core import kai_control_commands as kcc

    def _wrap(command: str):
        # Return a handler that treats `params` as-is and calls the underlying
        # formatter, so `dispatch` gets a clean {status/data} shape. The full
        # original text (including arguments, e.g. "/mission <goal>") is
        # carried in params["text"]; fall back to the command name.
        def _h(params):
            text = (params or {}).get("text") or command
            return kcc.handle_control_command(text)
        return _h

    read_low = (ActionType.READ, RiskLevel.LOW)
    for cmd in ("/status", "/network", "/vpn", "/missions", "/approvals",
                "/health", "/help"):
        bus.register_handler(cmd, _wrap(cmd), *read_low)

    # Workforce-mutating commands are WRITE/MEDIUM: AgentGuard allows them and
    # writes an audit event (Telegram §53 — authorization is not bypassed).
    for cmd in kcc.TEAM_COMMANDS:
        bus.register_handler(cmd, _wrap(cmd), ActionType.WRITE, RiskLevel.MEDIUM)


def _register_control_commands(bus: CommandBus) -> None:
    """Register the mutating control-plane actions of every interface (build 23A).

    These are the actions the Command Center/web, voice, the Android factory and
    the worker pool perform that change state. They all dispatch through the
    bus so AgentGuard authorizes them and every one lands in
    ``command_bus_audit``. Handlers import their implementation lazily so this
    module stays import-light and framework-free.

    Still direct (not routed here), and why:
      * read-only GETs everywhere — the bus exists for actions, not reads;
      * ``/api/docker/containers/{name}/{start,stop,restart}`` — the endpoints
        use an async httpx client to a configured remote Docker host; bus
        handlers are synchronous, so bridging async→sync inside the running
        loop is unsafe and the CLI-based tool is not equivalent;
      * tool-bus ``core.kai_tools.policy.execute`` — its own policy + audit
        gate; the actions it triggers (factory build/scaffold) additionally
        dispatch here. A caller that already holds the authorization (the
        policy gate) is the one documented escape hatch: ``policy`` invokes
        tools directly rather than re-entering the command bus.
    """
    write_medium = (ActionType.WRITE, RiskLevel.MEDIUM)

    def _emergency_stop(params):
        from core.kai_emergency import emergency_stop
        return emergency_stop(operator=params.get("user") or "operator",
                              reason=params.get("reason") or "")

    def _emergency_resume(params):
        from core.kai_emergency import emergency_resume
        return emergency_resume(operator=params.get("user") or "operator")

    def _mission_steer(params):
        from core.kai import mission_engine
        mission = mission_engine.steer_mission(
            params["mission_id"], params.get("action") or "",
            objective=params.get("objective") or None,
            note=params.get("note") or None)
        return {"ok": True, "mission": mission}

    def _mission_stop(params):
        from core.kai import mission_engine
        mission = mission_engine.steer_mission(
            params["mission_id"], params.get("action") or "stop",
            objective=params.get("objective") or None,
            note=params.get("note") or None)
        return {"ok": True, "mission": mission}

    def _scheduler_pause(params):
        from core.api import _get_pause_state, _set_pause_state
        _set_pause_state(True, reason=params.get("reason") or "", operator="command_bus")
        return {"ok": True, "scheduler": _get_pause_state()}

    def _scheduler_resume(params):
        from core.api import _get_pause_state, _set_pause_state
        _set_pause_state(False)
        return {"ok": True, "scheduler": _get_pause_state()}

    def _factory_build(params):
        from core.kai_tools.builtin import _factory_build_http
        return _factory_build_http(params["project"])

    def _factory_scaffold(params):
        from core.kai_tools.builtin import _factory_scaffold_http
        return _factory_scaffold_http(params["name"], params["package"],
                                      params.get("template") or "empty")

    def _voice_intent(params):
        from core.kai import commands
        return commands.dispatch(params["text"], via_bus=False)

    def _worker_submit(params):
        from core.workers.deepseek_pool import get_pool
        return get_pool()._enqueue_task(
            params["task_type"], params["prompt"],
            build_id=params.get("build_id"), build_name=params.get("build_name"))

    for cmd, handler in (
        ("control.emergency.stop", _emergency_stop),
        ("control.emergency.resume", _emergency_resume),
        ("control.scheduler.pause", _scheduler_pause),
        ("control.scheduler.resume", _scheduler_resume),
        ("control.mission.steer", _mission_steer),
        ("control.mission.stop", _mission_stop),
        ("control.factory.build", _factory_build),
        ("control.factory.scaffold", _factory_scaffold),
        ("control.voice.intent", _voice_intent),
        ("control.worker.submit", _worker_submit),
    ):
        bus.register_handler(cmd, handler, *write_medium)
