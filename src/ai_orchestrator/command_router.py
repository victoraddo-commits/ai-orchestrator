from ai_orchestrator.discovery_service import verify_kai_context
from ai_orchestrator.load_handler import handle_load_command

def initialize_orchestrator(project_root):
    # Verify Kai context and get manifest
    manifest = verify_kai_context(project_root)
    
    # Inject manifest into system prompt
    system_prompt = f"Kai Discovery Manifest: {json.dumps(manifest)}"
    
    # Add LOAD command handler to router
    command_router = {
        'LOAD': handle_load_command
    }
    
    return system_prompt, command_router

def handle_load_command(command, context):
    # Extract file path from command
    file_path = command.split(' ')[1]
    
    # Sanitize path to prevent directory traversal
    if not os.path.abspath(file_path).startswith(os.path.abspath(context['project_root'])):
        return "Error: Invalid file path"
    
    # Read file content
    with open(file_path, 'r') as file:
        content = file.read()
    
    return content
