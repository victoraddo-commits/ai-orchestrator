"""Kai Betting — versioned prompts, JSON schemas, and model catalog for the
three-tier GPU.ai inference layer.

Prompt versioning is mandatory for historical model evaluation: bump
PROMPT_VERSION whenever a prompt or schema changes, and every AI prediction
record retains the version it was produced under.
"""

from __future__ import annotations

PROMPT_VERSION = "BETTING_AI_PROMPT_V1.0"

# ── Model catalog (GPU.ai serverless) ─────────────────────────────────────────
# prices are USD per 1M tokens. max_output caps the (expensive) output length.
MODELS: dict = {
    "qwen": {
        "model_id": "gpuai/qwen3.7-plus",
        "name": "Qwen 3.7 Plus",
        "tier": 1,
        "input_price_per_mtok": 0.40,
        "output_price_per_mtok": 1.60,
        "max_output": 700,
    },
    "deepseek": {
        "model_id": "gpuai/deepseek-v4-pro",
        "name": "DeepSeek V4 Pro",
        "tier": 2,
        "input_price_per_mtok": 1.74,
        "output_price_per_mtok": 3.48,
        "max_output": 900,
    },
    "k3": {
        "model_id": "gpuai/kimi-k3",
        "name": "Kimi K3",
        "tier": 3,
        "input_price_per_mtok": 3.00,
        "output_price_per_mtok": 15.00,
        "max_output": 900,
    },
}

# ── JSON schemas (permissive keys; the client validates/coerces) ─────────────

QWEN_SCREENING_SCHEMA = {
    "prediction": {"market": str, "selection": str, "probability": float},
    "market_probability": float,
    "estimated_edge": float,
    "confidence": float,
    "risk_score": float,
    "value": str,  # none|low|moderate|high
    "contradictions": list,
    "red_flags": list,
    "deep_review": bool,
}

DEEPSEEK_ADVERSARIAL_SCHEMA = {
    "prediction": {"market": str, "selection": str, "probability": float},
    "market_probability": float,
    "estimated_edge": float,
    "confidence": float,
    "risk_score": float,
    "agreement_with_qwen": bool,
    "agreement_with_kai": bool,
    "contradictions": list,
    "red_flags": list,
    "needs_k3": bool,
}

K3_ADJUDICATION_SCHEMA = {
    "final_decision": str,  # BET|PASS
    "market": str,
    "selection": str,
    "probability": float,
    "market_probability": float,
    "estimated_edge": float,
    "confidence": float,
    "risk_score": float,
    "model_consensus": {"kai": float, "qwen": float, "deepseek": float, "k3": float},
    "strongest_argument_for": str,
    "strongest_argument_against": str,
    "key_reasons": list,
    "risk_flags": list,
    "contradictions": list,
    "final_recommendation": str,  # BET|PASS
}


def _json_block(schema_description: str) -> str:
    return (
        "Respond with ONLY a JSON object (no markdown fences, no prose). "
        f"The object must conform to: {schema_description}"
    )


# ── Tier prompts ─────────────────────────────────────────────────────────────

QWEN_SCREENING_PROMPT = """You are a sports-betting SCOUT performing a first-pass screen. You do NOT make the final betting decision.

You receive a normalized evidence package for one candidate selection that has already passed Kai's statistical screening. Your job is to judge whether it deserves deeper analysis.

Steps:
1. Compare Kai's statistical probability against the market implied probability.
2. Identify evidence that supports the selection.
3. Identify evidence that contradicts it.
4. Flag hidden risks (injuries, rotation, schedule, motivation, market anomalies).
5. Estimate your own probability and confidence.
6. Decide whether deeper (Tier 2) analysis is justified via deep_review.

Be concise and quantitative. Do not invent facts not present in the input. Probability is a 0-1 float; confidence and risk_score are 0-100 floats.
""" + _json_block(
    '{"prediction": {"market": str, "selection": str, "probability": float}, '
    '"market_probability": float, "estimated_edge": float, "confidence": float, '
    '"risk_score": float, "value": "none|low|moderate|high", '
    '"contradictions": [str], "red_flags": [str], "deep_review": bool}'
)


DEEPSEEK_ADVERSARIAL_PROMPT = """You are an independent ADVERSARIAL ANALYST. Your job is to actively find reasons the proposed prediction could be wrong — NOT to continue the previous model's reasoning.

You receive Kai's statistical prediction, Qwen's screening analysis, and the normalized match/market data. Independently calculate your own probability and challenge every assumption.

Specifically search for:
- false confidence, misleading recent form, lineup problems, rotation, schedule traps,
- market traps, odds distortion, contradictory statistics, tactical mismatch,
- motivation problems, hidden correlation, weak assumptions in Qwen's analysis.

Do NOT simply agree. Set agreement_with_qwen / agreement_with_kai honestly. Set needs_k3=true when you find material disagreement, a high-value/high-risk situation, or when the edge does not survive scrutiny.
""" + _json_block(
    '{"prediction": {"market": str, "selection": str, "probability": float}, '
    '"market_probability": float, "estimated_edge": float, "confidence": float, '
    '"risk_score": float, "agreement_with_qwen": bool, "agreement_with_kai": bool, '
    '"contradictions": [str], "red_flags": [str], "needs_k3": bool}'
)


K3_ADJUDICATION_PROMPT = """You are the PREMIUM ADJUDICATOR. You make a final, calibrated recommendation on a candidate that has already been screened (Qwen) and adversarially challenged (DeepSeek).

You receive the Kai statistical model, Qwen analysis, DeepSeek analysis, disagreement summary, and full match/market data.

Before concluding, explicitly identify the strongest argument AGAINST the wager. Then determine:
- the most likely outcome and probability,
- market implied probability and estimated edge,
- confidence and risk,
- whether the edge survives contradictory evidence,
- whether the price is still acceptable,
- a final decision: BET or PASS.

You must NOT determine bankroll allocation, must NOT override Kai's risk engine, and must NOT publish. final_decision and final_recommendation are each exactly "BET" or "PASS".
""" + _json_block(
    '{"final_decision": "BET|PASS", "market": str, "selection": str, '
    '"probability": float, "market_probability": float, "estimated_edge": float, '
    '"confidence": float, "risk_score": float, '
    '"model_consensus": {"kai": float, "qwen": float, "deepseek": float, "k3": float}, '
    '"strongest_argument_for": str, "strongest_argument_against": str, '
    '"key_reasons": [str], "risk_flags": [str], "contradictions": [str], '
    '"final_recommendation": "BET|PASS"}'
)
