"""Covers the GPU.ai inference layer: client, budget, cache, and router."""

from unittest.mock import patch, MagicMock
import json

import pytest

from core.kai_betting.ai.client import (
    GPUAIClient, GPUAIUnavailableError, GPUAIInferenceError, GPUAIResponse,
    estimate_cost,
)
from core.kai_betting.ai.budget import BettingAIBudgetController
from core.kai_betting.ai.cache import InferenceCache, data_hash
from core.kai_betting.ai.router import BettingAIRouter, AIRoutingResult
from core.kai_betting.ai.prompts import MODELS, PROMPT_VERSION


# ── Client ────────────────────────────────────────────────────────────────────

def _fake_response(content: dict, usage: dict = None):
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {
        "choices": [{"message": {"content": json.dumps(content)}}],
        "usage": usage or {"prompt_tokens": 10, "completion_tokens": 20},
    }
    return resp


def test_client_requires_api_key():
    client = GPUAIClient(api_key="")
    assert not client.configured
    with pytest.raises(GPUAIUnavailableError):
        client.chat_json("qwen", "system", {"a": 1})


def test_client_extracts_json_and_cost():
    client = GPUAIClient(api_key="test-key")
    with patch("core.kai_betting.ai.client.requests.post", return_value=_fake_response({"probability": 0.6})) as post:
        out = client.chat_json("qwen", "system", {"a": 1})
    assert out.data == {"probability": 0.6}
    assert out.input_tokens == 10 and out.output_tokens == 20
    assert out.estimated_cost == pytest.approx(estimate_cost("qwen", 10, 20))
    # model id passed dynamically
    body = post.call_args.kwargs["json"]
    assert body["model"] == MODELS["qwen"]["model_id"]


def test_client_strips_markdown_fence():
    client = GPUAIClient(api_key="test-key")
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {
        "choices": [{"message": {"content": "```json\n{\"x\": 1}\n```"}}],
        "usage": {},
    }
    with patch("core.kai_betting.ai.client.requests.post", return_value=resp):
        out = client.chat_json("deepseek", "system", {})
    assert out.data == {"x": 1}


def test_client_retries_then_raises():
    client = GPUAIClient(api_key="test-key")
    resp = MagicMock()
    resp.status_code = 500
    with patch("core.kai_betting.ai.client.requests.post", return_value=resp), \
         patch("core.kai_betting.ai.client.time.sleep"):
        with pytest.raises(GPUAIInferenceError):
            client.chat_json("qwen", "system", {})


# ── Budget ───────────────────────────────────────────────────────────────────

def test_budget_limits(fresh_db):
    import core.kai_betting.db as db_mod
    from core.kai_betting.db import get_db
    with get_db() as conn:
        budget = BettingAIBudgetController(conn, daily=0.10, weekly=0, monthly=0)
        assert not budget.over_budget()
        budget.record("qwen", 0.06, 100, 100, 500, "req-1")
        assert not budget.over_budget()
        budget.record("qwen", 0.06, 100, 100, 500, "req-2")
        assert budget.over_budget()
        assert budget.spend_today() == pytest.approx(0.12)


# ── Cache ────────────────────────────────────────────────────────────────────

def test_data_hash_deterministic_and_order_independent():
    a = data_hash({"x": 1, "y": [1, 2]})
    b = data_hash({"y": [1, 2], "x": 1})
    assert a == b
    assert data_hash({"x": 2}) != a


def test_cache_ttl():
    c = InferenceCache(default_ttl_seconds=1)
    c.set("k", "v")
    assert c.get("k") == "v"
    c.clear()
    assert c.get("k") is None


# ── Router ───────────────────────────────────────────────────────────────────

class _FakeClient:
    def __init__(self, responses=None):
        self.responses = responses or {}
        self.configured = True
        self.calls = []

    def chat_json(self, model_key, system_prompt, user_payload, **kw):
        self.calls.append(model_key)
        data = self.responses[model_key]
        return GPUAIResponse(data, model_key, 1, 1, 5, "req")


def _prediction(**kw):
    from types import SimpleNamespace
    defaults = dict(sport_key="football", market_type="match_result", selection="home",
                    bookmaker_odds=2.0, estimated_probability=0.55,
                    implied_probability=0.5, edge=0.05, confidence=60.0,
                    risk_score=20.0, data_quality=80.0, league_key="epl",
                    source_prediction_id="p1")
    defaults.update(kw)
    return SimpleNamespace(**defaults)


def test_router_not_configured():
    client = _FakeClient()
    client.configured = False
    router = BettingAIRouter(client=client)
    result = router.analyze(_prediction())
    assert result.status == "not_configured"
    assert client.calls == []


def test_router_data_insufficient():
    router = BettingAIRouter(client=_FakeClient())
    result = router.analyze(_prediction(bookmaker_odds=None))
    assert result.status == "data_insufficient"


def test_router_qwen_only_no_escalation():
    client = _FakeClient({"qwen": {"prediction": {"probability": 0.6},
                                   "confidence": 50.0, "deep_review": False}})
    router = BettingAIRouter(client=client)
    result = router.analyze(_prediction(edge=0.01))
    assert result.status == "qwen_only"
    assert result.tiers_run == ["qwen"]
    assert result.final_probability == pytest.approx(0.6)


def test_router_escalates_to_deepseek():
    client = _FakeClient({
        "qwen": {"prediction": {"probability": 0.6}, "confidence": 80.0, "deep_review": True},
        "deepseek": {"prediction": {"probability": 0.52}, "needs_k3": False},
    })
    router = BettingAIRouter(client=client)
    result = router.analyze(_prediction(edge=0.08))
    assert result.tiers_run == ["qwen", "deepseek"]
    assert result.final_probability == pytest.approx(0.52)


def test_router_escalates_to_k3_on_disagreement():
    client = _FakeClient({
        "qwen": {"prediction": {"probability": 0.68}, "confidence": 85.0, "deep_review": True},
        "deepseek": {"prediction": {"probability": 0.50}, "needs_k3": True},
        "k3": {"final_decision": "BET", "final_recommendation": "BET",
               "probability": 0.55, "strongest_argument_against": "rotation risk"},
    })
    router = BettingAIRouter(client=client)
    result = router.analyze(_prediction(edge=0.10))
    assert result.tiers_run == ["qwen", "deepseek", "k3"]
    assert result.final_decision == "BET"
    assert result.status == "complete"


def test_router_qwen_failure_falls_back():
    client = _FakeClient({})
    client.chat_json = MagicMock(side_effect=GPUAIInferenceError("boom"))
    router = BettingAIRouter(client=client)
    result = router.analyze(_prediction())
    assert result.status == "fallback_statistical"
