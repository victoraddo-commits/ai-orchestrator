import logging
import json
from typing import Callable, Dict, Any, Optional
from core.agentguard import guard, ActionType, RiskLevel
from core.memory import log_audit

class CommandBus:
    def __init__(self):
        self.handlers: Dict[str, Dict[str, Callable]] = {}
        self.register_default_handlers()

    def register_handler(self, command_name: str, handler_fn: Callable, required_permissions: Optional[str] = None):
        self.handlers[command_name] = {
            'handler_fn': handler_fn,
            'action_type': required_permissions
        }

    def dispatch(self, command_name: str, params: Dict[str, Any], source: str, user: str) -> Any:
        if command_name not in self.handlers:
            return {'error': 'Command not found'}

        handler_info = self.handlers[command_name]
        handler_fn = handler_info['handler_fn']
        action_type = handler_info['action_type']

        context = {
            'source': source,
            'user': user
        }

        try:
            if action_type:
                if not guard.check_permission(action_type, RiskLevel.HIGH, context):
                    log_audit(command_name, source, user, 'fail', 'Permission denied')
                    return {'error': 'Permission denied'}

            result = handler_fn(params)
            log_audit(command_name, source, user, 'success', 'Command executed successfully')
            return result
        except Exception as e:
            log_audit(command_name, source, user, 'fail', f'Command execution failed: {str(e)}')
            return {'error': str(e)}

    def register_default_handlers(self):
        from core.build_manager import create_build
        from core.kai_missions import create_mission
        from core.ai_provider import list_providers

        self.register_handler('build.create', create_build, ActionType.CREATE_BUILD)
        self.register_handler('build.approve', lambda params: approve_architecture(params), ActionType.APPROVE_ARCHITECTURE)
        self.register_handler('mission.create', create_mission, ActionType.CREATE_MISSION)
        self.register_handler('provider.list', list_providers, ActionType.LIST_PROVIDERS)

def approve_architecture(params: Dict[str, Any]) -> Any:
    # Placeholder for actual approval logic
    return {'status': 'approved', 'params': params}
