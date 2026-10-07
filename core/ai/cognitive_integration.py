"""Integration layer between cognitive router and existing AI router.

Wraps the cognitive router to work with the existing ai_router.py infrastructure.
"""

from typing import Optional, Dict, Any
from core.ai.cognitive_router import route_task, get_model_for_role
from core.llm_clients import call_ollama
from core.logger import info


def cognitive_delegate(prompt: str,
                      task_type: Optional[str] = None,
                      cognitive_role: Optional[str] = None,
                      timeout: int = 120,
                      **kwargs) -> str:
    """Route a task through the cognitive model router.

    Args:
        prompt: Task description/prompt
        task_type: Legacy task type (optional, for compatibility)
        cognitive_role: Override auto-classification (reasoning, coding, fast, etc.)
        timeout: Request timeout in seconds
        **kwargs: Additional parameters (ignored for compatibility)

    Returns:
        Model response string
    """
    # Route through cognitive system
    routing = route_task(prompt, task_type, cognitive_role)

    model_name = routing["model"]
    role = routing["role"]

    info(f"Cognitive delegate: {role} → {model_name}")
    info(f"  Prompt: {prompt[:100]}...")

    # Call Ollama with the selected model
    try:
        response = call_ollama(
            prompt=prompt,
            model=model_name,
            timeout=timeout
        )

        info(f"Cognitive delegate: success ({len(response)} chars)")
        return response

    except Exception as e:
        info(f"Cognitive delegate: {model_name} failed: {e}")

        # Fallback to default local model (qwen3-coder:kai)
        if model_name != "qwen3-coder:kai":
            info("Cognitive delegate: falling back to default local model")
            response = call_ollama(
                prompt=prompt,
                timeout=timeout
            )
            return response
        raise


def is_cognitive_routing_enabled() -> bool:
    """Check if cognitive routing is enabled.

    Can be controlled via environment variable or config file.
    """
    import os
    return os.environ.get("KAI_COGNITIVE_ROUTING", "1") == "1"
