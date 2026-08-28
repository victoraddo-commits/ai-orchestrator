"""Phase 13L-1: Provider/model pricing data for cost-efficiency scoring.

Stores per-model pricing in USD per million tokens. Used by the performance
router to compute cost-per-call for composite scoring.

Providers/models not listed here are given a neutral cost score so cost
efficiency never penalizes a provider just because its pricing is unknown.
"""

PRICING = {
    # 2026-08-07: qwen4_text/qwen4_pod_b removed — RunPod pods decommissioned.
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
    "claude": {
        "claude": {
            "description": "Direct Anthropic subscription via CloudCLI",
            "input_per_million": 3.00,
            "output_per_million": 15.00,
        },
    },
    # 2026-08-26: entries below close the calls_unknown gap -- every
    # provider the router can actually reach now has a pricing row, so
    # cost_tracker estimation never silently lands at $0/unknown.
    # local models are free (self-hosted ollama on Proxmox B).
    "local": {
        "qwen2.5:7b": {"input_per_million": 0.00, "output_per_million": 0.00},
    },
    # 2026-08-28: free_coding routes through the local free_model_manager
    # (ollama on localhost:20100) — self-hosted, no per-token cost.
    "free_coding": {
        "local-ollama": {"input_per_million": 0.00, "output_per_million": 0.00},
    },
    "llama3": {
        "llama3.2:3b": {"input_per_million": 0.00, "output_per_million": 0.00},
    },
    # OpenRouter bills per-model; the rotation list spans cheap paid models,
    # so these are documented approximations of each model's published rate.
    # gpt-4o-mini is exact ($0.15/$0.60 per M); deepseek-v4-flash/pro match
    # the native DeepSeek rates already listed above; z-ai/glm-5 and gpt-5
    # use conservative ballpark rates until a real invoice pins them down.
    "openrouter": {
        "openai/gpt-4o-mini": {"input_per_million": 0.15, "output_per_million": 0.60},
        "deepseek/deepseek-v4-flash": {"input_per_million": 0.14, "output_per_million": 0.28},
        "deepseek/deepseek-v4-pro": {"input_per_million": 0.42, "output_per_million": 0.84},
        "z-ai/glm-5": {"input_per_million": 0.50, "output_per_million": 2.00},
        "openai/gpt-5": {"input_per_million": 1.25, "output_per_million": 10.00},
    },
    # OmniRoute aggregates upstreams behind auto/ routes; best-fast typically
    # resolves to minimax-m2.5-class mid-tier models. Ballpark approximation
    # -- omniroute's own gateway logs remain the authoritative spend source.
    "omniroute": {
        "auto/best-fast": {"input_per_million": 0.30, "output_per_million": 1.20},
    },
    "gpuai_minimax": {
        "gpuai/minimax-m3": {"input_per_million": 0.30, "output_per_million": 1.20},
    },
    "minimax": {
        "MiniMax-M2": {"input_per_million": 0.30, "output_per_million": 1.20},
    },
    # GPU.ai serverless Gemma 4 31B -- mid-tier open-weight rates.
    "gpuai_gemma": {
        "gpuai/gemma-4-31b-it": {"input_per_million": 0.20, "output_per_million": 0.80},
    },
    # OmniRoute-named DeepSeek slots -- same published DeepSeek V4 Flash
    # rates as deepseek_native_flash (the gateway just re-bills upstream).
    "omniroute_deepseek_flash": {
        "ds/deepseek-v4-flash": {"input_per_million": 0.14, "output_per_million": 0.28},
    },
    # Coding route through OmniRoute's auto backend -- same ballpark as the
    # generic omniroute row until gateway logs pin the actual upstream mix.
    "omniroute_deepseek_coding": {
        "auto/coding": {"input_per_million": 0.30, "output_per_million": 1.20},
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
