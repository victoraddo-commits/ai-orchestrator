"""Scoring engine for free coding models.

Calculates coding capability scores and overall ranking scores based on:
- Multi-stage validation results
- Reliability metrics
- Latency metrics
- Real-world usage data
"""

import json
from datetime import datetime
from typing import Optional

from . import SCORING_WEIGHTS, MIN_CODING_SCORE
from .models import db


# Scoring rubric for coding capability (0-10)
CODING_RUBRIC = {
    "code_generation": {
        "weight": 0.15,
        "description": "Ability to generate correct, idiomatic code"
    },
    "debugging": {
        "weight": 0.15,
        "description": "Ability to identify and fix bugs"
    },
    "repository_understanding": {
        "weight": 0.10,
        "description": "Understanding of multi-file project structure"
    },
    "software_architecture": {
        "weight": 0.10,
        "description": "Design of production-grade systems"
    },
    "terminal_cli_work": {
        "weight": 0.05,
        "description": "Terminal and CLI operations"
    },
    "tool_calling": {
        "weight": 0.10,
        "description": "Function/tool calling capabilities"
    },
    "agentic_coding": {
        "weight": 0.15,
        "description": "Multi-step autonomous task completion"
    },
    "multi_step_tasks": {
        "weight": 0.05,
        "description": "Completion of complex multi-step tasks"
    },
    "test_generation": {
        "weight": 0.05,
        "description": "Writing effective tests"
    },
    "test_failure_correction": {
        "weight": 0.05,
        "description": "Diagnosing and fixing failing tests"
    },
    "long_context_coding": {
        "weight": 0.05,
        "description": "Handling large codebases and contexts"
    },
    "instruction_following": {
        "weight": 0.05,
        "description": "Following instructions precisely"
    },
    "code_reliability": {
        "weight": 0.05,
        "description": "Consistently producing working code"
    }
}


def calculate_coding_score(validation_results: dict) -> float:
    """Calculate coding capability score (0-10) from validation results.

    Based on which tests passed and their difficulty.
    """
    tests = validation_results.get("tests", {})
    passed_count = sum(1 for t in tests.values() if t.get("passed", False))
    total_tests = len(tests) or 1

    # Base score from test pass rate
    base_score = (passed_count / total_tests) * 10

    # Bonus for harder tests passing
    bonus = 0.0

    # TEST_B_DEBUGGING - hard
    if tests.get("TEST_B_DEBUGGING", {}).get("passed"):
        bonus += 0.5

    # TEST_G_SELF_CORRECTION - hard
    if tests.get("TEST_G_SELF_CORRECTION", {}).get("passed"):
        bonus += 0.5

    # TEST_H_ARCHITECTURE - hard
    if tests.get("TEST_H_ARCHITECTURE", {}).get("passed"):
        bonus += 0.5

    # TEST_D_TDD - medium hard
    if tests.get("TEST_D_TDD", {}).get("passed"):
        bonus += 0.25

    # TEST_F_LONG_CONTEXT - medium
    if tests.get("TEST_F_LONG_CONTEXT", {}).get("passed"):
        bonus += 0.25

    final_score = min(10.0, base_score + bonus)

    return round(final_score, 2)


def calculate_overall_score(model_data: dict) -> float:
    """Calculate overall ranking score using weighted factors.

    Weights:
    - coding_capability: 25%
    - agentic_coding: 20%
    - reasoning: 15%
    - tool_calling: 10%
    - reliability: 15%
    - latency: 5%
    - context: 5%
    - output_quality: 5%
    """
    weights = SCORING_WEIGHTS

    # Coding capability (from validation or model data)
    coding_capability = model_data.get("coding_score", 5.0)

    # Agentic coding (proxy: debugging + self-correction tests)
    agentic_coding = min(10.0, (
        (1 if model_data.get("status") == "ACTIVE" else 0) * 2 +
        model_data.get("success_rate", 0) / 10
    ))

    # Reasoning (proxy: architecture + long context tests)
    reasoning = 5.0  # Default

    # Tool calling (from model metadata or inference)
    tool_calling = 5.0  # Default

    # Reliability (success rate)
    reliability = model_data.get("success_rate", 0)

    # Latency (inverse scoring - lower is better)
    p95_latency = model_data.get("p95_latency", 0)
    if p95_latency > 0:
        # Score decreases as latency increases
        # < 5s = 10, 5-10s = 7.5, 10-20s = 5, > 20s = 2.5
        latency_score = max(0, 10 - (p95_latency / 2000))
    else:
        latency_score = 5.0  # Unknown

    # Context (context window)
    context_length = model_data.get("context_length", 0)
    if context_length >= 200000:
        context_score = 10.0
    elif context_length >= 100000:
        context_score = 8.0
    elif context_length >= 32000:
        context_score = 6.0
    elif context_length >= 8000:
        context_score = 4.0
    else:
        context_score = 2.0

    # Output quality (inverse of error rates)
    total_requests = model_data.get("requests", 0)
    if total_requests > 10:
        invalid_rate = (model_data.get("invalid_responses", 0) + model_data.get("empty_responses", 0)) / total_requests
        output_quality = max(0, 10 - (invalid_rate * 100))
    else:
        output_quality = 5.0  # Not enough data

    # Calculate weighted sum
    overall = (
        weights["coding_capability"] * coding_capability +
        weights["agentic_coding"] * agentic_coding +
        weights["reasoning"] * reasoning +
        weights["tool_calling"] * tool_calling +
        weights["reliability"] * reliability +
        weights["latency"] * latency_score +
        weights["context"] * context_score +
        weights["output_quality"] * output_quality
    )

    return round(overall, 2)


