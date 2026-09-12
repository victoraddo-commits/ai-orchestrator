from ai_orchestrator.command_router import initialize_orchestrator

def main():
    project_root = 'path/to/project'
    system_prompt, command_router = initialize_orchestrator(project_root)
    
    # Simulate user input and command execution
    user_input = 'LOAD path/to/CLAUDE.md'
    command = user_input.split(' ')[0]
    if command in command_router:
        result = command_router[command](user_input, {'project_root': project_root})
        print(result)
    else:
        print("Unknown command")

if __name__ == "__main__":
    main()
