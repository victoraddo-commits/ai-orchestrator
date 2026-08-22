"""Kai Betting — serverless AI inference layer (GPU.ai).

Three on-demand inference tiers routed through a single provider client:

    Tier 0  Kai statistical engine (core.kai_betting.prediction_engine) — local.
    Tier 1  Qwen 3.7 Plus   — high-volume screening.
    Tier 2  DeepSeek V4 Pro — independent adversarial review.
    Tier 3  Kimi K3         — premium final adjudication (rare).

GPU.ai is a pay-per-use HTTP dependency: no persistent process, no idle GPU,
no background inference loop. See the module docstrings for the contract.
"""

from core.kai_betting.ai.client import GPUAIClient
from core.kai_betting.ai.prompts import (
    PROMPT_VERSION,
    MODELS,
    QWEN_SCREENING_SCHEMA,
    DEEPSEEK_ADVERSARIAL_SCHEMA,
    K3_ADJUDICATION_SCHEMA,
)
from core.kai_betting.ai.router import BettingAIRouter, AIRoutingResult

__all__ = [
    "GPUAIClient",
    "PROMPT_VERSION",
    "MODELS",
    "QWEN_SCREENING_SCHEMA",
    "DEEPSEEK_ADVERSARIAL_SCHEMA",
    "K3_ADJUDICATION_SCHEMA",
    "BettingAIRouter",
    "AIRoutingResult",
]
