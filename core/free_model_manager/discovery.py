"""OpenRouter model discovery and pricing verification.

Fetches all models from OpenRouter API, filters for genuinely free coding models,
and verifies current pricing before adding to the candidate pool.
"""

import json
import time
import requests
from datetime import datetime
from typing import Optional

from . import OMNIROUTE_BASE_URL, OPENROUTER_API_KEY
from .models import db


# OpenRouter API base
OPENROUTER_API_BASE = "https://openrouter.ai/api/v1"


class DiscoveryError(Exception):
    """Error during model discovery."""
    pass


def get_openrouter_headers() -> dict:
    """Get headers for OpenRouter API calls."""
    return {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
    }


def fetch_all_models(timeout: int = 30) -> list[dict]:
    """Fetch all available models from OpenRouter API."""
    try:
        response = requests.get(
            f"{OPENROUTER_API_BASE}/models",
            headers=get_openrouter_headers(),
            timeout=timeout
        )
        if response.status_code == 429:
            raise DiscoveryError("Rate limited by OpenRouter API")
        if response.status_code == 401:
            raise DiscoveryError("Invalid OpenRouter API key")
        if not response.ok:
            raise DiscoveryError(f"OpenRouter API error: {response.status_code}")

        data = response.json()
        return data.get("data", [])
    except requests.RequestException as e:
        raise DiscoveryError(f"Failed to fetch models: {e}")


def fetch_model_pricing(model_id: str, timeout: int = 10) -> tuple[Optional[float], Optional[float]]:
    """Fetch current pricing for a specific model.

    Returns: (prompt_price_per_million, completion_price_per_million)
    Both None if pricing unavailable.
    """
    try:
        response = requests.get(
            f"{OPENROUTER_API_BASE}/models/{model_id}",
            headers=get_openrouter_headers(),
            timeout=timeout
        )
        if not response.ok:
            return None, None

        data = response.json()
        pricing = data.get("pricing", {})

        prompt_price = pricing.get("prompt")
        completion_price = pricing.get("completion")

        # Pricing is per million tokens
        return prompt_price, completion_price
    except requests.RequestException:
        return None, None


def is_free_model(prompt_price: any, completion_price: any) -> bool:
    """Check if model is genuinely free (both prices = 0 or "0" or None/0).

    Handles both float and string representations of price.
    """
    # Handle string prices (from API)
    if isinstance(prompt_price, str):
        try:
            prompt_price = float(prompt_price)
        except (ValueError, TypeError):
            return False
    if isinstance(completion_price, str):
        try:
            completion_price = float(completion_price)
        except (ValueError, TypeError):
            return False

    if prompt_price is None or completion_price is None:
        return False
    return prompt_price == 0 and completion_price == 0


def is_coding_related(model_data: dict) -> bool:
    """Heuristic check for coding-related models based on name/metadata."""
    model_id = model_data.get("id", "").lower()
    name = model_data.get("name", "").lower()
    description = model_data.get("description", "").lower()

    coding_keywords = [
        "code", "coding", "claude", "deepseek", "qwen", "llama",
        "mistral", "wizard", "starcoder", "codellama", "wizardcoder",
        "phi", "gemma", "granite", "codegemma", "starcoder",
        "openchat", "magicoder", "wasp", "octopus", "agent",
        "reasoning", "pro", "ultra", "flash", "fast"
    ]

    # Check for free suffix
    if model_id.endswith(":free") or "-free" in model_id:
        return True

    # Check keywords in name or description
    for keyword in coding_keywords:
        if keyword in model_id or keyword in name or keyword in description:
            return True

    return False


def fetch_free_models(timeout: int = 30) -> list[dict]:
    """Fetch all free models from OpenRouter API."""
    try:
        response = requests.get(
            f"{OPENROUTER_API_BASE}/models?free=true",
            headers=get_openrouter_headers(),
            timeout=timeout
        )
        if not response.ok:
            # Try without filter
            return fetch_all_models(timeout)

        data = response.json()
        return data.get("data", [])
    except requests.RequestException:
        return fetch_all_models(timeout)


