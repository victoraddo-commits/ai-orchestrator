"""Guarded tool execution - AgentGuard integration wrapper."""

import subprocess
from typing import Dict, Any, Optional
from .guard import AgentGuard, ActionRequest, ActionType, Decision

# Global guard instance
_guard = AgentGuard({"enabled": True})


def execute_shell_command(
    agent_id: str,
    user_id: str,
    command: str,
    reason: Optional[str] = None
) -> Dict[str, Any]:
    """
    Guarded shell execution.
    All shell commands flow through AgentGuard.
    """
    request = ActionRequest(
        agent_id=agent_id,
        user_id=user_id,
        action_type=ActionType.EXECUTE,
        resource="shell",
        details=command,
        reason=reason
    )

    result = _guard.check_action(request)

    if result.decision == Decision.DENY:
        return {
            "success": False,
            "blocked": True,
            "error": "Action denied by AgentGuard",
            "message": result.message,
            "audit_id": result.audit_event_id,
            "risk_level": result.risk_level.value
        }

    if result.decision == Decision.REQUIRE_APPROVAL:
        return {
            "success": False,
            "blocked": True,
            "approval_required": True,
            "approval_id": result.approval_request_id,
            "message": result.message,
            "audit_id": result.audit_event_id,
            "risk_level": result.risk_level.value
        }

    # ALLOW - execute command (safely, without shell=True)
    try:
        # Parse command into list (basic splitting - production needs proper parser)
        cmd_parts = command.split()

        output = subprocess.check_output(
            cmd_parts,
            text=True,
            timeout=30,
            stderr=subprocess.STDOUT
        )
        return {
            "success": True,
            "output": output,
            "audit_id": result.audit_event_id,
            "risk_level": result.risk_level.value
        }
    except subprocess.CalledProcessError as e:
        return {
            "success": False,
            "error": f"Command failed with exit code {e.returncode}",
            "output": e.output if e.output else "",
            "audit_id": result.audit_event_id
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "audit_id": result.audit_event_id
        }


def write_file_guarded(
    agent_id: str,
    user_id: str,
    file_path: str,
    content: str,
    reason: Optional[str] = None
) -> Dict[str, Any]:
    """Guarded file write operation."""
    request = ActionRequest(
        agent_id=agent_id,
        user_id=user_id,
        action_type=ActionType.WRITE,
        resource=file_path,
        details=f"Write {len(content)} bytes to {file_path}",
        reason=reason
    )

    result = _guard.check_action(request)

    if result.decision != Decision.ALLOW:
        return {
            "success": False,
            "blocked": True,
            "approval_required": (result.decision == Decision.REQUIRE_APPROVAL),
            "approval_id": result.approval_request_id,
            "message": result.message,
            "audit_id": result.audit_event_id
        }

    # ALLOW - write file
    try:
        with open(file_path, 'w') as f:
            f.write(content)
        return {
            "success": True,
            "message": f"Wrote {len(content)} bytes to {file_path}",
            "audit_id": result.audit_event_id
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "audit_id": result.audit_event_id
        }
