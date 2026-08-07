# Direct Qwen3 Routing Override
# Installed by direct_qwen3_router.py
# Simple monkey-patch that avoids circular imports

import sys
from pathlib import Path
sys.path.insert(0, str(Path('/project/ai-orchestrator').resolve()))

# Store original functions (will be patched later)
_original_delegate = None
_original_chat = None

class Qwen3DirectRouter:
    """Simple direct router for Qwen3 GPU tasks"""

    def __init__(self):
        self.max_concurrent = 2
        self.active_tasks = 0

    def route_to_qwen3(self, prompt, task_type=None, project_path=None):
        """Route task directly to Qwen3 GPU"""
        if task_type == 'coding':
            print(f"🔀 DIRECT QWEN3: Coding task routed to GPU")
            # For now, just log - actual routing happens in patched functions
            return True
        return False

# Global router instance
_qwen3_router = Qwen3DirectRouter()

def install_qwen3_overrides():
    """Install Qwen3 routing overrides - called after imports are complete"""
    global _original_delegate, _original_chat

    try:
        # Import here to avoid circular import
        from core.ai.ai_router import delegate, chat

        # Store originals
        _original_delegate = delegate
        _original_chat = chat

        # Create override functions
        def qwen3_delegate(prompt, task_type=None, project_path=None, timeout=None, capability=None, **kwargs):
            """Delegate with Qwen3 priority"""

            # Route coding tasks to Qwen3
            if task_type == 'coding' or (capability and 'coding' in capability):
                print(f"🎯 QWEN3 GPU ROUTING: task_type={task_type}")

                # Try to use Qwen3 first
                from core.ai_provider import get_provider
                qwen3_provider = get_provider('qwen3_coding')

                if qwen3_provider and qwen3_provider.get('available_fn')():
                    print(f"✅ Using Qwen3 GPU for coding task")
                    # Let the original delegate handle it - Qwen3 is already first in routing list

            # Call original delegate
            return _original_delegate(prompt, task_type, project_path, timeout, capability, **kwargs)

        def qwen3_chat(prompt, **kwargs):
            """Chat with Qwen3 priority"""
            print(f"💬 QWEN3 CHAT ROUTING")
            # Use original chat - Qwen3 routing is configured in ai_router
            return _original_chat(prompt, **kwargs)

        # Apply patches
        import core.ai.ai_router as ai_router_module
        ai_router_module.delegate = qwen3_delegate
        ai_router_module.chat = qwen3_chat

        print("✅ Qwen3 routing overrides installed")
        return True

    except Exception as e:
        print(f"❌ Failed to install Qwen3 overrides: {e}")
        return False

# Install overrides after a short delay to avoid circular imports
import threading
import time

def delayed_install():
    time.sleep(0.5)
    install_qwen3_overrides()

# Start installation in background
thread = threading.Thread(target=delayed_install, daemon=True)
thread.start()

print("🔄 Qwen3 routing override loading...")
