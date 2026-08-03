"""Command handlers for Juris Kai Legal Assistant

This module contains command handlers for the juris_kai legal assistant, 
following the same security constraints as law_tutor - no imports of 
operational capabilities.
"""

import re
from core.juris_kai.prompt import build_prompt
from core.memory import save

# Import only safe modules (no operational capabilities)
from core.juris_kai.session import get_user_session

# Import only text_task capable providers (no coding agents)
from core.ai.ai_router import delegate

# Response handling pattern similar to law_tutor
def handle_command(text, update):
    """Process legal assistant commands."""
    
    try:
        # Extract command and arguments
        parts = text.strip().split(' ', 1)
        command = parts[0].lstrip('/')
        args = parts[1] if len(parts) > 1 else ""
        
        # Process commands
        if command in ['help', 'start']:
            return handle_help()
        elif command == 'learn':
            return handle_learn(args, update)
        elif command == 'case':
            return handle_case(args, update)
        elif command == 'research':
            return handle_research(args, update)
        elif command == 'argument':
            return handle_argument(args, update)
        elif command == 'flashcards':
            return handle_flashcards(args, update)
        elif command == 'progress':
            return handle_progress(update)
        else:
            return f"Unknown command: {command}. Use /help for available commands."
            
    except Exception as e:
        # Return graceful error responses (same as law_tutor)
        return f"Error processing command: {str(e)}"

def handle_help():
    """Return help text."""
    from core.juris_kai.bot import HELP_TEXT
    return HELP_TEXT

def handle_learn(topic, update):
    """Learn about a legal topic."""
    if not topic.strip():
        return "Usage: /learn <legal topic>\nExample: /learn contract law"
    
    # Build the prompt for legal teaching
    prompt = build_prompt("legal_teaching", topic)
    
    # Delegate to AI router with text_task capability (same method as law_tutor)
    try:
        result = delegate(prompt, task_type="juris_legal_teaching", capability="text_task")
        return result["response"]
    except Exception as e:
        return f"Unable to provide legal teaching: {str(e)}"

def handle_case(case_name, update):
    """Analyze a legal case."""
    if not case_name.strip():
        return "Usage: /case <case name>\nExample: /case Donoghue v Stevenson"
    
    # Build the prompt for case analysis
    prompt = build_prompt("legal_case_analysis", case_name)
    
    # Delegate to AI router with text_task capability (same method as law_tutor)
    try:
        result = delegate(prompt, task_type="juris_case_analysis", capability="text_task")
        return result["response"]
    except Exception as e:
        return f"Unable to analyze case: {str(e)}"

def handle_research(query, update):
    """Research legal concepts."""
    if not query.strip():
        return "Usage: /research <legal query>\nExample: /research duty of care evolution"
    
    # Build the prompt for legal research
    prompt = build_prompt("legal_research", query)
    
    # Delegate to AI router with text_task capability (same method as law_tutor)
    try:
        result = delegate(prompt, task_type="juris_research", capability="text_task")
        return result["response"]
    except Exception as e:
        return f"Unable to research legal concepts: {str(e)}"

def handle_argument(topic, update):
    """Construct legal arguments."""
    if not topic.strip():
        return "Usage: /argument <legal topic>\nExample: /argument strict liability"
    
    # Build the prompt for argument construction
    prompt = build_prompt("legal_argument", topic)
    
    # Delegate to AI router with text_task capability (same method as law_tutor)
    try:
        result = delegate(prompt, task_type="juris_argument_construction", capability="text_task")
        return result["response"]
    except Exception as e:
        return f"Unable to construct legal argument: {str(e)}"

def handle_flashcards(topic, update):
    """Generate legal flashcards."""
    if not topic.strip():
        return "Usage: /flashcards <legal topic>\nExample: /flashcards offer and acceptance"
    
    # Build the prompt for flashcard generation
    prompt = build_prompt("legal_flashcards", topic)
    
    # Delegate to AI router with text_task capability (same method as law_tutor)
    try:
        result = delegate(prompt, task_type="juris_flashcards", capability="text_task")
        return result["response"]
    except Exception as e:
        return f"Unable to generate flashcards: {str(e)}"

def handle_progress(update):
    """Show learning progress."""
    session = get_user_session(update.get("chat_id", ""))
    topics_studied = session.get("topics_studied", [])
    
    if not topics_studied:
        return "No topics studied yet. Start learning with /learn <topic>"
    
    return f"Topics studied:\n{chr(10).join('- ' + topic for topic in topics_studied)}"

# Same security constraint pattern as law_tutor - no operational imports
# This module must never import core.build_manager, core.approval, or core.deployment_manager