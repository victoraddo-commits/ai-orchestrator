import json
import os
import datetime
from core.agentguard.guard import ActionType, RiskLevel, check_permission
from core.memory import log_audit

class CommandBus:
    """
    A thin in-process command dispatch layer.
    Orchestrates authorization via AgentGuard and execution of handlers.
    """
    
    def __init__(self):
        # Registry: command_name -> {handler_fn, action_type}
        self._registry = {}

    def register_handler(self, command_name, handler_fn, action_type):
        """
        Registers a command handler with its required ActionType for authorization.
        
        Args:
            command_name (str): The name of the command.
            handler_fn (callable): The function to execute.
            action_type (ActionType): The type of action required for authorization.
        """
        self._registry[command_name] = {
            "handler": handler_fn,
            "action_type": action_type
        }

    def dispatch(self, command_name, params, source, user):
        """
        Dispatches a command through the authorization and execution pipeline.
        
        Args:
            command_name (str): The name of the command to dispatch.
            params (dict): Parameters for the command.
            source (str): Source identifier (e.g., IP, Service).
            user (str): User identifier.
            
        Returns:
            dict: {'status': 'success'|'error', 'data': ...|'message': ...}
        """
        # 1. Lookup handler and ActionType
        if command_name not in self._registry:
            error_msg = f"Unknown command: {command_name}"
            self._audit_log(command_name, source, user, status="fail", reason=error_msg)
            return {"status": "error", "message": error_msg}

        handler_info = self._registry[command_name]
        handler_fn = handler_info["handler"]
        action_type = handler_info["action_type"]

        # 2. Construct context for AgentGuard
        context = {
            "source": source,
            "user": user,
            "params": params
        }

        # 3. Authorization Check
        try:
            is_allowed = check_permission(action_type, RiskLevel.HIGH, context)
        except Exception as e:
            # If the guard system is unavailable, treat as denial for safety
            is_allowed = False
            error_msg = f"Authorization system error: {str(e)}"

        if not is_allowed:
            error_msg = "Authorization denied"
            self._audit_log(command_name, source, user, status="fail", reason=error_msg)
            return {"status": "error", "message": error_msg}

        # 4. Execution
        try:
            result = handler_fn(params)
            self._audit_log(command_name, source, user, status="success", result=result)
            return {"status": "success", "data": result}
        except Exception as e:
            error_msg = f"Execution failed: {str(e)}"
            self._audit_log(command_name, source, user, status="fail", reason=error_msg)
            return {"status": "error", "message": error_msg}

    def _audit_log(self, command_name, source, user, status, reason=None, result=None):
        """
        Internal helper to format and persist audit logs to memory/command_audit.json
        """
        audit_entry = {
            "command": command_name,
            "source": source,
            "user": user,
            "status": status,
            "timestamp": datetime.datetime.utcnow().isoformat()
        }
        
        if reason:
            audit_entry["reason"] = reason
        if result is not None:
            audit_entry["result"] = result
            
        log_audit(audit_entry)
