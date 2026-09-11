import json
from typing import Callable, Dict, Any

from core.agentguard.guard import ActionType, RiskLevel, check_permission
from core.memory import log_audit

class CommandBus:
    def __init__(self):
        self.handlers: Dict[str, Dict[str, Any]] = {}

    def register_handler(self, command_name: str, handler_fn: Callable, required_permissions: List[str]):
        self.handlers[command_name] = {
            'handler_fn': handler_fn,
            'action_type': ActionType.EXECUTE
        }

    def dispatch(self, command_name: str, params: Dict[str, Any], source: str, user: str) -> Any:
        if command_name not in self.handlers:
            log_audit(command_name, source, user, 'dispatch', 'fail', 'Command not found')
            return {'error': 'Command not found'}

        handler_info = self.handlers[command_name]
        handler_fn = handler_info['handler_fn']
        action_type = handler_info['action_type']

        context = {
            'source': source,
            'user': user
        }

        if not check_permission(action_type, RiskLevel.HIGH, context):
            log_audit(command_name, source, user, 'dispatch', 'fail', 'Permission denied')
            return {'error': 'Permission denied'}

        try:
            result = handler_fn(params)
            log_audit(command_name, source, user, 'dispatch', 'success', 'Command executed successfully')
            return result
        except Exception as e:
            log_audit(command_name, source, user, 'dispatch', 'fail', f'Command execution failed: {str(e)}')
            return {'error': str(e)}

# Example usage
if __name__ == "__main__":
    def example_handler(params):
        return {'message': 'Hello, World!'}

    command_bus = CommandBus()
    command_bus.register_handler('example_command', example_handler, ['admin'])

    result = command_bus.dispatch('example_command', {}, 'source1', 'user1')
    print(result)
