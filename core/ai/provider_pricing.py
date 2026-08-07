"""Phase 13L-1: Provider/model pricing data for cost-efficiency scoring.

Stores per-model pricing in USD per million tokens. Used by the performance
router to compute cost-per-call for composite scoring.

Providers/models not listed here are given a neutral cost score so cost
efficiency never penalizes a provider just because its pricing is unknown.
"""

PRICING = {
    "qwen4_text": {
        "Qwen/Qwen3-32B-FP8": {
            "description": "Self-hosted RunPod RTX PRO 6000, $0.99/hr GPU",
            "input_per_million": 0.05,
            "output_per_million": 0.05,
        },
    },
    "qwen4_pod_b": {
        "Qwen/Qwen3-32B-FP8": {
            "description": "Self-hosted RunPod RTX PRO 6000, $0.99/hr GPU",
            "input_per_million": 0.05,
            "output_per_million": 0.05,
        },
    },
    "gemini": {
        "gemini-flash-lite-latest": {
            "input_per_million": 0.00,
            "output_per_million": 0.00,
        },
    },
    "geminix": {
        "gemini-flash-lite-latest": {
            "input_per_million": 0.00,
            "output_per_million": 0.00,
        },
    },
    "groq": {
        "llama-3.3-70b-versatile": {
            "input_per_million": 0.00,
            "output_per_million": 0.00,
        },
    },
    "deepseek_native_flash": {
        "deepseek-v4-flash": {
            "input_per_million": 0.14,
            "output_per_million": 0.28,
        },
    },
    "deepseek_native_pro": {
        "deepseek-v4-pro": {
            "input_per_million": 0.42,
            "output_per_million": 0.84,
        },
    },
    "deepseek": {
        "deepseek/deepseek-v4-pro": {
            "input_per_million": 0.42,
            "output_per_million": 0.84,
        },
    },
    "openai": {
        "gpt-4o-mini": {
            "input_per_million": 0.15,
            "output_per_million": 0.60,
        },
    },
    "claude": {
        "claude": {
            "description": "Direct Anthropic subscription via CloudCLI",
            "input_per_million": 3.00,
            "output_per_million": 15.00,
        },
    },
}


def get_pricing(provider_name, model_name=None):
    """Return pricing dict for a given provider/model, or None if unknown."""
    provider_prices = PRICING.get(provider_name)
    if not provider_prices:
        return None
    if model_name and model_name in provider_prices:
        return provider_prices[model_name]
    return next(iter(provider_prices.values())) if provider_prices else None


def compute_cost(provider_name, model_name, prompt_tokens, completion_tokens):
    """Compute the USD cost for a call, or None if pricing is unknown."""
    pricing = get_pricing(provider_name, model_name)
    if pricing is None:
        return None
    cost = (prompt_tokens / 1_000_000) * pricing["input_per_million"]
    cost += (completion_tokens / 1_000_000) * pricing["output_per_million"]
    return round(cost, 8)
