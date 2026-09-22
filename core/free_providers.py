"""Phase 17M: Free-tier provider status — read-only view of the local fabric.

Owner directive: zero third-party providers. The model fabric is local-only,
so every registered provider is self-hosted and free ($0/token). This module
no longer registers anything — it only reports the free (local) providers.
"""

FREE_PROVIDER_CHECKLIST = {
    "genuinely_free": True,  # No credit card, no trial expiry
    "rate_limit_known": True,  # Rate limits documented
    "basic_quality_pass": True,  # Produces coherent output for basic tasks
    "api_key_available": True,  # Operator has a valid key
}


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
        "rule": "Local-only fabric: every provider is self-hosted and free. No third-party providers are registered.",
    }