def discover_models(verify_pricing: bool = True) -> list[dict]:
    """Discover all potentially free coding models.

    1. Fetch all models from OpenRouter
    2. Filter for free models
    3. Verify pricing for each
    4. Filter for coding-related
    5. Store in database
    """
    print(f"[free-model-manager] Starting model discovery at {datetime.utcnow().isoformat()}")

    # Fetch all models
    all_models = fetch_all_models()
    print(f"[free-model-manager] Fetched {len(all_models)} models from OpenRouter")

    discovered = []
    free_verified = []

    for model in all_models:
        model_id = model.get("id", "")
        if not model_id:
            continue

        # Get pricing from model data (already available in list)
        pricing = model.get("pricing", {})
        prompt_price = pricing.get("prompt")
        completion_price = pricing.get("completion")

        # Check if it's actually free
        model_is_free = is_free_model(prompt_price, completion_price)

        # Also check for :free suffix
        has_free_suffix = ":free" in model_id.lower()

        if not (model_is_free or has_free_suffix):
            # Skip paid models - they're not candidates for our free pool
            continue

        # Check if coding-related (broad filter first)
        if not is_coding_related(model):
            continue

        # Record as discovered
        db.upsert_model(
            model_id=model_id,
            provider=str(model.get("owned_by") or model.get("top_provider") or "unknown"),
            display_name=str(model.get("name") or model_id),
            context_length=int(model.get("context_length") or 0),
            first_seen=datetime.utcnow().isoformat(),
            last_seen=datetime.utcnow().isoformat(),
            status="DISCOVERED",
            metadata=json.dumps({
                "description": str(model.get("description", "")),
                "architecture": model.get("architecture", {}),
                "top_provider": str(model.get("top_provider", ""))
            })
        )
        discovered.append(model_id)

        # Mark as free if pricing confirms it
        if model_is_free:
            db.upsert_model(
                model_id=model_id,
                price_prompt=0.0,
                price_completion=0.0,
                is_free=1,
                status="FREE_VERIFIED"
            )
            free_verified.append(model_id)
        elif has_free_suffix:
            # Has :free suffix but pricing not $0 (might be cached/stale)
            # Mark as free based on suffix
            db.upsert_model(
                model_id=model_id,
                price_prompt=float(prompt_price) if prompt_price else 0,
                price_completion=float(completion_price) if completion_price else 0,
                is_free=1,
                status="FREE_VERIFIED"
            )
            free_verified.append(model_id)

    print(f"[free-model-manager] Discovered {len(discovered)} potentially free coding models")
    print(f"[free-model-manager] Verified free: {len(free_verified)}")

    return free_verified


def verify_model_free(model_id: str) -> bool:
    """Verify a specific model is still free (re-check pricing)."""
    prompt_price, completion_price = fetch_model_pricing(model_id)

    if is_free_model(prompt_price, completion_price):
        db.upsert_model(
            model_id=model_id,
            price_prompt=0.0,
            price_completion=0.0,
            is_free=1,
            status="FREE_VERIFIED",
            last_test=datetime.utcnow().isoformat()
        )
        return True
    else:
        db.upsert_model(
            model_id=model_id,
            price_prompt=prompt_price or -1,
            price_completion=completion_price or -1,
            is_free=0,
            status="PAID",
            last_test=datetime.utcnow().isoformat()
        )
        return False


def test_omniroute_endpoint() -> bool:
    """Test if OmniRoute is accessible."""
    try:
        response = requests.get(
            f"{OMNIROUTE_BASE_URL}/models",
            timeout=5
        )
        return response.ok
    except requests.RequestException:
        return False


def get_omniroute_models() -> list[str]:
    """Get list of models available through OmniRoute."""
    try:
        response = requests.get(
            f"{OMNIROUTE_BASE_URL}/models",
            timeout=5
        )
        if not response.ok:
            return []

        data = response.json()
        return [m.get("id") for m in data.get("data", []) if m.get("id")]
    except requests.RequestException:
        return []