def score_model(model_id: str, validation_results: Optional[dict] = None) -> dict:
    """Score a model and update its record.

    Returns: dict with scores and breakdown
    """
    model = db.get_model(model_id)
    if not model:
        return {"error": "Model not found"}

    # Calculate coding score from validation results if available
    if validation_results:
        coding_score = calculate_coding_score(validation_results)
    else:
        coding_score = model.get("coding_score", 0.0)

    # Update database with coding score
    db.upsert_model(model_id, coding_score=coding_score)

    # Calculate overall score
    model_updated = db.get_model(model_id)
    overall_score = calculate_overall_score(model_updated)

    # Update database with overall score
    db.upsert_model(model_id, overall_score=overall_score)

    # Check if model qualifies for pool (coding score > 5.0)
    qualifies = coding_score > MIN_CODING_SCORE

    if qualifies and model_updated.get("is_free") == 1:
        if model_updated.get("status") not in ("AVAILABLE", "ACTIVE"):
            db.update_status(model_id, "AVAILABLE")
    elif coding_score > 0:
        if model_updated.get("status") not in ("REJECTED",):
            db.update_status(model_id, "REJECTED", f"Coding score {coding_score} below minimum {MIN_CODING_SCORE}")

    return {
        "model_id": model_id,
        "coding_score": coding_score,
        "overall_score": overall_score,
        "qualifies_for_pool": qualifies,
        "breakdown": {
            "coding_capability": coding_score,
            "reliability": model_updated.get("success_rate", 0),
            "latency": model_updated.get("p95_latency", 0),
            "context": model_updated.get("context_length", 0),
        }
    }


def rank_models(models: list[dict]) -> list[dict]:
    """Rank models by overall score.

    Returns sorted list with rank field added.
    """
    ranked = sorted(models, key=lambda m: m.get("overall_score", 0), reverse=True)

    for i, model in enumerate(ranked):
        model["rank"] = i + 1

    return ranked


def get_pool_ranking() -> dict:
    """Get the current model pool ranked by overall score.

    Returns pool structure with PRIMARY, SECONDARY, etc.
    """
    verified_models = db.get_verified_free_models()

    # Filter to only AVAILABLE or ACTIVE models
    eligible = [m for m in verified_models if m.get("status") in ("AVAILABLE", "ACTIVE")]

    # Rank by overall score
    ranked = rank_models(eligible)

    pool = {}
    pool_labels = ["PRIMARY", "SECONDARY", "TERTIARY", "QUATERNARY", "EMERGENCY"]

    for i, model in enumerate(ranked[:5]):
        label = pool_labels[i] if i < len(pool_labels) else f"BACKUP_{i+1}"
        pool[label] = {
            "model_id": model.get("model_id"),
            "coding_score": model.get("coding_score", 0),
            "overall_score": model.get("overall_score", 0),
            "success_rate": model.get("success_rate", 0),
            "p95_latency": model.get("p95_latency", 0),
            "context_length": model.get("context_length", 0),
        }

    return pool


def should_promote(new_model: dict, current_model: dict, improvement_threshold: float = 0.05) -> tuple[bool, str]:
    """Determine if a new model should be promoted over the current model.

    Returns: (should_promote, reason)
    """
    new_score = new_model.get("overall_score", 0)
    current_score = current_model.get("overall_score", 0)

    # Must have sufficient improvement
    improvement = (new_score - current_score) / current_score if current_score > 0 else 0

    if improvement >= improvement_threshold:
        # Check coding score improvement too
        new_coding = new_model.get("coding_score", 0)
        current_coding = current_model.get("coding_score", 0)

        coding_improvement = (new_coding - current_coding) / current_coding if current_coding > 0 else 0

        if coding_improvement >= improvement_threshold or new_coding > current_coding:
            return True, f"New model {new_model['model_id']} outperforms {current_model['model_id']}: {new_score:.2f} vs {current_score:.2f} ({improvement:.1%} improvement)"

    return False, f"Insufficient improvement: {new_score:.2f} vs {current_score:.2f} ({improvement:.1%} < {improvement_threshold:.1%})"
