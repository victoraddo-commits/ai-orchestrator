"""Phase 13L-1: Provider/model pricing data for cost-efficiency scoring.

Stores per-model pricing in USD per million tokens. Used by the performance
router to compute cost-per-call for composite scoring.

Providers/models not listed here are given a neutral cost score so cost
efficiency never penalizes a provider just because its pricing is unknown.
"""

# Local-only model fabric: every registered provider is self-hosted, so the
# cost of every reachable route is genuinely $0/token. Cloud/third-party
# entries were removed per owner directive (zero third-party providers); an
# unknown provider simply gets a neutral cost score (get_pricing returns
# None), which never penalizes it.
PRICING = {
    "local": {
        "qwen3-coder:kai": {"input_per_million": 0.00, "output_per_million": 0.00},
    },
    "kai_brain": {
        "qwen3-coder:kai": {"input_per_million": 0.00, "output_per_million": 0.00},
    },
    "kai_coder": {
        "qwen3-coder:kai": {"input_per_million": 0.00, "output_per_million": 0.00},
    },
    "kai_deep": {
        "qwen3-coder:kai": {"input_per_million": 0.00, "output_per_million": 0.00},
    },
    "llama_coder_cpu": {
        "Qwen2.5-Coder-7B-Instruct-Q4_K_M.gguf": {
            "input_per_million": 0.00,
            "output_per_million": 0.00,
        },
    },
    "llama3": {
        "llama3.2:3b": {"input_per_million": 0.00, "output_per_million": 0.00},
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
