"""Phase 17M: Free-tier provider expansion — evaluation checklist and
registry for free-tier AI providers used as fallback capacity.

Never displaces top-priority providers. Adds genuinely free providers
after passing a documented evaluation checklist.
"""

import os

FREE_PROVIDER_CHECKLIST = {
    "genuinely_free": True,  # No credit card, no trial expiry
    "rate_limit_known": True,  # Rate limits documented
    "basic_quality_pass": True,  # Produces coherent output for basic tasks
    "api_key_available": True,  # Operator has a valid key
}


def evaluate_free_provider(name, test_fn):
    """Run the evaluation checklist against a candidate free-tier provider.
    Returns (passed: bool, issues: list[str])."""
    issues = []

    # Check for API key env var
    key_var = f"{name.upper()}_API_KEY"
    if not os.environ.get(key_var):
        issues.append(f"{key_var} not set — provider unavailable")

    # Basic quality check
    try:
        result = test_fn("Say 'ok' in one word.", timeout=10)
        if not result or len(result.strip()) < 1:
            issues.append("empty response on basic test")
    except Exception as e:
        issues.append(f"basic quality test failed: {e}")

    return len(issues) == 0, issues


def get_free_provider_status():
    """Return the status of all currently registered free-tier providers."""
    from core.ai_provider import list_providers

    providers = list_providers()
    free_providers = {}

    for name, info in providers.items():
        if info.get("cost_tier") == "free":
            free_providers[name] = {
                "available": info.get("available", False),
                "enabled": info.get("enabled", True),
                "description": info.get("description", ""),
            }

    return {
        "free_providers": free_providers,
        "checklist": FREE_PROVIDER_CHECKLIST,
        "rule": "Free providers are placed AFTER all top-priority providers in ROLE_PROVIDERS. They are fallback capacity, not replacements.",
    }


def register_free_provider(name, run_text_task_fn, description):
    """Register a new free-tier provider if it passes evaluation.
    Returns the provider name on success, or raises on failure."""

    passed, issues = evaluate_free_provider(name, run_text_task_fn)
    if not passed:
        raise ValueError(f"Provider '{name}' failed evaluation: {'; '.join(issues)}")

    from core.ai_provider import register_provider
    from core.ai.credential_vault import retrieve_api_key

    def _available():
        # Try vault first (provider name = slug), then env var fallback
        return bool(retrieve_api_key(name) or os.environ.get(f"{name.upper()}_API_KEY"))

    register_provider(
        name,
        run_text_task=run_text_task_fn,
        available_fn=_available,
        kind="cloud",
        description=description,
        cost_tier="free",
    )

    return name
