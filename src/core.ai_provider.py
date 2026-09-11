from core.ai_provider import register_provider
from core.ai.ai_router import ROLE_PROVIDERS

# Registering the new providers
register_provider(
    model_id="opencode/claude-fable-5",
    env_var="CLAUDE_FABLE5_OPENCODE_ZEN_API_KEY",
    provider_class="core.ai.providers.scoped_opencode_runner.ScopedOpenCodeRunner"
)

register_provider(
    model_id="opencode/gemini-3.1-pro",
    env_var="GEMINI_3_1_PRO_OPENCODE_ZEN_API_KEY",
    provider_class="core.ai.providers.scoped_opencode_runner.ScopedOpenCodeRunner"
)

# Adding the new providers to the AI Router with highest priority
ROLE_PROVIDERS['coding'].insert(0, "opencode/claude-fable-5")
ROLE_PROVIDERS['coding'].insert(0, "opencode/gemini-3.1-pro")
ROLE_PROVIDERS['debugging'].insert(0, "opencode/claude-fable-5")
ROLE_PROVIDERS['debugging'].insert(0, "opencode/gemini-3.1-pro")
